"""TradingAgents Operável — serviço completo: API + painel de configuração e
operação embutidos. Uma senha (ACCESS_PASSWORD), um volume (/data), um deploy.

Config vive em /data/config.json (chmod 600), editada PELO PAINEL, aplicada a
cada job — sem redeploy, sem tocar em env no Coolify. Segredos nunca voltam
inteiros ao navegador (exibição mascarada)."""
import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from service.api import segredos_em_repouso
from service.api.ia_uso import RegistroDeUso
from service.api.owner_credentials import CredenciaisDoDono, erro_de_senha
from service.iq_option_read_only import (
    IqOptionReadOnlyError,
    probe_iq_option_read_only_account_async,
)
from tradingagents.agency import AgencyTask, build_llm_handler
from tradingagents.agency.store import AgencyTaskStore
from tradingagents.agency_runtime import AgencyRuntime
from tradingagents.brokers.ccxt_bridge import (
    BrokerSecurityError,
    probe_read_only_account,
    read_only_credential_fingerprint,
    testnet_credential_fingerprint,
)
from tradingagents.desk.models import AgentStage
from tradingagents.desk.modo import EstadoDaMesa, ModoBloqueado
from tradingagents.desk.storage import escrever_json_atomico
from tradingagents.domain import (
    MarketSnapshot,
    OrderRequest,
    Side,
    TradeDecision,
)
from tradingagents.execution import ExecutionRouter, KillSwitch, PaperAdapter, ShadowAdapter
from tradingagents.infra.supabase import resolve_database_url
from tradingagents.job_store import JobStore
from tradingagents.reconciliation_worker import ReconciliationMonitor
from tradingagents.risk import RiskEngine
from tradingagents.telemetry import AuditLog, EventBus, MetricsRegistry
from tradingagents.tenancy import AuthError, PostgresTenantStore, Principal, TenantStore
from tradingagents.workers import WorkerSupervisor

_data_dir = os.environ.get("DATA_DIR", "/data")
try:
    DATA = Path(_data_dir)
    DATA.mkdir(parents=True, exist_ok=True)
except PermissionError:
    DATA = Path("./data")
    DATA.mkdir(parents=True, exist_ok=True)

CFG_PATH = DATA / "config.json"
DB = DATA / "jobs.db"
LIVE_HALT_PATH = DATA / "live-halts.json"
HEALTH: dict = {"armazenamento": "?", "avisos": []}
LOGGER = logging.getLogger("tradingagents.service")


def _build_job_store():
    """Prefere DEGRADAR a morrer: um Postgres fora do ar não pode derrubar a
    página que o dono usa para consertar (causa do 502 real em produção)."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        try:
            store = JobStore(DB, dsn)
            HEALTH["armazenamento"] = "postgres"
            return store
        except Exception as exc:
            HEALTH["avisos"].append(
                "DATABASE_URL configurada mas o PostgreSQL não pôde ser usado "
                f"({type(exc).__name__}); usando o banco local do volume /data.")
    store = JobStore(DB, None)
    HEALTH["armazenamento"] = "sqlite" if not dsn else "sqlite (fallback)"
    return store


DATABASE_URL, DATABASE_ORIGEM = resolve_database_url()
if DATABASE_URL and not os.environ.get("DATABASE_URL"):
    # o resto do código (tenancy, assinaturas) lê do ambiente
    os.environ["DATABASE_URL"] = DATABASE_URL

JOB_STORE = _build_job_store()
PASSWORD = os.environ.get("ACCESS_PASSWORD", "")
# Credenciais do dono com hash Argon2id (owner_credentials.json no DATA_DIR).
# Quando o arquivo existe ele VENCE o ambiente: o segredo verificado está no
# disco como hash, nunca em texto puro — o env é só o caminho legado (dev).
OWNER_CREDENTIALS = CredenciaisDoDono(DATA / "owner_credentials.json")
MULTI_TENANT_REQUESTED = os.environ.get("MULTI_TENANT_ENABLED", "false").lower() == "true"
MULTI_TENANT_ENABLED = MULTI_TENANT_REQUESTED

# ── GATE G2 — APLICATIVO PESSOAL DE DONO ÚNICO (SINGLE-OWNER) ────────────────
# Este é um aplicativo PESSOAL de um único dono. Toda a superfície de SaaS
# (cadastro self-service, cobrança/Woovi, login social, multi-tenant e
# administração de inquilinos) continua existindo apenas como CÓDIGO LEGADO
# PRESERVADO — nenhum dado é apagado — porém é desligada de forma
# INCONDICIONAL aqui. A trava é FAIL-CLOSED por construção: é uma constante
# do código, NÃO uma flag de ambiente. Nenhuma variável (MULTI_TENANT_ENABLED,
# SIGNUP_ENABLED, WOOVI_*, TENANT_*) pode reabri-la. Ao forçar o multi-tenant
# a desligado AQUI, antes da construção de qualquer store, garantimos que a
# autenticação do processo inteiro use sempre a senha do dono (ACCESS_PASSWORD)
# e nunca o fluxo de inquilinos. A inalcançabilidade das rotas é reforçada,
# em profundidade, pelo middleware `bloqueio_single_owner` mais abaixo e
# provada por tests/test_single_owner_g2.py.
SINGLE_OWNER_ENFORCED = True
if SINGLE_OWNER_ENFORCED:
    MULTI_TENANT_REQUESTED = False
    MULTI_TENANT_ENABLED = False

_TENANT_SIGNING_KEY = os.environ.get("TENANT_TOKEN_SIGNING_KEY")
TENANT_STORE = None
if MULTI_TENANT_ENABLED:
    # Configuração errada NÃO pode matar o contêiner: o dono ficaria com um
    # 502 mudo, sem página para ler o que fazer. Desliga o multi-tenant,
    # mantém o serviço de pé e diz exatamente como corrigir.
    try:
        if not _TENANT_SIGNING_KEY:
            raise ValueError("TENANT_TOKEN_SIGNING_KEY é obrigatório")
        if os.environ.get("DATABASE_URL"):
            TENANT_STORE = PostgresTenantStore(os.environ["DATABASE_URL"],
                                               signing_key=_TENANT_SIGNING_KEY)
        else:
            TENANT_STORE = TenantStore(DATA / "identity.db",
                                       signing_key=_TENANT_SIGNING_KEY)
    except Exception as _exc:
        MULTI_TENANT_ENABLED = False
        HEALTH["avisos"].append(
            f"multi-tenant desligado: {_exc}. Defina TENANT_TOKEN_SIGNING_KEY "
            "com 32+ caracteres aleatórios e reinicie para habilitar contas.")
# NOTA DE MERGE: a base (Qoder) definia aqui `SINGLE_OWNER_ENFORCED =
# not MULTI_TENANT_ENABLED` — política flag-reabrível. Por DECISÃO ARQUITETURAL
# (dono único permanente), essa redefinição foi removida: a trava fixa definida
# acima é a única autoridade, e nenhuma flag reabre a superfície SaaS.
TENANT_BOOTSTRAP_TOKEN = os.environ.get("TENANT_BOOTSTRAP_TOKEN", "")
STATIC = Path(__file__).parent / "static"
AGENCY_AUDIT = AuditLog(DATA / "agency-events.jsonl")
AGENCY_EVENT_BUS = EventBus(AGENCY_AUDIT)
AGENCY = AgencyRuntime(audit_log=AGENCY_AUDIT, event_bus=AGENCY_EVENT_BUS,
                       store=AgencyTaskStore(DATA / "agency.db"))
AUDIT_LOG = AuditLog(DATA / "execution-events.jsonl")
EVENT_BUS = EventBus(AUDIT_LOG)
PAPER_ADAPTER = PaperAdapter(state_path=DATA / "paper-state.json")
SHADOW_ADAPTER = ShadowAdapter(state_path=DATA / "shadow-state.json")
KILL_SWITCH = KillSwitch(DATA / "kill-switch.json")
METRICS = MetricsRegistry()
# V8 §6: consumo de IA por inquilino (chamadas, tokens, custo estimado).
# Informativo — jamais desativa verificação de contexto nem gate algum.
IA_USO = RegistroDeUso(DATA / "ia-uso.json")


def remote_reconciliation_snapshot() -> dict:
    raise RuntimeError("provider remoto de reconciliação não configurado")


PAPER_RECONCILIATION = ReconciliationMonitor(PAPER_ADAPTER.reconciliation_snapshot, remote_reconciliation_snapshot)
EXECUTION_ROUTER = ExecutionRouter(risk_engine=RiskEngine(), audit_log=AUDIT_LOG, event_bus=EVENT_BUS, kill_switch=KILL_SWITCH, metrics=METRICS, reconciliation_guard=lambda: bool(PAPER_RECONCILIATION.last_report and PAPER_RECONCILIATION.last_report.is_safe))
SUPERVISOR = WorkerSupervisor()
SUPERVISOR.add("paper-reconciliation", PAPER_RECONCILIATION.run_once, interval_seconds=60)
for _agent_id, _desk, _role in (
    ("market-scanner", "research", "Market Scanner"),
    ("technical-analyst", "research", "Technical Analyst"),
    ("sentiment-analyst", "research", "Sentiment Analyst"),
    ("fundamental-analyst", "research", "Fundamental Analyst"),
    ("macro-analyst", "research", "Macro Analyst"),
    ("quant-researcher", "research", "Quant Researcher"),
    ("alternative-data-agent", "research", "Alternative Data Analyst"),
    ("strategy-agent", "strategy", "Strategy Researcher"),
    ("risk-advisor", "risk", "Risk Advisor"),
    ("portfolio-manager", "portfolio", "Portfolio Manager"),
    ("execution-planner", "execution", "Execution Planner"),
    ("reconciliation-agent", "monitoring", "Reconciliation"),
    ("orderbook-analyst", "execution", "Order Book Analyst"),
    ("trading-compliance-agent", "risk", "Trading Compliance"),
    ("trade-journal-agent", "monitoring", "Trade Journal"),
    ("research-agent", "research", "Business Research"),
    ("news-agent", "intelligence", "News Intelligence"),
    ("operations-agent", "operations", "Operations Manager"),
    ("content-agent", "content", "Content Studio"),
    ("finance-agent", "finance", "Financial Analyst"),
    ("crm-agent", "crm", "CRM Manager"),
    ("market-intelligence-agent", "intelligence", "Market Intelligence"),
):
    AGENCY.register_agent(_agent_id, _desk, _role)

ALLOWED_KEYS = [
    "TRADINGAGENTS_LLM_PROVIDER", "TRADINGAGENTS_BACKEND_URL", "TRADINGAGENTS_DEEP_THINK_LLM",
    "TRADINGAGENTS_QUICK_THINK_LLM", "TRADINGAGENTS_OUTPUT_LANGUAGE",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS", "TRADINGAGENTS_MAX_RISK_ROUNDS",
    "TRADINGAGENTS_LLM_FALLBACK", "TRADINGAGENTS_IA_ORCAMENTO_USD",
    "TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY", "OPENAI_COMPATIBLE_API_KEY",
    "FRED_API_KEY", "ALPHA_VANTAGE_API_KEY",
    "TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE",
    "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "EXCHANGE_API_PASSWORD",
    "EXCHANGE_API_UID", "EXCHANGE_CREDENTIAL_EXCHANGE",
    "EXCHANGE_CREDENTIAL_BOUND_AT",
    "REAL_READ_ONLY_API_KEY", "REAL_READ_ONLY_API_SECRET",
    "REAL_READ_ONLY_API_PASSWORD", "REAL_READ_ONLY_API_UID",
    "REAL_READ_ONLY_EXCHANGE",
    "IQ_OPTION_MCP_BEARER_TOKEN",
    "TRADINGAGENTS_DESK_FEED", "TRADINGAGENTS_DESK_BROKER",
    "TRADINGAGENTS_DESK_INSTRUMENTS", "TRADINGAGENTS_DESK_CAPITAL",
    "TRADINGAGENTS_DESK_CONTROLE", "TRADINGAGENTS_DESK_TETO_EXPOSICAO",
    "LIVE_MAX_ORDER_VALUE", "LIVE_MAX_DAILY_LOSS",
    "LIVE_TRADING_CONFIRM",
    "ALERTA_TELEGRAM_TOKEN", "ALERTA_TELEGRAM_CHAT", "ALERTA_WEBHOOK_URL",
    "ALERTA_TELEGRAM_WEBHOOK_SECRET", "ALERTA_DISCORD_WEBHOOK",
    "ALERTA_WHATSAPP_TOKEN", "ALERTA_WHATSAPP_PHONE", "ALERTA_WHATSAPP_DESTINO",
    "ALERTA_WINDOWS", "ALERTA_WEBPUSH", "ALERTA_VAPID_PUBLICA",
    "ALERTA_VAPID_PRIVADA", "PUSH_ASSINATURAS",
    "EMAIL_SMTP_HOST", "EMAIL_SMTP_PORT", "EMAIL_SMTP_USER", "EMAIL_SMTP_PASS",
    "EMAIL_SMTP_FROM", "EMAIL_SMTP_TO",
    "TRANSPARENCIA_PUBLICA", "TRANSPARENCIA_NOME",
]
# Segredo é declarado, NÃO inferido pelo sufixo do nome. A heurística
# anterior (_KEY/_SECRET) deixava o token do Telegram e a URL de webhook —
# que costuma embutir credencial — voltarem em TEXTO CLARO pela API para
# qualquer usuário com permissão de leitura.
SEGREDOS_DECLARADOS = {
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GOOGLE_API_KEY",
    "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY", "FRED_API_KEY", "ALPHA_VANTAGE_API_KEY",
    "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "EXCHANGE_API_PASSWORD",
    "EXCHANGE_API_UID",
    "REAL_READ_ONLY_API_KEY", "REAL_READ_ONLY_API_SECRET",
    "REAL_READ_ONLY_API_PASSWORD", "REAL_READ_ONLY_API_UID",
    "IQ_OPTION_MCP_BEARER_TOKEN",
    "ALERTA_TELEGRAM_TOKEN", "ALERTA_TELEGRAM_CHAT", "ALERTA_WEBHOOK_URL",
    "ALERTA_TELEGRAM_WEBHOOK_SECRET", "ALERTA_DISCORD_WEBHOOK",
    "ALERTA_WHATSAPP_TOKEN", "EMAIL_SMTP_PASS",
    # Chave privada VAPID e assinaturas push (endpoint carrega capacidade):
    # passam pela cifragem em repouso do SEC-1 como qualquer segredo.
    "ALERTA_VAPID_PRIVADA", "PUSH_ASSINATURAS",
    "LIVE_TRADING_CONFIRM",
}
# Rede de segurança: qualquer chave futura com cara de credencial também
# entra, mesmo que alguém esqueça de declará-la.
SECRETish = tuple(sorted(
    {k for k in ALLOWED_KEYS
     if k in SEGREDOS_DECLARADOS
     or k.endswith(("_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_WEBHOOK_URL"))}))

# Liberação de CÓDIGO para a mesa operar dinheiro real (auditoria FIN-1,
# ADR-006). Só muda por decisão explícita do dono em código auditado — o
# painel NÃO concede: `build_desk_broker` recusa LIVE sem autorização e o
# checklist abaixo diz isso ao dono com todas as letras.
MESA_LIVE_LIBERADA = False

# Padrão EFETIVO do papel de cada chave que reconstrói a mesa. Instalação
# nova não tem nada salvo; o painel reenvia todos os campos no primeiro
# Salvar, incluindo os defaults dos seletores (fixture/binance/testnet).
# Salvar o padrão não é mudança — senão o primeiro clique derruba a mesa.
_DESK_EFFECTIVE_DEFAULTS = {
    "TRADINGAGENTS_DESK_BROKER": "paper",
    "TRADINGAGENTS_DESK_FEED": "fixture",
    "TRADINGAGENTS_EXCHANGE": "binance",
    "TRADINGAGENTS_BROKER_MODE": "testnet",
}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
bearer = HTTPBearer(auto_error=False)

_REQUEST_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def _request_id(request: Request) -> str:
    """Aceita só UUID canônico; nunca ecoa texto livre ou token disfarçado."""
    supplied = request.headers.get("X-Request-ID", "")
    return supplied if _REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())


def _route_label(request: Request) -> str:
    """Template de rota de baixa cardinalidade, nunca o caminho fornecido."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template.isascii() and 1 <= len(template) <= 128:
        return template
    return "unmatched"


@app.middleware("http")
async def observabilidade_http(request: Request, call_next):
    request_id = _request_id(request)
    request.state.request_id = request_id
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        labels = {
            "method": request.method[:16] if request.method.isascii() else "OTHER",
            "route": _route_label(request),
            "status_class": f"{status_code // 100}xx",
        }
        METRICS.inc("http.requests", labels=labels)
        METRICS.observe("http.request.duration_ms", duration_ms, labels=labels)
        # JSON de uma linha facilita correlação sem registrar query, headers,
        # corpo, IP, conta, ativo financeiro ou qualquer credencial.
        LOGGER.info(
            json.dumps(
                {
                    "event": "http_request_completed",
                    "request_id": request_id,
                    "method": labels["method"],
                    "route": labels["route"],
                    "status": status_code,
                    "duration_ms": round(duration_ms, 3),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )

# ── GATE G2 — BLOQUEIO INCONDICIONAL DA SUPERFÍCIE SaaS ─────────────────────
# Prefixos de rota que NÃO existem num aplicativo pessoal de dono único. O
# casamento é por SEGMENTO (prefixo exato ou seguido de '/'), para jamais
# atingir vizinhos legítimos — ex.: `/v1/admin/database` (inspeção do próprio
# banco pelo dono) NÃO é afetado por `/v1/admin/tenants`.
SAAS_BLOCKED_PREFIXES = (
    "/v1/auth/signup",         # cadastro self-service
    "/v1/auth/signup-status",  # vitrine do cadastro (sufixo '-status', não '/')
    "/v1/auth/login",       # login multi-tenant (o dono usa ACCESS_PASSWORD)
    "/v1/auth/bootstrap",   # bootstrap inicial de inquilino
    "/v1/auth/oauth",       # login social (Google/Microsoft)
    "/v1/admin/tenants",    # criação/gestão de inquilinos
    "/v1/admin/users",      # criação/gestão de usuários de inquilino
    "/v1/billing",          # planos, status de assinatura, checkout PIX
    "/v1/webhooks/woovi",   # webhook de pagamento que ativaria plano
)


def rota_saas_bloqueada(path: str) -> bool:
    """True se `path` pertence à superfície SaaS desligada no modo dono único."""
    return any(path == pref or path.startswith(pref + "/")
               for pref in SAAS_BLOCKED_PREFIXES)


@app.middleware("http")
async def bloqueio_single_owner(request: Request, call_next):
    """Fecha a superfície SaaS ANTES da autenticação e ANTES do roteamento,
    para QUALQUER método HTTP e autenticado ou não. Responde 404: no produto
    pessoal esses recursos simplesmente não existem. Incondicional: não lê
    nenhuma flag de ambiente, então configuração incorreta não o reabre."""
    if SINGLE_OWNER_ENFORCED and rota_saas_bloqueada(request.url.path):
        return JSONResponse(
            {"detail": "recurso indisponível neste aplicativo pessoal "
                       "(dono único); superfície SaaS desativada"},
            status_code=404,
        )
    return await call_next(request)


@app.on_event("startup")
async def start_workers():
    SUPERVISOR.start()


@app.on_event("shutdown")
async def stop_workers():
    SUPERVISOR.stop()

# Trava de força-bruta: com o código público, qualquer um sabe que UMA senha
# guarda tudo — então errar custa caro. 5 erros por IP => 15 min de espera.
_FAILS: dict = {}
MAX_FAILS, LOCK_SECONDS = 5, 900

def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "?"
    trusted = []
    for item in os.environ.get("TRUSTED_PROXY_NETWORKS", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            trusted.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            LOGGER.warning("ignoring invalid TRUSTED_PROXY_NETWORKS entry")
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        peer_ip = None
    proxy_trusted = peer_ip is not None and any(peer_ip in network for network in trusted)
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd and proxy_trusted else None) or peer

def _tenant_permission(request: Request) -> str:
    if request.url.path.startswith("/v1/admin"):
        return "admin"
    if request.method in {"GET", "HEAD"}:
        return "read"
    if request.url.path.startswith("/v1/execution/kill-switch"):
        return "kill_switch"
    if request.url.path.startswith("/v1/execution/paper"):
        return "paper"
    if request.url.path.startswith("/v1/execution/shadow"):
        return "shadow"
    if request.url.path.startswith("/v1/config"):
        return "admin"
    if request.url.path.startswith("/v1/agency"):
        path = request.url.path
        if "/worker" in path and request.method != "GET":
            return "agency_run"
        if path.endswith("/approve"):
            return "agency_approve"
        if path.endswith("/run"):
            return "agency_run"
        if "/agents/" in path and request.method != "GET":
            return "agent_admin"
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            return "agency_submit"
        return "analysis"
    return "analysis"


def auth(request: Request, c: HTTPAuthorizationCredentials = Depends(bearer)):
    if MULTI_TENANT_REQUESTED and not MULTI_TENANT_ENABLED:
        raise HTTPException(503, "multi-tenant solicitado, mas indisponível; corrija a configuração")
    if MULTI_TENANT_ENABLED:
        if not TENANT_STORE or not c:
            raise HTTPException(401, "sessão multi-tenant obrigatória")
        try:
            principal = TENANT_STORE.authenticate(c.credentials)
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from None
        permission = _tenant_permission(request)
        if not principal.can(permission):
            raise HTTPException(403, f"papel {principal.role} não possui a permissão {permission}")
        request.state.principal = principal
        return principal
    # Requisição anônima (ex.: página abrindo antes do login) NÃO conta como
    # tentativa de senha; só credencial ERRADA alimenta a trava de força-bruta.
    if not c or not c.credentials:
        raise HTTPException(401, "não autenticado")
    ip = _client_ip(request)
    rec = _FAILS.get(ip)
    if rec and rec["n"] >= MAX_FAILS:
        wait = int(rec["until"] - time.time())
        if wait > 0:
            raise HTTPException(429, f"muitas tentativas erradas — espera {wait // 60 + 1} min")
        _FAILS.pop(ip, None)
    if OWNER_CREDENTIALS.existe():
        ok = OWNER_CREDENTIALS.verificar_senha(c.credentials)
    elif PASSWORD:
        ok = hmac.compare_digest(c.credentials, PASSWORD)
    else:
        # Nada configurado ainda: não é senha errada, é primeira execução.
        # O painel usa este 409 para abrir o assistente de configuração.
        raise HTTPException(409, "primeira configuração pendente — crie a senha do dono no painel")
    if not ok:
        r = _FAILS.setdefault(ip, {"n": 0, "until": 0})
        r["n"] += 1
        r["until"] = time.time() + LOCK_SECONDS
        raise HTTPException(401, "não autenticado")
    _FAILS.pop(ip, None)
    return True


_shutdown_hook = None


def set_shutdown_hook(fn):
    """Registra como desligar o servidor (o launcher liga no uvicorn.Server)."""
    global _shutdown_hook
    _shutdown_hook = fn


@app.post("/v1/app/quit")
async def app_quit(_=Depends(auth)):
    if _shutdown_hook is None:
        raise HTTPException(409, "servidor iniciado fora do launcher; encerre pelo terminal")
    threading.Timer(0.3, _shutdown_hook).start()
    return {"ok": True, "message": "servidor encerrando"}


@app.post("/v1/auth/bootstrap")
async def tenant_bootstrap(req: Request):
    if not MULTI_TENANT_ENABLED or not TENANT_STORE or not TENANT_BOOTSTRAP_TOKEN:
        raise HTTPException(404, "bootstrap multi-tenant desabilitado")
    if TENANT_STORE.tenant_count() > 0:
        raise HTTPException(409, "bootstrap inicial já consumido; crie usuários/tenants pelo painel Admin")
    if not hmac.compare_digest(req.headers.get("X-Tenant-Bootstrap", ""), TENANT_BOOTSTRAP_TOKEN):
        raise HTTPException(401, "bootstrap não autorizado")
    body = await _json_body(req)
    try:
        tenant_id = TENANT_STORE.create_tenant(str(body["tenant_name"]))
        user_id = TENANT_STORE.create_user(str(body["email"]), str(body["password"]))
        TENANT_STORE.add_member(tenant_id, user_id, "admin")
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None
    return {"tenant_id": tenant_id, "user_id": user_id, "role": "admin"}


@app.post("/v1/auth/login")
async def tenant_login(req: Request):
    if not MULTI_TENANT_ENABLED or not TENANT_STORE:
        raise HTTPException(404, "login multi-tenant desabilitado")
    body = await _json_body(req)
    try:
        token = TENANT_STORE.login(str(body["email"]), str(body["password"]), str(body["tenant_id"]))
    except (KeyError, AuthError) as exc:
        raise HTTPException(401, str(exc)) from None
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600}


@app.get("/v1/auth/status")
def auth_status():
    return {
        "multi_tenant": MULTI_TENANT_ENABLED,
        "bootstrap_required": bool(MULTI_TENANT_ENABLED and TENANT_STORE and TENANT_STORE.tenant_count() == 0),
        "live_trading": False,
        # Primeira execução (nenhuma senha criada): o painel abre o assistente.
        # Presença FÍSICA do arquivo (mesmo ilegível) bloqueia o assistente —
        # storage existente não é instalação nova (V9-SEC-04).
        "setup_required": (not OWNER_CREDENTIALS.arquivo_presente()) and not PASSWORD,
    }


_SETUP_LOCK = threading.Lock()


@app.post("/v1/auth/setup")
async def owner_setup(req: Request):
    """Assistente de primeira execução: cria a senha do dono e devolve o
    código de recuperação UMA única vez. Só existe enquanto não há senha."""
    body = await _json_body(req)
    senha = str(body.get("senha", ""))
    erro = erro_de_senha(senha)
    if erro:
        raise HTTPException(422, erro)
    with _SETUP_LOCK:
        if PASSWORD or OWNER_CREDENTIALS.arquivo_presente():
            raise HTTPException(409, "senha do dono já configurada")
        codigo = OWNER_CREDENTIALS.criar(senha)
    return {"ok": True, "codigo_recuperacao": codigo}


@app.post("/v1/auth/recover")
async def owner_recover(req: Request):
    """Recuperação pelo código: troca a senha e devolve código NOVO (o antigo
    morre no uso). Mesma trava de força-bruta do login."""
    if not OWNER_CREDENTIALS.existe():
        raise HTTPException(409, "senha do dono ainda não configurada")
    body = await _json_body(req)
    ip = _client_ip(req)
    rec = _FAILS.get(ip)
    if rec and rec["n"] >= MAX_FAILS:
        wait = int(rec["until"] - time.time())
        if wait > 0:
            raise HTTPException(429, f"muitas tentativas erradas — espera {wait // 60 + 1} min")
        _FAILS.pop(ip, None)
    try:
        novo = OWNER_CREDENTIALS.usar_codigo_de_recuperacao(
            str(body.get("codigo", "")), str(body.get("nova_senha", "")))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if not novo:
        r = _FAILS.setdefault(ip, {"n": 0, "until": 0})
        r["n"] += 1
        r["until"] = time.time() + LOCK_SECONDS
        raise HTTPException(401, "código de recuperação incorreto")
    _FAILS.pop(ip, None)
    return {"ok": True, "codigo_recuperacao": novo}


@app.post("/v1/auth/recovery-code")
async def owner_new_recovery_code(_=Depends(auth)):
    """Código de recuperação novo para a sessão autenticada (ex.: dono migrado
    do formato antigo nunca viu um código, ou perdeu o papel anotado)."""
    novo = OWNER_CREDENTIALS.rotacionar_codigo()
    if not novo:
        raise HTTPException(409, "credenciais de arquivo não configuradas")
    return {"ok": True, "codigo_recuperacao": novo}


@app.get("/v1/auth/me")
def auth_me(principal=Depends(auth)):
    if isinstance(principal, Principal):
        return {"user_id": principal.user_id, "email": principal.email, "tenant_id": principal.tenant_id, "role": principal.role, "permissions": sorted(principal.permissions)}
    return {"user_id": "legacy", "email": "legacy", "tenant_id": "legacy", "role": "admin", "permissions": ["admin"]}


@app.post("/v1/auth/logout")
def tenant_logout(request: Request, c: HTTPAuthorizationCredentials = Depends(bearer), _=Depends(auth)):
    if MULTI_TENANT_ENABLED and TENANT_STORE and c:
        TENANT_STORE.revoke(c.credentials)
    return {"ok": True}


@app.post("/v1/admin/tenants")
async def create_tenant(req: Request, _=Depends(auth)):
    if not MULTI_TENANT_ENABLED or not TENANT_STORE:
        raise HTTPException(404, "multi-tenant desabilitado")
    body = await _json_body(req)
    try:
        tenant_id = TENANT_STORE.create_tenant(str(body["name"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None
    return {"tenant_id": tenant_id}


@app.post("/v1/admin/users")
async def create_tenant_user(req: Request, _=Depends(auth)):
    if not MULTI_TENANT_ENABLED or not TENANT_STORE:
        raise HTTPException(404, "multi-tenant desabilitado")
    body = await _json_body(req)
    try:
        user_id = TENANT_STORE.create_user(str(body["email"]), str(body["password"]))
        target_tenant = str(body.get("tenant_id", request_tenant(req)))
        if target_tenant != request_tenant(req):
            raise AuthError("administrador só pode gerenciar o próprio tenant")
        TENANT_STORE.add_member(target_tenant, user_id, str(body.get("role", "viewer")))
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None
    except AuthError as exc:
        raise HTTPException(403, str(exc)) from None
    return {"user_id": user_id, "tenant_id": target_tenant, "role": str(body.get("role", "viewer"))}


@app.get("/v1/admin/users")
def list_tenant_users(request: Request, _=Depends(auth)):
    if not MULTI_TENANT_ENABLED or not TENANT_STORE:
        return {"users": []}
    return {"users": TENANT_STORE.list_members(request_tenant(request))}


# NOTA DE MERGE: a base (Qoder) trazia aqui uma segunda `rota_saas_bloqueada`
# e um middleware `block_saas_routes` acoplados a `not MULTI_TENANT_ENABLED`
# (política flag-reabrível). Removidos por decisão arquitetural: o bloqueio é
# feito pela `rota_saas_bloqueada`/`bloqueio_single_owner` únicos lá de cima,
# de forma incondicional (nenhuma flag reabre).


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # O painel embute o Trading Floor em iframe same-origin (/floor). DENY
    # bloquearia a própria tela; SAMEORIGIN mantém a proteção anti-clickjacking
    # contra sites externos. Todo o resto segue DENY.
    resp.headers["X-Frame-Options"] = "SAMEORIGIN" if request.url.path == "/floor" else "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    # HTML do app muda a cada atualização: sem no-store o navegador pode
    # continuar rodando a tela antiga em cache, inclusive a versão que
    # pollava sem sessão e armava a trava de força-bruta do dono.
    if request.url.path in {"/app", "/floor"}:
        resp.headers["Cache-Control"] = "no-store"
    return resp

# Provedores gratuitos/locais de primeira classe: todos falam o protocolo
# OpenAI-compatível do núcleo, cada um com sua URL e sua chave própria
# (a credencial dedicada OPENAI_COMPATIBLE_API_KEY é herança do Gate A:
# um backend redirecionado jamais recebe a chave da OpenAI de verdade).
FREE_PROVIDERS = {
    # Padrões de fábrica do Groq verificados AO VIVO em 2026-10-14: os llama
    # foram descontinuados e respondiam 404 — a lista real da API serve os
    # gpt-oss. Sempre que o dono buscar modelos no painel, a descoberta real
    # manda (R3); estes nomes são só o último recurso honesto.
    "groq":       {"url": "https://api.groq.com/openai/v1", "key_from": "GROQ_API_KEY",
                   "deep": "openai/gpt-oss-120b", "quick": "openai/gpt-oss-20b"},
    "mistral":    {"url": "https://api.mistral.ai/v1", "key_from": "MISTRAL_API_KEY",
                   "deep": "mistral-large-latest", "quick": "mistral-small-latest"},
    "openrouter": {"url": "https://openrouter.ai/api/v1", "key_from": "OPENROUTER_API_KEY",
                   "deep": "meta-llama/llama-3.3-70b-instruct:free",
                   "quick": "meta-llama/llama-3.3-70b-instruct:free"},
    "ollama":     {"url": None, "key_from": None,
                   "deep": "llama3.1", "quick": "llama3.1"},
}


def resolve_llm(cfg: dict):
    """Traduz a escolha do painel em (config do núcleo, env). Pura e testável."""
    conf, env = {}, {}
    prov = (cfg.get("TRADINGAGENTS_LLM_PROVIDER") or "").strip().lower()
    deep = (cfg.get("TRADINGAGENTS_DEEP_THINK_LLM") or "").strip()
    quick = (cfg.get("TRADINGAGENTS_QUICK_THINK_LLM") or "").strip()
    url = (cfg.get("TRADINGAGENTS_BACKEND_URL") or "").strip()
    if prov in FREE_PROVIDERS:
        p = FREE_PROVIDERS[prov]
        conf["llm_provider"] = "ollama" if prov == "ollama" else "openai_compatible"
        conf["backend_url"] = url or p["url"]
        if prov == "ollama" and not conf["backend_url"]:
            raise ValueError("Ollama precisa do 'Endereço do servidor de IA' "
                             "(ex.: http://ip-do-teu-pc:11434/v1) — configura na aba ⚙️.")
        conf["deep_think_llm"] = deep or p["deep"]
        conf["quick_think_llm"] = quick or p["quick"]
        if p["key_from"]:
            key = cfg.get(p["key_from"]) or ""
            if not key:
                raise ValueError(f"Falta a chave {p['key_from']} — pega grátis e cola na aba ⚙️.")
            env["OPENAI_COMPATIBLE_API_KEY"] = key
    else:
        if prov:
            conf["llm_provider"] = prov
        if url:
            conf["backend_url"] = url
        if deep:
            conf["deep_think_llm"] = deep
        if quick:
            conf["quick_think_llm"] = quick
    return conf, env


_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _tenant_id_valido(tenant_id: str) -> bool:
    return bool(_TENANT_ID_RE.fullmatch(str(tenant_id)))


def tenant_cfg_path(tenant_id: str = "legacy") -> Path:
    if tenant_id == "legacy":
        return CFG_PATH
    if not _tenant_id_valido(tenant_id):
        raise ValueError("identificador de inquilino inválido")
    return DATA / "tenants" / tenant_id / "config.json"


# Segredo cifrado que não pôde ser decifrado NESTE contexto (outro usuário
# do Windows, outra máquina, blob corrompido). Fica registrado por inquilino
# para o painel contar a verdade — e o valor permanece no arquivo: perder
# credencial por falha de leitura é pior que o próprio risco.
_SEGREDOS_ILEGIVEIS: dict[str, dict[str, str]] = {}


def _segredos_do_disco(do_disco: dict, tenant_id: str, path: Path) -> None:
    """Decifra o que der, migra texto claro legado — sem perder nada.

    1) Valor cifrado: decifra em memória; ilegível fica registrado e INTACTO.
    2) Valor em texto claro no arquivo (config anterior à cifragem): com
       proteção disponível, reescreve o arquivo com os segredos cifrados —
       a verificação de ida-e-volta acontece ANTES de tocar o disco; falhou,
       o arquivo não é tocado e a migração é retentada no próximo uso.
       No POSIX não há o que migrar: o 0600 do arquivo é a proteção real.
    """
    registry = _SEGREDOS_ILEGIVEIS.setdefault(tenant_id, {})
    if segredos_em_repouso.tem_protecao():
        migrados = {}
        for chave in SECRETish:
            valor = do_disco.get(chave)
            if (isinstance(valor, str) and valor
                    and not segredos_em_repouso.e_cifrado(valor)):
                try:
                    migrados[chave] = segredos_em_repouso.cifrar(valor)
                except segredos_em_repouso.SegredoIlegivel:
                    migrados = {}
                    break        # verificação falhou: NÃO gravar nada
        if migrados:
            try:
                materializado = {**do_disco, **migrados}
                escrever_json_atomico(path, materializado)
                os.chmod(path, 0o600)
                do_disco.update(migrados)
                LOGGER.info("segredos em repouso migrados para cifrado (%s)",
                            tenant_id)
            except Exception as exc:
                # arquivo intocado: o texto claro continua legível e a
                # migração é retentada no próximo uso
                LOGGER.warning("migração de segredos falhou (%s): %s",
                               tenant_id, type(exc).__name__)
    for chave in SECRETish:
        valor = do_disco.get(chave)
        if not segredos_em_repouso.e_cifrado(valor):
            # Ausente ou legível em claro: o diagnóstico anterior ficou obsoleto.
            registry.pop(chave, None)
            continue
        try:
            do_disco[chave] = segredos_em_repouso.decifrar(valor)
            registry.pop(chave, None)
        except segredos_em_repouso.SegredoIlegivel as exc:
            registry[chave] = str(exc)   # valor fica como está no arquivo
            LOGGER.warning("segredo %s ilegível neste contexto", chave)


def load_cfg(tenant_id: str = "legacy") -> dict:
    # Em multi-tenant, variáveis de ambiente NÃO servem de configuração
    # padrão: senão a chave de IA do servidor (ou de um inquilino que mexeu
    # no processo) apareceria para todos. Só o modo de instalação única
    # ("legacy") herda do ambiente.
    if MULTI_TENANT_ENABLED and tenant_id != "legacy":
        cfg: dict = {}
    else:
        cfg = {key: os.environ[key] for key in ALLOWED_KEYS if os.environ.get(key)}
    path = tenant_cfg_path(tenant_id)
    if path.exists():
        with contextlib.suppress(ValueError):
            do_disco = json.loads(path.read_text())
            if isinstance(do_disco, dict):
                _segredos_do_disco(do_disco, tenant_id, path)
                cfg.update(do_disco)
    return cfg


_GENERAL_AGENT_IDS = (
    "research-agent", "news-agent", "operations-agent", "content-agent",
    "finance-agent", "crm-agent", "market-intelligence-agent",
)
_TRADING_AGENT_IDS = (
    "market-scanner", "technical-analyst", "sentiment-analyst", "fundamental-analyst",
    "macro-analyst", "quant-researcher", "alternative-data-agent", "strategy-agent",
    "risk-advisor", "portfolio-manager", "execution-planner", "orderbook-analyst",
    "trading-compliance-agent", "trade-journal-agent", "reconciliation-agent",
)
_AGENCY_LLM_AGENT_IDS = _GENERAL_AGENT_IDS + _TRADING_AGENT_IDS


class _LLMIndisponivelHandler:
    """Recusa execução quando não há LLM válido — nunca reusa chave removida."""

    def __init__(self, motivo: str):
        self.motivo = motivo

    def __call__(self, task):
        raise RuntimeError(f"LLM da agência indisponível — {self.motivo}")


def _agencia_llm_fail_closed(runtime, motivo: str) -> None:
    runtime.agency._agencia_llm_assinatura = None
    recusa = _LLMIndisponivelHandler(motivo)
    for agent_id in _AGENCY_LLM_AGENT_IDS:
        runtime.agency.executor.handlers.pop(agent_id, None)
        with contextlib.suppress(KeyError):
            runtime.agency.register_task_handler(agent_id, recusa)


def configure_agency_llm(runtime, tenant_id: str) -> str | None:
    """Configura handlers LLM por instância; retorna diagnóstico sem segredo."""
    try:
        cfg = load_cfg(tenant_id)
        conf, resolved_env = resolve_llm(cfg)
        provider = str(conf.get("llm_provider", "")).strip()
        model = str(conf.get("quick_think_llm") or conf.get("deep_think_llm") or "").strip()
        if not provider or not model:
            _agencia_llm_fail_closed(runtime, "modo de pensar não configurado")
            return "LLM não configurado para a agência"
        api_key = resolved_env.get("OPENAI_COMPATIBLE_API_KEY")
        if not api_key:
            for key_name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
                if cfg.get(key_name):
                    api_key = cfg[key_name]
                    break
        assinatura = (provider, model, str(conf.get("backend_url") or ""), api_key or "")
        handlers = runtime.agency.executor.handlers
        if (
            getattr(runtime.agency, "_agencia_llm_assinatura", None) == assinatura
            and all(agent_id in handlers for agent_id in _AGENCY_LLM_AGENT_IDS)
        ):
            return None
        role_by_agent = {
            "research-agent": "pesquisa empresarial",
            "news-agent": "inteligência de notícias",
            "operations-agent": "operações e processos",
            "content-agent": "estratégia e produção de conteúdo",
            "finance-agent": "análise financeira sem execução de pagamentos",
            "crm-agent": "gestão e qualificação de CRM",
            "market-intelligence-agent": "inteligência competitiva e de mercado",
            "market-scanner": "varredura de ativos e mercados",
            "technical-analyst": "análise técnica, sem envio de ordens",
            "sentiment-analyst": "sentimento de mercado e fontes públicas",
            "fundamental-analyst": "análise fundamentalista",
            "macro-analyst": "cenário macroeconômico",
            "quant-researcher": "pesquisa quantitativa e backtests",
            "alternative-data-agent": "dados alternativos e qualidade de fontes",
            "strategy-agent": "pesquisa de estratégia e hipóteses",
            "risk-advisor": "análise e bloqueio de risco, nunca aprovação própria",
            "portfolio-manager": "análise de alocação e exposição",
            "execution-planner": "planejamento Paper/Testnet, sem submissão",
            "orderbook-analyst": "microestrutura e liquidez",
            "trading-compliance-agent": "compliance e limites de mandato",
            "trade-journal-agent": "registro e reconciliação de operações",
            "reconciliation-agent": "reconciliação Paper/Testnet",
        }
        for agent_id in _AGENCY_LLM_AGENT_IDS:
            handlers.pop(agent_id, None)
        for agent_id in _AGENCY_LLM_AGENT_IDS:
            runtime.agency.register_task_handler(
                agent_id,
                build_llm_handler(
                    provider=provider,
                    model=model,
                    role=role_by_agent[agent_id],
                    base_url=conf.get("backend_url"),
                    api_key=api_key,
                ),
            )
        runtime.agency._agencia_llm_assinatura = assinatura
        return None
    except Exception as exc:
        LOGGER.warning("LLM da agência indisponível para tenant %s: %s", tenant_id, type(exc).__name__)
        _agencia_llm_fail_closed(runtime, type(exc).__name__)
        return f"LLM da agência indisponível ({type(exc).__name__})"

def save_cfg(cfg: dict, tenant_id: str = "legacy"):
    path = tenant_cfg_path(tenant_id)
    # Segredo vai CIFRADO para o disco (SEC-1 da auditoria: chmod 0600 sozinho
    # não protege no Windows). Valor já cifrado passa intacto — nunca cifrar
    # de novo, nunca tratar um token ilegível como ausente. Falha de cifragem
    # (com verificação de ida-e-volta) ABORTA o save: nada é gravado em claro.
    para_disco = dict(cfg)
    for chave in SECRETish:
        valor = para_disco.get(chave)
        if (isinstance(valor, str) and valor
                and not segredos_em_repouso.e_cifrado(valor)):
            para_disco[chave] = segredos_em_repouso.cifrar(valor)
    escrever_json_atomico(path, para_disco)
    os.chmod(path, 0o600)


def request_tenant(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.tenant_id if isinstance(principal, Principal) else "legacy"


# Painel (item 7 do escopo): saúde e latência da última consulta de IA de
# contexto, por inquilino. Só observabilidade — não guarda prompt nem resposta.
_IA_CONTEXTO: dict[str, dict] = {}


def _registrar_ia_contexto(tenant_id: str, provedor, latencia_ms: float,
                           ok: bool) -> None:
    _IA_CONTEXTO[tenant_id] = {
        "provedor": str(provedor or ""),
        "latencia_ms": round(latencia_ms, 1),
        "ok": bool(ok),
        "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# Último teste de autenticação/descoberta por provedor, por inquilino
# (Smart Setup): os estados configurado → conectado → autenticado →
# operacional derivam desta evidência — nunca de um "ping" imaginário.
# Em memória: é observação da sessão, não dado de negócio.
_ULTIMO_TESTE: dict[str, dict[str, dict]] = {}


def _registrar_teste(tenant_id: str, relatorio: dict) -> None:
    """Guarda o resultado do teste/da descoberta para o diagnóstico visual.
    Nunca guarda credencial — só estado, latência, limites e detalhe."""
    provedor = str(relatorio.get("provedor") or "").strip()
    if not provedor:
        return
    _ULTIMO_TESTE.setdefault(tenant_id, {})[provedor] = {
        "estado": str(relatorio.get("estado") or ""),
        "ok": bool(relatorio.get("ok")),
        "latencia_ms": relatorio.get("latencia_ms"),
        "detalhe": str(relatorio.get("detalhe") or ""),
        "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _orcamento_do_cfg(cfg: dict) -> tuple[float | None, str]:
    """(USD, aviso). Vazio/inválido/≤0 devolve None com o motivo — um teto
    de orçamento ilegível nunca vira teto zero 'por acidente'."""
    bruto = str(cfg.get("TRADINGAGENTS_IA_ORCAMENTO_USD") or "").strip()
    if not bruto:
        return None, ""
    try:
        valor = float(bruto.replace(",", "."))
    except ValueError:
        return None, "orçamento inválido (use números, ex.: 5.00)"
    if valor <= 0:
        return None, "orçamento precisa ser maior que zero"
    return valor, ""


def _ia_bloqueio(tenant_id: str) -> str:
    """Motivo de bloqueio do orçamento de IA ('' = permitido). Vale SÓ para
    chamadas controladas pelo DZ23 e SÓ quando o dono liga o bloqueio no
    painel — nunca toca travas de risco nem a execução da mesa."""
    from service.api import smart_setup

    cfg = load_cfg(tenant_id)
    orcamento, _aviso = _orcamento_do_cfg(cfg)
    if orcamento is None:
        return ""
    resumo = IA_USO.resumo(tenant_id, orcamento_usd=orcamento)
    permitida, motivo = smart_setup.ia_permitida(
        cfg, {**resumo["orcamento"], "parcial": resumo["custo_parcial"]})
    return "" if permitida else motivo

def upd(jid, **kw):
    JOB_STORE.update(jid, **kw)

_cancel = set()
_lock = threading.Lock()

def run_job(jid, ticker, date, tenant_id="legacy"):
    with _lock:  # um job por vez: env é global do processo
        runtime = runtime_for_tenant_id(tenant_id)
        if jid in _cancel:
            upd(jid, status="cancelled")
            return
        upd(jid, status="running")
        try:
            runtime.agency.set_agent_status("market-scanner", "ANALYZING")
            runtime.agency.set_agent_status("strategy-agent", "ANALYZING")
            cfg = load_cfg(tenant_id)
            # Guardado ANTES de qualquer mutação: o finally restaura isto, e
            # sem a captura aqui ele estouraria NameError mascarando o erro
            # real — além de deixar a chave de um inquilino no processo.
            _env_anterior = {k: os.environ.get(k) for k in ALLOWED_KEYS}
            for k, v in cfg.items():
                if k in ALLOWED_KEYS and v:
                    os.environ[k] = str(v)
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            conf = dict(DEFAULT_CONFIG)
            llm_conf, llm_env = resolve_llm(cfg)
            conf.update(llm_conf)
            os.environ.update(llm_env)
            graph = TradingAgentsGraph(config=conf)
            t_up = ticker.upper()
            asset_type = "crypto" if ("/" in t_up or t_up.endswith(("-USD", "-USDT", "-BTC"))) else "stock"
            state, decision = graph.propagate(ticker, date, asset_type=asset_type)
            reports = {k: v for k, v in (state or {}).items()
                       if isinstance(v, str) and ("report" in k or "decision" in k)}
            # guarda o mark de transporte + identidade: é o que permite EXECUTAR
            # a decisão depois, passando pelo gate de capital honestamente.
            reports["_transport_mark"] = (state or {}).get("transport_security")
            reports["_identity"] = {"company_of_interest": (state or {}).get("company_of_interest"),
                                     "trade_date": (state or {}).get("trade_date")}
            upd(jid, status="completed", decision=str(decision)[:4000],
                result=json.dumps(reports, ensure_ascii=False, default=str)[:200000])
            runtime.agency.set_agent_status("strategy-agent", "PAPER_TRADING")
            runtime.agency.set_agent_status("market-scanner", "WAITING_SETUP")
        except Exception as e:
            runtime.agency.set_agent_status("strategy-agent", "ERROR")
            upd(jid, status="failed",
                error=f"{type(e).__name__}: {e}"[:2000] + "\n" +
                      traceback.format_exc()[-1500:])
        finally:
            # a chave de um inquilino não pode sobrar no processo para o próximo
            for _k, _antes in _env_anterior.items():
                if _antes is None:
                    os.environ.pop(_k, None)
                else:
                    os.environ[_k] = _antes

async def _json_body(req: Request) -> dict:
    """Corpo JSON validado: requisição malformada é 422 com mensagem legível,
    nunca 500. É a única leitura crua de corpo em todo o serviço."""
    try:
        body = await req.json()
    except Exception as exc:
        raise HTTPException(422, "corpo da requisição não é JSON válido") from exc
    if not isinstance(body, dict):
        raise HTTPException(422, "corpo da requisição deve ser um objeto JSON")
    return body


@app.get("/healthz")
def hz():
    from tradingagents.versao import versao_do_pacote

    return {"status": "degraded" if HEALTH["avisos"] else "ok",
            "app": "tradingagents-agency/1",
            "versao": versao_do_pacote(),
            "armazenamento": HEALTH["armazenamento"],
            "banco": DATABASE_ORIGEM,
            "avisos": HEALTH["avisos"]}


@app.get("/readyz")
def readyz():
    blocked = KILL_SWITCH.state.enabled
    return {"status": "blocked" if blocked else "ready", "paper": PAPER_ADAPTER.healthcheck(), "event_sequence": EVENT_BUS.latest_sequence, "kill_switch": blocked}


@app.get("/metrics")
def metrics(_=Depends(auth)):
    return METRICS.snapshot(detailed=True)

@app.get("/")
def index():
    """Raiz é a página PÚBLICA: quem chega pelo domínio precisa entender o
    produto antes de ver um formulário de login."""
    return FileResponse(STATIC / "site.html")


@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
def aplicacao():
    """A plataforma em si — login e painel."""
    return FileResponse(STATIC / "index.html")


@app.get("/static/marca/{arquivo}", include_in_schema=False)
def marca(arquivo: str):
    """Serve os arquivos da marca (favicon, ícone de atalho, logo).

    Caminho fixo e nome simples: a pasta é pública por natureza, mas aceitar
    nome arbitrário abriria leitura de qualquer arquivo do disco.
    """
    permitidos = {"favicon.ico", "favicon-32.png", "favicon-64.png",
                  "dz23-logo.png", "dz23-logo-512.png", "dz23-logo-192.png",
                  "dz23-simbolo-512.png", "dz23-simbolo-192.png",
                  "dz23-simbolo-64.png", "dz23-icone.ico"}
    if arquivo not in permitidos:
        raise HTTPException(status_code=404, detail="arquivo não encontrado")
    caminho = STATIC / "marca" / arquivo
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="arquivo não encontrado")
    return FileResponse(caminho, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "marca" / "favicon.ico")


@app.get("/static/vendor/lightweight-charts/{arquivo}", include_in_schema=False)
def vendor_lightweight_charts(arquivo: str):
    """Biblioteca de gráficos vendorizada (Lightweight Charts, Apache-2.0).

    Servida pela MESMA origem, para o painel funcionar offline e sem CDN.
    Nome simples e allowlist, como a marca: aceitar caminho arbitrário
    abriria leitura de qualquer arquivo do disco.
    """
    permitidos = {"lightweight-charts.standalone.production.js",
                  "LICENSE", "VERSION"}
    if arquivo not in permitidos:
        raise HTTPException(status_code=404, detail="arquivo não encontrado")
    caminho = STATIC / "vendor" / "lightweight-charts" / arquivo
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="arquivo não encontrado")
    tipo = ("application/javascript" if arquivo.endswith(".js")
            else "text/plain")
    return FileResponse(caminho, media_type=tipo,
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    """Manifesto PWA: o painel instalável herda a identidade DZ23."""
    return FileResponse(STATIC / "manifest.webmanifest",
                        media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Service Worker do PWA. Escopo raiz exige o header abaixo; o arquivo
    versiona o cache e NUNCA guarda página autenticada nem dado de /v1."""
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache",
                                 "Service-Worker-Allowed": "/"})


# CSP/anti-cache do painel: o app instalado (PWA) roda em contexto seguro;
# respostas autenticadas não podem ficar em cache de proxy/navegador.
# O Floor é embutido same-origin no painel (X-Frame-Options SAMEORIGIN):
# frame-ancestors 'self' lá mantém o iframe vivo e ainda barra sites externos.
_CSP_BASE = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
             "font-src 'self' data:; connect-src 'self'; "
             "base-uri 'self'; form-action 'self'; frame-ancestors ")
_CSP_PAINEL = _CSP_BASE + "'none'"
_CSP_FLOOR = _CSP_BASE + "'self'"


@app.middleware("http")
async def cabecalhos_de_seguranca(request, call_next):
    resposta = await call_next(request)
    caminho = request.url.path
    if caminho.startswith("/v1") or caminho in ("/app", "/app/"):
        resposta.headers.setdefault("Cache-Control", "no-store")
    if resposta.headers.get("content-type", "").startswith("text/html"):
        resposta.headers.setdefault(
            "Content-Security-Policy",
            _CSP_FLOOR if caminho == "/floor" else _CSP_PAINEL,
        )
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("Referrer-Policy", "same-origin")
    return resposta

@app.get("/v1/config")
def get_cfg(request: Request, _=Depends(auth)):
    tenant = request_tenant(request)
    cfg = load_cfg(tenant)
    ilegiveis = _SEGREDOS_ILEGIVEIS.get(tenant, {})
    out = {}
    for k in ALLOWED_KEYS:
        v = cfg.get(k, "")
        if not v:
            out[k] = {"set": False}
        elif k in SECRETish:
            out[k] = {"set": True,
                      "hint": ("ilegível neste contexto — re-colar substitui"
                               if k in ilegiveis else "•••" + str(v)[-4:])}
        else:
            out[k] = {"set": True, "value": v}
    return {"config": out,
            "segredos_em_repouso": {**segredos_em_repouso.status(),
                                    "ilegiveis": dict(ilegiveis)}}

@app.post("/v1/config")
async def set_cfg(req: Request, _=Depends(auth)):
    body = await _json_body(req)
    tenant_id = request_tenant(req)
    cfg = load_cfg(tenant_id)
    fingerprint_execucao_anterior = testnet_credential_fingerprint({
        k: cfg.get(k, "") for k in (
            "TRADINGAGENTS_EXCHANGE", "EXCHANGE_API_KEY",
            "EXCHANGE_API_SECRET", "EXCHANGE_API_PASSWORD",
            "EXCHANGE_API_UID")})
    saved, changed = [], []
    vars_recebidas = body.get("vars") or {}
    vinculos_gerenciados = {
        "EXCHANGE_CREDENTIAL_EXCHANGE", "EXCHANGE_CREDENTIAL_BOUND_AT",
        "REAL_READ_ONLY_EXCHANGE"}
    for k, v in vars_recebidas.items():
        if (k in ALLOWED_KEYS and k not in vinculos_gerenciados
                and isinstance(v, str) and v.strip()):
            novo = v.strip()
            saved.append(k)
            atual = str(cfg.get(k) or "").strip()
            if not atual:
                # Nada salvo (nem no ambiente): o que vale é o padrão
                # efetivo do papel da chave. Salvar esse padrão não é
                # mudança e não pode reconstruir a mesa.
                atual = _DESK_EFFECTIVE_DEFAULTS.get(k, "")
            if atual != novo:
                changed.append(k)
            cfg[k] = novo
    # Uma credencial de execução nunca pode migrar silenciosamente para outra
    # corretora. O vínculo só é (re)feito quando chave E segredo novos chegam
    # juntos na mesma ação; trocar apenas o seletor deixa o checklist fechado.
    chave_nova = str(vars_recebidas.get("EXCHANGE_API_KEY") or "").strip()
    segredo_novo = str(vars_recebidas.get("EXCHANGE_API_SECRET") or "").strip()
    if chave_nova and segredo_novo:
        exchange = str(cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
        if exchange:
            anterior = str(
                cfg.get("EXCHANGE_CREDENTIAL_EXCHANGE") or "").strip().lower()
            cfg["EXCHANGE_CREDENTIAL_EXCHANGE"] = exchange
            if "EXCHANGE_CREDENTIAL_EXCHANGE" not in saved:
                saved.append("EXCHANGE_CREDENTIAL_EXCHANGE")
            if anterior != exchange:
                changed.append("EXCHANGE_CREDENTIAL_EXCHANGE")
    chave_leitura = str(
        vars_recebidas.get("REAL_READ_ONLY_API_KEY") or "").strip()
    segredo_leitura = str(
        vars_recebidas.get("REAL_READ_ONLY_API_SECRET") or "").strip()
    if chave_leitura and segredo_leitura:
        exchange = str(cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
        if exchange:
            anterior = str(
                cfg.get("REAL_READ_ONLY_EXCHANGE") or "").strip().lower()
            cfg["REAL_READ_ONLY_EXCHANGE"] = exchange
            if "REAL_READ_ONLY_EXCHANGE" not in saved:
                saved.append("REAL_READ_ONLY_EXCHANGE")
            if anterior != exchange:
                changed.append("REAL_READ_ONLY_EXCHANGE")
    token_iq = str(
        vars_recebidas.get("IQ_OPTION_MCP_BEARER_TOKEN") or "").strip()
    if token_iq:
        exchange = str(cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
        if exchange != "iq_option":
            raise HTTPException(
                409, "o token MCP só pode ser vinculado à IQ Option")
        anterior = str(
            cfg.get("REAL_READ_ONLY_EXCHANGE") or "").strip().lower()
        cfg["REAL_READ_ONLY_EXCHANGE"] = exchange
        if "REAL_READ_ONLY_EXCHANGE" not in saved:
            saved.append("REAL_READ_ONLY_EXCHANGE")
        if anterior != exchange:
            changed.append("REAL_READ_ONLY_EXCHANGE")
    # V8 §6: remover credencial/valor passou a ser possível. Antes o painel
    # só sabia SALVAR campo não-vazio — uma chave errada ficava presa para
    # sempre. A remoção tira do arquivo; se o ambiente do sistema ainda
    # fornecer o valor, a resposta diz isso (honestidade > botão mentiroso).
    pedidos = [k for k in (body.get("remover") or [])
               if isinstance(k, str) and k in ALLOWED_KEYS]
    for k in pedidos:
        if str(cfg.get(k) or "").strip():
            changed.append(k)
        cfg.pop(k, None)
    exchange_execucao = str(
        cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
    credencial_execucao_completa = bool(
        str(cfg.get("EXCHANGE_API_KEY") or "").strip()
        and str(cfg.get("EXCHANGE_API_SECRET") or "").strip()
        and str(cfg.get("EXCHANGE_CREDENTIAL_EXCHANGE") or "").strip().lower()
        == exchange_execucao)
    fingerprint_execucao_atual = testnet_credential_fingerprint({
        k: cfg.get(k, "") for k in (
            "TRADINGAGENTS_EXCHANGE", "EXCHANGE_API_KEY",
            "EXCHANGE_API_SECRET", "EXCHANGE_API_PASSWORD",
            "EXCHANGE_API_UID")})
    if credencial_execucao_completa:
        if (fingerprint_execucao_atual != fingerprint_execucao_anterior
                or not _horario_testnet_valido(
                    cfg.get("EXCHANGE_CREDENTIAL_BOUND_AT"))):
            cfg["EXCHANGE_CREDENTIAL_BOUND_AT"] = (
                datetime.now(timezone.utc).isoformat())
            saved.append("EXCHANGE_CREDENTIAL_BOUND_AT")
            changed.append("EXCHANGE_CREDENTIAL_BOUND_AT")
    elif cfg.pop("EXCHANGE_CREDENTIAL_BOUND_AT", None) is not None:
        changed.append("EXCHANGE_CREDENTIAL_BOUND_AT")
    if not saved and not pedidos:
        raise HTTPException(400, "nada para salvar")
    save_cfg(cfg, tenant_id)
    removidos, ainda_no_ambiente = [], []
    if pedidos:
        efeito = load_cfg(tenant_id)  # relê: ambiente + arquivo já sem a chave
        for k in pedidos:
            if str(efeito.get(k) or "").strip():
                ainda_no_ambiente.append(k)
            else:
                removidos.append(k)
    # Configuração que muda a mesa (corretora, feed, capital, limites) exige
    # reconstrução — senão o cliente salva e nada acontece até reiniciar.
    # Só quando o valor REALMENTE mudou: o painel reenvia o formulário
    # inteiro a cada Salvar, e um reenvio idêntico não pode derrubar a mesa
    # (a autônoma pararia sem o dono ter pedido).
    if any(k.startswith(("TRADINGAGENTS_DESK", "LIVE_", "EXCHANGE_")) or
           k in ("TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE")
           for k in changed):
        _desk_reset(tenant_id)
    return {"saved": saved, "removidos": removidos,
            "ainda_no_ambiente": ainda_no_ambiente}


def _ai_provedores_payload(tenant_id: str) -> dict:
    """Catálogo + estado da configuração de IA — NUNCA devolve credencial."""
    from service.api.ia_provedores import CATALOGO

    cfg = load_cfg(tenant_id)
    provedores = []
    for p in CATALOGO:
        chave_env = p["chave_env"]
        provedores.append({
            "id": p["id"], "nome": p["nome"], "chave_env": chave_env,
            "tem_chave": (bool(str(cfg.get(chave_env) or "").strip())
                          if chave_env else None),
            "url_padrao": p["url_padrao"],
            "exige_url": p.get("exige_url", False),
            "descoberta": p["descoberta"],
            "gratuito": p.get("gratuito", False),
        })
    return {
        "provedores": provedores,
        "principal": str(cfg.get("TRADINGAGENTS_LLM_PROVIDER") or ""),
        "fallback": str(cfg.get("TRADINGAGENTS_LLM_FALLBACK") or ""),
        "modelo_profundo": str(cfg.get("TRADINGAGENTS_DEEP_THINK_LLM") or ""),
        "modelo_rapido": str(cfg.get("TRADINGAGENTS_QUICK_THINK_LLM") or ""),
        "ultima_consulta": _IA_CONTEXTO.get(tenant_id),
    }


@app.get("/v1/ai/provedores")
def ai_listar_provedores(request: Request, _=Depends(auth)):
    return _ai_provedores_payload(request_tenant(request))


@app.post("/v1/ai/testar")
async def ai_testar_provedor(req: Request, _=Depends(auth)):
    """Testa autenticação do provedor de verdade (endpoint de modelos). Sem
    chave salva não há saída de rede — o teste não 'passa' sem credencial."""
    body = await _json_body(req)
    from service.api.ia_provedores import testar_provedor

    relatorio = testar_provedor(load_cfg(request_tenant(req)),
                                str(body.get("provedor") or ""))
    _registrar_teste(request_tenant(req), relatorio)
    return relatorio


@app.get("/v1/ai/modelos")
def ai_listar_modelos(request: Request, provedor: str = "", _=Depends(auth)):
    from service.api.ia_provedores import descobrir_modelos

    tenant_id = request_tenant(request)
    relatorio = descobrir_modelos(load_cfg(tenant_id), provedor)
    _registrar_teste(tenant_id, relatorio)
    return relatorio


@app.get("/v1/ai/uso")
def ai_uso(request: Request, _=Depends(auth)):
    """Consumo de IA do inquilino: chamadas, tokens reportados, custo
    estimado (tabela de referência) e estado do orçamento — INFORMATIVO,
    nunca um gate. Valores sem preço de referência ficam explícitos."""
    tenant_id = request_tenant(request)
    orcamento, aviso = _orcamento_do_cfg(load_cfg(tenant_id))
    return IA_USO.resumo(tenant_id, orcamento_usd=orcamento,
                         aviso_orcamento=aviso)


@app.get("/v1/setup/estado")
def setup_estado(request: Request, perfil: str = "pronto", _=Depends(auth)):
    """Assistente de primeira execução (Smart Setup): só as pendências
    realmente necessárias, sem percentual inventado. Não sai à rede."""
    tenant_id = request_tenant(request)
    cfg = load_cfg(tenant_id)
    from service.api import smart_setup

    return smart_setup.estado(
        cfg, perfil=perfil, tem_config=tenant_cfg_path(tenant_id).exists(),
        testes=_ULTIMO_TESTE.get(tenant_id, {}),
        uso=IA_USO.resumo(tenant_id))


@app.post("/v1/setup/aplicar")
async def setup_aplicar(req: Request, _=Depends(auth)):
    """Aplica um perfil SOMENTE em campos vazios — preferência já definida
    pelo dono nunca é sobrescrita (atualizar o app não mexe em configuração)
    e nenhuma chave de API é criada por aqui."""
    body = await _json_body(req)
    tenant_id = request_tenant(req)
    cfg = load_cfg(tenant_id)
    from service.api import smart_setup

    perfil = str(body.get("perfil") or "pronto").strip().lower()
    modelos = body.get("modelos") if isinstance(body.get("modelos"), dict) else {}
    try:
        resultado = smart_setup.aplicar(
            cfg, perfil, testes=_ULTIMO_TESTE.get(tenant_id, {}),
            modelos_por_provedor=modelos)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    save_cfg(resultado["cfg"], tenant_id)
    if any(k.startswith("TRADINGAGENTS_DESK") for k in resultado["aplicados"]):
        _desk_reset(tenant_id)
    return {"perfil": perfil, "aplicados": resultado["aplicados"],
            "mantidos": resultado["mantidos"], "sugestao": resultado["sugestao"]}


@app.get("/v1/setup/diagnostico")
def setup_diagnostico(request: Request, _=Depends(auth)):
    """Estados por provedor (configurado/conectado/autenticado/operacional)
    derivados de evidência local — último teste e registro de consumo."""
    tenant_id = request_tenant(request)
    from service.api import smart_setup

    return {"provedores": smart_setup.diagnostico(
        load_cfg(tenant_id), _ULTIMO_TESTE.get(tenant_id, {}),
        IA_USO.resumo(tenant_id))}

@app.post("/v1/jobs")
async def new_job(req: Request, _=Depends(auth)):
    b = await _json_body(req)
    t, d = str(b.get("ticker", "")).strip().upper(), str(b.get("trade_date", "")).strip()
    if not t or len(d) != 10:
        raise HTTPException(422, "ticker e trade_date (AAAA-MM-DD) obrigatórios")
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "trade_date deve ser uma data válida no formato AAAA-MM-DD") from None
    idem = req.headers.get("Idempotency-Key") or uuid.uuid4().hex
    tenant_id = request_tenant(req)
    existing = JOB_STORE.find_idempotent(tenant_id, idem)
    if existing:
        return {"id": existing, "idempotent": True}
    jid = uuid.uuid4().hex[:16]
    JOB_STORE.insert(job_id=jid, idem=idem, ticker=t, trade_date=d, tenant_id=tenant_id)
    threading.Thread(target=run_job, args=(jid, t, d, tenant_id), daemon=True).start()
    return {"id": jid}

@app.get("/v1/jobs")
def list_jobs(request: Request, _=Depends(auth)):
    tenant_id = request_tenant(request)
    return {"jobs": JOB_STORE.list(tenant_id)}


@app.get("/v1/jobs/health")
def jobs_health(_=Depends(auth)):
    return JOB_STORE.healthcheck()


@app.get("/v1/jobs/{jid}")
def get_job(jid: str, request: Request, _=Depends(auth)):
    tenant_id = request_tenant(request)
    result = JOB_STORE.get(jid, tenant_id)
    if not result:
        raise HTTPException(404, "job não encontrado")
    return result

@app.post("/v1/jobs/{jid}/cancel")
def cancel(jid: str, request: Request, _=Depends(auth)):
    tenant_id = request_tenant(request)
    _cancel.add(jid)
    if JOB_STORE.status(jid, tenant_id) == "queued":
        upd(jid, status="cancelled")
    return {"ok": True}


@app.get("/v1/personal/exchanges")
def personal_exchanges(_=Depends(auth)):
    """Authenticated read-only list; does not connect, trade, or validate exchange APIs."""
    from service.personal_exchange_catalog import public_catalog
    from service.personal_exchange_discovery import (
        discover_ccxt_catalog,
        installed_ccxt_exchange_ids,
    )

    fixed = public_catalog()
    try:
        dynamic = discover_ccxt_catalog(installed_ccxt_exchange_ids())
        inventory = [
            {"id": item.id, "configured": item.configured,
             "execution_enabled": item.execution_enabled, "status": item.status}
            for item in dynamic
        ]
        error = None
    except ImportError:
        inventory = []
        error = "CCXT_NOT_INSTALLED"
    except (AttributeError, TypeError, ValueError):
        inventory = []
        error = "CCXT_DISCOVERY_UNAVAILABLE"
    return {
        "fixed_rooms": fixed,
        "ccxt_available": inventory,
        "discovery_error": error,
        "execution_enabled": False,
    }


@app.get("/v1/personal/exchanges/{exchange_id}/capabilities")
def personal_exchange_capabilities(exchange_id: str, _=Depends(auth)):
    """Inspect local CCXT advertised features without API credentials or network calls."""
    import ccxt

    from service.personal_exchange_capabilities import report_capabilities
    from service.personal_exchange_discovery import installed_ccxt_exchange_ids

    allowed = installed_ccxt_exchange_ids(lambda: ccxt)
    if exchange_id not in allowed:
        raise HTTPException(404, "corretora não disponível no adaptador instalado")
    # Only permit local CCXT classes from the installed package, never a user URL.
    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    try:
        return report_capabilities(exchange_id, exchange.has)
    finally:
        close = getattr(exchange, "close", None)
        if callable(close):
            close()


@app.get("/v1/personal/exchanges/{exchange_id}/public-health")
def personal_exchange_public_health(exchange_id: str, _=Depends(auth)):
    """Prova conectividade pública com ticker+velas, sem usar credenciais."""
    from service.personal_exchange_health import probe_public_exchange
    try:
        return probe_public_exchange(exchange_id)
    except KeyError as exc:
        raise HTTPException(404, "corretora fora da linha DZ23") from exc


@app.get("/v1/personal/exchanges/iq_option/mcp-health")
def iq_option_mcp_health(request: Request, _=Depends(auth)):
    """Handshake+tools/list nos sete MCPs; nunca chama ferramenta remota."""
    from service.iq_option_mcp import probe_iq_option_mcp
    cfg = load_cfg(request_tenant(request))
    return probe_iq_option_mcp(
        token=str(cfg.get("IQ_OPTION_MCP_BEARER_TOKEN") or ""))


@app.get("/v1/broker/status")
def broker_status(request: Request, _=Depends(auth)):
    """Valida a configuração de corretora SEM enviar ordem nenhuma."""
    cfg = load_cfg(request_tenant(request))
    credenciais_ok, faltando = _credenciais_execucao_prontas(cfg)
    if not credenciais_ok:
        return {"ready": False,
                "erro": "credencial incompatível ou incompleta: "
                         + ", ".join(faltando)}
    env = {k: cfg.get(k, "") for k in
           ("TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE",
            "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET",
            "EXCHANGE_API_PASSWORD", "EXCHANGE_API_UID",
            "LIVE_TRADING_CONFIRM",
            "LIVE_MAX_ORDER_VALUE", "LIVE_MAX_DAILY_LOSS")}
    try:
        from tradingagents.brokers.ccxt_bridge import CCXTBridge
        b = CCXTBridge(env=env, journal_path=str(DATA / "broker-journal.jsonl"))
        nota = ("DINHEIRO REAL: ordens valem de verdade, respeitando os tetos"
                if b.mode == "live" else "testnet: ordens de treino")
        return {"ready": True, **b.status(), "nota": nota}
    except Exception as e:
        return {"ready": False, "erro": f"{type(e).__name__}: {e}"[:400]}


@app.post("/v1/broker/order")
async def broker_order(req: Request, _=Depends(auth)):
    """Endpoint removido: nenhuma ordem sai por esta rota legada."""
    raise HTTPException(410, "endpoint legado permanentemente removido: use /v1/execution/paper ou o ExecutionRouter governado")


@app.get("/floor")
def floor():
    """Trading Floor visual; os dados continuam protegidos pela API autenticada."""
    return FileResponse(STATIC / "floor.html")


@app.get("/v1/agency/snapshot")
def agency_snapshot(request: Request, _=Depends(auth)):
    runtime = runtime_for(request)
    snapshot = runtime.agency.snapshot()
    agents_by_id = runtime.agency.agents
    for desk in snapshot["desks"]:
        desk["agents_data"] = [agents_by_id[agent_id] for agent_id in desk["agents"]]
    return snapshot


@app.get("/v1/agency/events")
def agency_events(request: Request, after: int = 0, _=Depends(auth)):
    if after < 0:
        raise HTTPException(422, "after deve ser não-negativo")
    return {"events": [{"sequence": item.sequence, "event": item.event.as_dict()} for item in runtime_for(request).agency.event_bus.replay(after)]}


@app.get("/v1/agency/health")
def agency_health(request: Request, _=Depends(auth)):
    runtime = runtime_for(request)
    configured = sum(agent_id in runtime.agency.executor.handlers for agent_id in _AGENCY_LLM_AGENT_IDS)
    required = len(_AGENCY_LLM_AGENT_IDS)
    agent_details = [
        {
            "agent_id": agent_id,
            "desk_id": agent["desk_id"],
            "role": agent["role"],
            "status": agent["status"],
            "handler_configured": agent_id in runtime.agency.executor.handlers,
        }
        for agent_id, agent in sorted(runtime.agency.agents.items())
    ]
    return {
        "runtime": "READY" if configured == required else "DEGRADED",
        "event_sequence": runtime.agency.event_bus.latest_sequence,
        "agents": len(runtime.agency.agents),
        "llm_handlers": {"configured": configured, "required": required},
        "agent_details": agent_details,
        "next_action": None if configured == required else "Configure provider, model e credencial em /v1/config para este tenant",
        "worker": runtime.agency.worker_status(request_tenant(request)),
        "meetings": len(runtime.agency.meetings),
        "audit_log": "tenant-scoped persistent event stream",
    }


@app.get("/v1/agency/tasks")
def agency_tasks(request: Request, _=Depends(auth)):
    """Lista apenas tarefas do tenant autenticado."""
    tenant_id = request_tenant(request)
    return {"tasks": runtime_for(request).agency.tasks_for_tenant(tenant_id)}


@app.get("/v1/agency/memory")
def agency_memory(request: Request, q: str = "", limit: int = 20, _=Depends(auth)):
    if limit < 1 or limit > 100:
        raise HTTPException(422, "limit deve estar entre 1 e 100")
    return {"memories": runtime_for(request).agency.recall_memory(request_tenant(request), query=q, limit=limit)}


@app.get("/v1/agency/missions")
def agency_missions(request: Request, _=Depends(auth)):
    return {"missions": runtime_for(request).agency.missions_for_tenant(request_tenant(request))}


@app.post("/v1/agency/missions")
async def agency_create_mission(req: Request, _=Depends(auth)):
    body = await _json_body(req)
    agent_ids = tuple(str(item) for item in body.get("agent_ids", _GENERAL_AGENT_IDS))
    try:
        mission = runtime_for(req).agency.create_mission(
            request_tenant(req), str(body.get("objective", "")), agent_ids,
            requires_approval=bool(body.get("requires_approval", False)))
    except KeyError:
        raise HTTPException(422, "agente da missão não encontrado") from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"missão inválida: {exc}") from exc
    return mission


@app.post("/v1/agency/tasks")
async def agency_submit_task(req: Request, _=Depends(auth)):
    body = await _json_body(req)
    tenant_id = request_tenant(req)
    try:
        task = AgencyTask(
            task_id=str(body.get("task_id") or uuid.uuid4()),
            mission_id=str(body["mission_id"]),
            tenant_id=tenant_id,
            agent_id=str(body["agent_id"]),
            kind=str(body["kind"]),
            payload=dict(body.get("payload", {})),
            idempotency_key=str(body.get("idempotency_key") or uuid.uuid4()),
            dependencies=tuple(str(item) for item in body.get("dependencies", [])),
            requires_approval=bool(body.get("requires_approval", False)),
            max_attempts=int(body.get("max_attempts", 2)),
        )
        created = runtime_for(req).agency.submit_task(task)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"tarefa inválida: {exc}") from exc
    item = next(item for item in runtime_for(req).agency.tasks_for_tenant(tenant_id)
                if item["task_id"] == created.task_id)
    return {"task": item}


@app.post("/v1/agency/tasks/{task_id}/approve")
def agency_approve_task(task_id: str, request: Request, _=Depends(auth)):
    try:
        task = runtime_for(request).agency.approve_task(task_id, request_tenant(request))
    except KeyError:
        raise HTTPException(404, "tarefa não encontrada") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task.task_id, "status": task.status.value}


@app.post("/v1/agency/tasks/run")
async def agency_run_tasks(req: Request, _=Depends(auth)):
    body = {} if req.headers.get("content-length", "0") == "0" else await _json_body(req)
    try:
        timeout = min(max(float(body.get("timeout", 30)), 0.1), 120)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "timeout inválido") from exc
    runtime = runtime_for(req)
    diagnostic = configure_agency_llm(runtime, request_tenant(req))
    result = runtime.agency.run_tasks(request_tenant(req), timeout=timeout)
    if diagnostic:
        result["llm"] = {"status": "degraded", "reason": diagnostic}
    else:
        result["llm"] = {"status": "ready"}
    return result


@app.get("/v1/agency/worker/status")
def agency_worker_status(request: Request, _=Depends(auth)):
    try:
        return runtime_for(request).agency.worker_status(request_tenant(request))
    except KeyError:
        raise HTTPException(403, "worker pertence a outro tenant") from None


@app.post("/v1/agency/worker/start")
async def agency_worker_start(req: Request, _=Depends(auth)):
    body = {} if req.headers.get("content-length", "0") == "0" else await _json_body(req)
    try:
        poll_interval = float(body.get("poll_interval", 1.0))
        cycle_timeout = float(body.get("cycle_timeout", 30.0))
        tenant_runtime = runtime_for(req)
        runtime = tenant_runtime.agency
        tenant_id = request_tenant(req)
        diagnostic = configure_agency_llm(tenant_runtime, tenant_id)
        pending = [task for task in runtime.tasks_for_tenant(tenant_id)
                   if task["status"] in {"QUEUED", "RETRYING", "WAITING_DEPENDENCY"}]
        if diagnostic and pending:
            raise HTTPException(409, diagnostic)
        return runtime.start_worker(tenant_id, poll_interval=poll_interval, cycle_timeout=cycle_timeout)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"configuração de worker inválida: {exc}") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/v1/agency/worker/stop")
def agency_worker_stop(request: Request, _=Depends(auth)):
    try:
        return runtime_for(request).agency.stop_worker(request_tenant(request))
    except KeyError:
        raise HTTPException(403, "worker pertence a outro tenant") from None


@app.get("/v1/office/snapshot")
def office_snapshot(request: Request, _=Depends(auth)):
    runtime = runtime_for(request)
    agency = runtime.agency.snapshot()
    agents_by_id = runtime.agency.agents
    for desk in agency["desks"]:
        desk["agents_data"] = [agents_by_id[agent_id] for agent_id in desk["agents"]]
    kill_state = runtime.kill_switch.state
    return {
        "schema_version": "1.0",
        "mode": "PAPER_SHADOW",
        "live_trading": False,
        "agency": agency,
        "paper": runtime.paper.reconciliation_snapshot(),
        "shadow": runtime.shadow.summary(),
        "kill_switch": {"enabled": kill_state.enabled, "reason": kill_state.reason, "changed_at": kill_state.changed_at.isoformat(), "actor": kill_state.actor},
        "jobs": JOB_STORE.list(request_tenant(request), limit=10),
        "job_store": JOB_STORE.healthcheck(),
        "sequences": {"agency": runtime.agency.event_bus.latest_sequence, "execution": runtime.execution_event_bus.latest_sequence},
    }


@app.get("/v1/office/events")
def office_events(request: Request, after_agency: int = 0, after_execution: int = 0, _=Depends(auth)):
    if after_agency < 0 or after_execution < 0:
        raise HTTPException(422, "cursores devem ser não-negativos")
    runtime = runtime_for(request)
    items = [
        {"stream": "agency", "sequence": item.sequence, "event": item.event.as_dict()}
        for item in runtime.agency.event_bus.replay(after_agency)
    ]
    items.extend(
        {"stream": "execution", "sequence": item.sequence, "event": item.event.as_dict()}
        for item in runtime.execution_event_bus.replay(after_execution)
    )
    items.sort(key=lambda item: item["event"]["occurred_at"])
    return {"schema_version": "1.0", "events": items}


@app.post("/v1/agency/agents/{agent_id}/status")
async def agency_agent_status(agent_id: str, req: Request, _=Depends(auth)):
    body = await _json_body(req)
    status = str(body.get("status", "")).strip().upper()
    if not status:
        raise HTTPException(422, "status obrigatório")
    try:
        event = runtime_for(req).agency.set_agent_status(agent_id, status)
    except KeyError:
        raise HTTPException(404, "agente não encontrado") from None
    return {"ok": True, "event_id": event.event_id}


@app.post("/v1/agency/meetings")
async def agency_meeting(req: Request, _=Depends(auth)):
    body = await _json_body(req)
    try:
        meeting = runtime_for(req).agency.start_meeting(str(body["meeting_id"]), str(body["meeting_type"]), str(body["desk_id"]), tuple(body["participants"]), int(body["quorum"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"reunião inválida: {exc}") from exc
    return {"meeting": {"meeting_id": meeting.meeting_id, "status": meeting.status, "quorum": meeting.quorum}}


@app.post("/v1/agency/meetings/{meeting_id}/votes")
async def agency_vote(meeting_id: str, req: Request, _=Depends(auth)):
    body = await _json_body(req)
    meeting = runtime_for(req).agency.meetings.get(meeting_id)
    if not meeting:
        raise HTTPException(404, "reunião não encontrada")
    try:
        meeting.record_vote(str(body["agent_id"]), str(body["action"]), float(body["confidence"]), list(body.get("evidence_ids", [])))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"voto inválido: {exc}") from exc
    return {"meeting_id": meeting_id, "status": meeting.status, "votes": meeting.votes}


@app.get("/v1/agency/stream")
async def agency_stream(request: Request, after: int = 0, _=Depends(auth)):
    runtime = runtime_for(request)
    async def events():
        cursor = after
        for _ in range(120):
            items = runtime.agency.event_bus.replay(cursor)
            for item in items:
                cursor = item.sequence
                yield f"id: {cursor}\nevent: {item.event.event_type}\ndata: {json.dumps(item.event.as_dict(), ensure_ascii=False)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/v1/execution/paper")
async def paper_execution(req: Request, _=Depends(auth)):
    """Executa apenas Paper através de Decision -> RiskEngine -> Adapter."""
    body = await _json_body(req)
    runtime = runtime_for(req)
    try:
        now = datetime.now(timezone.utc)
        decision = TradeDecision(
            str(body["decision_id"]), str(body.get("run_id", uuid.uuid4().hex)), str(body["strategy_id"]), str(body.get("desk_id", "strategy")), str(body["instrument_id"]), "paper", runtime.paper.account_id,
            Side(str(body["side"]).upper()), str(body.get("order_type", "MARKET")), Decimal(str(body["entry_price"])), Decimal(str(body["stop_loss"])), Decimal(str(body["take_profit"])), Decimal(str(body["risk_amount"])), Decimal(str(body["risk_pct_equity"])), Decimal(str(body["quantity"])), str(body.get("time_in_force", "GTC")), now + timedelta(seconds=int(body.get("signal_ttl_seconds", 60))), Decimal(str(body["confidence"])), str(body.get("regime", "UNKNOWN")), tuple(body.get("evidence_ids", [])), tuple(body.get("agent_votes", [])), now,
        )
        observed = datetime.fromisoformat(str(body.get("observed_at", now.isoformat())))
        snapshot = MarketSnapshot(str(body.get("snapshot_id", uuid.uuid4().hex)), decision.instrument_id, "paper", Decimal(str(body["bid"])), Decimal(str(body["ask"])), Decimal(str(body.get("last", body["entry_price"]))), Decimal(str(body.get("volume", "0"))), Decimal(str(body.get("spread", Decimal(str(body["ask"])) - Decimal(str(body["bid"]))))), observed, now, "paper-api", int(body.get("sequence", 0)))
        portfolio = runtime.paper.get_portfolio(runtime.paper.account_id)
        request_order = OrderRequest(str(body.get("client_order_id", f"paper-{decision.decision_id}")), decision.decision_id, decision.run_id, decision.strategy_id, decision.account_id, decision.instrument_id, "paper", decision.side, decision.order_type, decision.quantity, decision.entry_price_reference, decision.stop_loss, decision.take_profit)
        result = runtime.router.submit(decision, portfolio, snapshot, runtime.paper, request=request_order)
        return {"risk": {"status": result.risk.status.value, "reason_codes": result.risk.reason_codes, "risk_decision_id": result.risk.risk_decision_id}, "order": result.order.__dict__ if result.order and hasattr(result.order, "__dict__") else ({"order_id": result.order.order_id, "client_order_id": result.order.client_order_id, "status": result.order.status.value, "filled_quantity": str(result.order.filled_quantity), "average_price": str(result.order.average_price) if result.order.average_price is not None else None} if result.order else None)}
    except PermissionError as exc:
        raise HTTPException(423, str(exc)) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"paper execution inválida: {exc}") from exc


@app.get("/v1/execution/shadow")
def shadow_summary(request: Request, _=Depends(auth)):
    return runtime_for(request).shadow.summary()


@app.post("/v1/execution/shadow")
async def shadow_execution(req: Request, _=Depends(auth)):
    body = await _json_body(req)
    runtime = runtime_for(req)
    try:
        now = datetime.now(timezone.utc)
        request = OrderRequest(str(body["client_order_id"]), str(body.get("decision_id", "shadow-decision")), str(body.get("run_id", "shadow-run")), str(body.get("strategy_id", "shadow-strategy")), "shadow-account", str(body["instrument_id"]), "shadow", Side(str(body["side"]).upper()), str(body.get("order_type", "MARKET")), Decimal(str(body["quantity"])), Decimal(str(body.get("price", body["last"]))), Decimal(str(body.get("stop_loss", "0.0001"))), Decimal(str(body.get("take_profit", "0.0002"))))
        snapshot = MarketSnapshot(str(body.get("snapshot_id", uuid.uuid4().hex)), request.instrument_id, "shadow", Decimal(str(body["bid"])), Decimal(str(body["ask"])), Decimal(str(body["last"])), Decimal(str(body.get("volume", "0"))), Decimal(str(body.get("spread", "0"))), now, now, "shadow-api", int(body.get("sequence", 0)))
        trade = runtime.shadow.record(request, snapshot)
        return {"shadow_id": trade.shadow_id, "client_order_id": trade.client_order_id, "status": trade.status, "reference_price": str(trade.reference_price), "orders_sent": 0}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"shadow execution inválida: {exc}") from exc


@app.get("/v1/execution/health")
def execution_health(request: Request, _=Depends(auth)):
    runtime = runtime_for(request)
    return {"paper": runtime.paper.healthcheck(), "kill_switch": {"enabled": runtime.kill_switch.state.enabled, "reason": runtime.kill_switch.state.reason, "changed_at": runtime.kill_switch.state.changed_at.isoformat(), "actor": runtime.kill_switch.state.actor}, "reconciliation": runtime.reconciliation.health(), "workers": SUPERVISOR.health(), "event_sequence": runtime.router.event_bus.latest_sequence}


@app.post("/v1/execution/reconciliation")
def execution_reconciliation(request: Request, _=Depends(auth)):
    report = runtime_for(request).reconciliation.run_once()
    return {"status": report.status, "safe": report.is_safe, "missing_local": report.missing_local, "missing_remote": report.missing_remote, "mismatches": report.mismatches}


@app.post("/v1/execution/kill-switch")
async def execution_kill_switch(req: Request, _=Depends(auth)):
    body = await _json_body(req)
    runtime = runtime_for(req)
    action = str(body.get("action", "engage")).lower()
    if action == "engage":
        state = runtime.kill_switch.engage(str(body.get("reason", "bloqueio administrativo")), actor=str(body.get("actor", "api")))
    elif action == "reset":
        report = runtime.reconciliation.run_once()
        try:
            state = runtime.kill_switch.reset(actor=str(body.get("actor", "api")), reconciliation_ok=report.is_safe)
        except PermissionError as exc:
            raise HTTPException(423, str(exc)) from exc
    else:
        raise HTTPException(422, "action deve ser engage ou reset")
    return {"enabled": state.enabled, "reason": state.reason, "changed_at": state.changed_at.isoformat(), "actor": state.actor}


class TenantRuntime:
    def __init__(self, tenant_id: str):
        root = DATA / "tenants" / tenant_id
        root.mkdir(parents=True, exist_ok=True)
        audit = AuditLog(root / "execution-events.jsonl")
        bus = EventBus(audit)
        self.execution_event_bus = bus
        self.agency_audit = AuditLog(root / "agency-events.jsonl")
        self.agency = AgencyRuntime(audit_log=self.agency_audit, event_bus=EventBus(self.agency_audit),
                                    store=AgencyTaskStore(root / "agency.db"))
        self.paper = PaperAdapter(state_path=root / "paper-state.json")
        self.shadow = ShadowAdapter(state_path=root / "shadow-state.json")
        self.kill_switch = KillSwitch(root / "kill-switch.json")
        self.metrics = MetricsRegistry()
        self.reconciliation = ReconciliationMonitor(self.paper.reconciliation_snapshot, lambda: (_ for _ in ()).throw(RuntimeError("provider remoto de reconciliação não configurado")))
        self.router = ExecutionRouter(risk_engine=RiskEngine(), audit_log=audit, event_bus=bus, kill_switch=self.kill_switch, metrics=self.metrics, reconciliation_guard=lambda: bool(self.reconciliation.last_report and self.reconciliation.last_report.is_safe))
        for agent_id, desk, role in (("market-scanner", "research", "Market Scanner"), ("technical-analyst", "research", "Technical Analyst"), ("sentiment-analyst", "research", "Sentiment Analyst"), ("fundamental-analyst", "research", "Fundamental Analyst"), ("macro-analyst", "research", "Macro Analyst"), ("quant-researcher", "research", "Quant Researcher"), ("alternative-data-agent", "research", "Alternative Data Analyst"), ("strategy-agent", "strategy", "Strategy Researcher"), ("risk-advisor", "risk", "Risk Advisor"), ("portfolio-manager", "portfolio", "Portfolio Manager"), ("execution-planner", "execution", "Execution Planner"), ("orderbook-analyst", "execution", "Order Book Analyst"), ("trading-compliance-agent", "risk", "Trading Compliance"), ("trade-journal-agent", "monitoring", "Trade Journal"), ("reconciliation-agent", "monitoring", "Reconciliation")):
            self.agency.register_agent(agent_id, desk, role)
        for agent_id, desk, role in (
            ("research-agent", "research", "Business Research"),
            ("news-agent", "intelligence", "News Intelligence"),
            ("operations-agent", "operations", "Operations Manager"),
            ("content-agent", "content", "Content Studio"),
            ("finance-agent", "finance", "Financial Analyst"),
            ("crm-agent", "crm", "CRM Manager"),
            ("market-intelligence-agent", "intelligence", "Market Intelligence"),
        ):
            self.agency.register_agent(agent_id, desk, role)


_TENANT_RUNTIMES: dict[str, TenantRuntime] = {}
_TENANT_RUNTIME_LOCK = threading.Lock()


def runtime_for(request: Request):
    return runtime_for_tenant_id(request_tenant(request))


def runtime_for_tenant_id(tenant_id: str):
    if tenant_id == "legacy" or not MULTI_TENANT_ENABLED:
        return type("LegacyRuntime", (), {"agency": AGENCY, "paper": PAPER_ADAPTER, "shadow": SHADOW_ADAPTER, "kill_switch": KILL_SWITCH, "metrics": METRICS, "router": EXECUTION_ROUTER, "reconciliation": PAPER_RECONCILIATION, "execution_event_bus": EVENT_BUS})()
    with _TENANT_RUNTIME_LOCK:
        return _TENANT_RUNTIMES.setdefault(tenant_id, TenantRuntime(tenant_id))


# ================================================================== COBRANÇA
# PIX via Woovi. O servidor só ativa plano com webhook ASSINADO (verificado
# com a chave pública) — nada que venha do navegador libera acesso.
from tradingagents.billing.store import SubscriptionStore  # noqa: E402
from tradingagents.billing.woovi import (  # noqa: E402
    PLANS,
    BillingError,
    WebhookRejected,
    WooviClient,
    apply_paid_event,
    verify_webhook,
)


def _build_subscription_store():
    """Mesma regra do resto: banco fora do ar degrada, não derruba. Sem isso,
    um Postgres indisponível impediria o cliente até de PAGAR."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        try:
            return SubscriptionStore(DATA / "subscriptions.db", dsn)
        except Exception as exc:
            HEALTH["avisos"].append(
                "assinaturas: PostgreSQL indisponível "
                f"({type(exc).__name__}); usando o banco local do volume.")
    return SubscriptionStore(DATA / "subscriptions.db", None)


SUBS = _build_subscription_store()


def _woovi_public_key() -> str:
    return (os.environ.get("WOOVI_WEBHOOK_PUBLIC_KEY")
            or load_cfg().get("WOOVI_WEBHOOK_PUBLIC_KEY") or "")


def require_plan(request: Request, feature: str = "mesa"):
    """Portão de acesso. Assinatura vencida bloqueia o que custa dinheiro
    (mesa, análises), mas NUNCA a página de pagamento nem o login — senão o
    cliente não consegue nem pagar para voltar."""
    # GATE G2: num aplicativo pessoal de dono único não há plano a cobrar; o
    # dono tem direito pleno. O portão de assinatura (402) é conceito de SaaS
    # e fica desligado junto com o resto da superfície. A assinatura segue
    # registrada apenas para preservar os dados antigos.
    if SINGLE_OWNER_ENFORCED:
        return SUBS.ensure("legacy")
    tenant = request_tenant(request)
    sub = SUBS.ensure(tenant)
    if SINGLE_OWNER_ENFORCED or tenant == "legacy":
        return sub
    if not sub.is_active():
        raise HTTPException(402, {
            "erro": "assinatura expirada",
            "plano_atual": sub.plan_code,
            "acao": "Assine em Plano e pagamento para continuar usando",
        })
    return sub


@app.get("/v1/billing/planos")
def billing_plans(_=Depends(auth)):
    return {"planos": [{"codigo": p.code, "nome": p.name, "preco": p.price_brl,
                        "preco_centavos": p.price_cents, "dias": p.period_days,
                        "recursos": list(p.features)}
                       for p in PLANS.values()]}


@app.get("/v1/billing/status")
def billing_status(request: Request, _=Depends(auth)):
    sub = SUBS.ensure(request_tenant(request))
    return {"plano": sub.plan_code, "nome": sub.plan.name, "status": sub.status,
            "ativo": sub.is_active(), "expira_em": sub.expires_at.isoformat(),
            "gateway_configurado": WooviClient().configured}


@app.post("/v1/billing/checkout")
async def billing_checkout(req: Request, _=Depends(auth)):
    """Gera a cobrança PIX. Não ativa nada: quem ativa é o webhook assinado."""
    body = await _json_body(req)
    plano = str(body.get("plano", "starter")).strip().lower()
    if plano not in PLANS or PLANS[plano].price_cents <= 0:
        raise HTTPException(422, "plano inválido")
    tenant = request_tenant(req)
    correlation = f"dz23-{tenant}-{plano}-{time.strftime('%Y%m')}"
    try:
        resposta = WooviClient().create_charge(tenant, plano, correlation)
    except BillingError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True, "correlation_id": correlation,
            **WooviClient.charge_view(resposta)}


@app.post("/v1/webhooks/woovi")
async def woovi_webhook(request: Request):
    """PÚBLICO por necessidade — e por isso verificado por assinatura RSA
    sobre o corpo BRUTO. Sem assinatura válida, nada é ativado."""
    raw = await request.body()
    assinatura = (request.headers.get("x-webhook-signature")
                  or request.headers.get("signature") or "")
    try:
        evento = verify_webhook(raw, assinatura, _woovi_public_key())
    except WebhookRejected as exc:
        raise HTTPException(401, str(exc)) from None
    charge = evento.get("charge") or {}
    if (evento.get("event") or charge.get("status", "")).upper() not in (
            "OPENPIX:CHARGE_COMPLETED", "COMPLETED", "PAID"):
        return {"ok": True, "ignorado": "evento não é de pagamento concluído"}
    correlation = charge.get("correlationID", "")
    partes = correlation.split("-")
    if len(partes) < 3 or partes[0] != "dz23":
        return {"ok": True, "ignorado": "cobrança não pertence a este sistema"}
    tenant, plano = partes[1], partes[2]
    charge_id = charge.get("identifier") or correlation
    atual = SUBS.get(tenant)
    nova = apply_paid_event(atual, tenant, plano, charge_id)
    SUBS.save(nova)
    return {"ok": True, "tenant": tenant, "plano": nova.plan_code,
            "expira_em": nova.expires_at.isoformat()}

# ================================================================== CADASTRO
# Autoatendimento: o cliente cria a própria agência, recebe o período de
# teste e pode assinar. Sem isto não há produto para vender — só instalação
# manual pelo dono.
#
# DECISÃO ASSUMIDA: cadastro aberto por padrão (SIGNUP_ENABLED=true). Para
# um SaaS que se vende sozinho, exigir token do administrador mataria a
# conversão. Quem quiser produto fechado põe SIGNUP_ENABLED=false.
SIGNUP_ENABLED = os.environ.get("SIGNUP_ENABLED", "true").strip().lower() != "false"
_SIGNUP_ATTEMPTS: dict = {}
SIGNUP_MAX, SIGNUP_WINDOW = 5, 3600


def _signup_rate_limit(ip: str) -> None:
    """Cadastro aberto sem limite vira fábrica de contas de robô."""
    agora = time.time()
    janela = [t for t in _SIGNUP_ATTEMPTS.get(ip, []) if agora - t < SIGNUP_WINDOW]
    if len(janela) >= SIGNUP_MAX:
        raise HTTPException(429, "muitos cadastros deste endereço; tente mais tarde")
    janela.append(agora)
    _SIGNUP_ATTEMPTS[ip] = janela


def _validar_senha(senha: str) -> None:
    if len(senha) < 12:
        raise HTTPException(422, "a senha precisa de pelo menos 12 caracteres")
    if senha.lower() in ("senha123456789", "123456789012", "password1234"):
        raise HTTPException(422, "escolha uma senha menos previsível")


def _validar_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1] or len(email) < 6:
        raise HTTPException(422, "e-mail inválido")
    return email


@app.get("/v1/auth/signup-status")
def signup_status():
    """Público: o painel precisa saber se mostra 'Criar conta'."""
    return {"habilitado": SIGNUP_ENABLED and MULTI_TENANT_ENABLED and bool(TENANT_STORE),
            "dias_de_teste": PLANS["trial"].period_days,
            "planos": [{"codigo": p.code, "nome": p.name, "preco": p.price_brl}
                       for p in PLANS.values() if p.price_cents > 0]}


@app.post("/v1/auth/signup")
async def tenant_signup(request: Request):
    """Cria agência + administrador + período de teste, em uma chamada."""
    if not (SIGNUP_ENABLED and MULTI_TENANT_ENABLED and TENANT_STORE):
        raise HTTPException(404, "cadastro desabilitado neste servidor")
    _signup_rate_limit(_client_ip(request))
    body = await _json_body(request)
    nome = str(body.get("agencia", "")).strip()
    email = _validar_email(str(body.get("email", "")))
    senha = str(body.get("senha") or body.get("password") or "")
    if len(nome) < 2:
        raise HTTPException(422, "informe o nome da sua agência")
    _validar_senha(senha)
    try:
        tenant_id = TENANT_STORE.create_tenant(nome)
        user_id = TENANT_STORE.create_user(email, senha)
        TENANT_STORE.add_member(tenant_id, user_id, "admin")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        # e-mail repetido chega como erro de banco: vira mensagem humana,
        # nunca um 500 com detalhe interno.
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(409, "este e-mail já tem conta; faça login") from None
        raise HTTPException(500, "não foi possível criar a conta agora") from None
    sub = SUBS.ensure(tenant_id)      # teste começa na criação
    token = TENANT_STORE.login(email, senha, tenant_id)
    return {"ok": True, "tenant_id": tenant_id, "access_token": token,
            "plano": sub.plan_code, "teste_expira_em": sub.expires_at.isoformat(),
            "proximo_passo": "Configure a IA (Groq é grátis) e rode a primeira análise"}


# ============================================================ LOGIN SOCIAL
# OAuth só PROVA identidade; quem concede acesso continua sendo o tenant
# store. Conta nova pelo social entra com teste grátis, igual ao cadastro.
from tradingagents.identity.oauth import (  # noqa: E402
    PROVIDERS,
    OAuthError,
    StateStore,
    authorize_url,
    email_of,
    exchange_code,
)

_OAUTH_STATES = StateStore()
_OAUTH_TRANSPORT = None          # injetável em teste; None = rede real


# Valores aceitos como "ligado". Qualquer outra coisa — inclusive "talvez",
# "enable" ou um erro de digitação — é NÃO. Num campo que decide se ordens
# chegam a uma corretora, o erro precisa cair do lado seguro.
_SIM = frozenset({"1", "true", "yes", "sim", "on"})
_NAO = frozenset({"", "0", "false", "no", "nao", "não", "off"})


def flag_ligada(nome: str, env=None) -> bool:
    """Parser estrito de flag booleana de ambiente.

    `bool(os.environ.get(...))` liga a flag com QUALQUER texto — "false" e
    "0" incluídos, porque string não vazia é verdadeira em Python. Aqui só
    um conjunto declarado liga; desconhecido fica desligado.
    """
    bruto = (env or os.environ).get(nome)
    if bruto is None:
        return False
    # Só o conjunto declarado liga. `_NAO` existe para documentar os
    # negativos esperados; qualquer valor fora dos dois cai aqui como NÃO.
    return str(bruto).strip().lower() in _SIM


@app.get("/v1/status/operacao")
def status_operacao():
    """Status PÚBLICO de capacidade e rollout da plataforma.

    Texto fixo na tela mente com o tempo: alguém libera um gate e a interface
    continua dizendo o contrário, ou o gate fecha e a tela segue convidando a
    operar. O painel pergunta; quem responde é o servidor.

    LIVE não é removido do produto — é uma capacidade PREVISTA e hoje
    indisponível. Some da tela seria esconder o roteiro; dizer que está
    pronta seria mentir.

    O QUE ISTO NÃO É: prova de prontidão operacional. Aqui se responde o que
    a PLATAFORMA oferece — sem nada de inquilino, conta, posição ou saldo,
    porque a rota é pública. O estado real de um inquilino depende de kill
    switch, reconciliação, incidentes de risco, consistência de capital,
    capacidade da corretora, mandato, autorização e frescor do SAFE; isso
    vive em /v1/operational-readiness, autenticado e por inquilino.
    Responder aqui o que é de lá vazaria estado de cliente para quem nem fez
    login.
    """
    ambiente = {
        "PAPER": {"habilitado": True,
                  "descricao": "simulação completa, sem ordem na corretora"},
        "SHADOW": {"habilitado": True,
                   "descricao": "decisões com dados reais, sem envio"},
        "DEMO": {"habilitado": flag_ligada("DEMO_TRADING"),
                 "descricao": "conta de demonstração da corretora"},
        "LIVE": {"habilitado": False,
                 "descricao": "execução com dinheiro real"},
    }
    # Um gate só é "aprovado" quando há evidência; enquanto não há, o estado
    # é EM ANDAMENTO — não "pendente de configuração".
    gates = [
        {"nome": "kernel financeiro", "status": "EM_AUDITORIA",
         "detalhe": "capital, reservas e reconciliação sob auditoria adversarial"},
        {"nome": "segurança de dados", "status": "EM_AUDITORIA",
         "detalhe": "isolamento por inquilino e exposição de dados em revisão"},
    ]
    bloqueado = not ambiente["LIVE"]["habilitado"]
    return {
        "escopo": "PLATAFORMA",
        "nao_e": ("prova de prontidão operacional (operational readiness); "
                  "consulte o estado por inquilino, autenticado, em "
                  "/v1/operational-readiness"),
        # PAPER é POLÍTICA ATUAL, não detecção: enquanto só ele estiver
        # autorizado, o valor é fixo de propósito. Quando o modo passar a
        # vir do mandato do inquilino, esta origem muda junto — e quem lê
        # precisa saber distinguir as duas coisas.
        "modo_atual": "PAPER",
        "modo_atual_origem": "POLITICA_ATUAL",
        "modos": ambiente,
        "operacao_real": {
            "disponivel": not bloqueado,
            "estado": "BLOQUEADA" if bloqueado else "LIBERADA",
            "motivo": ("auditoria de segurança em andamento" if bloqueado
                       else "gates aprovados"),
        },
        "gates": gates,
    }


@app.get("/v1/auth/oauth/providers")
def oauth_providers():
    """Público: o painel só mostra o botão do provedor configurado."""
    return {"provedores": [{"nome": p.name, "disponivel": p.configured()}
                           for p in PROVIDERS.values()]}


@app.get("/v1/auth/oauth/{provider}/start")
def oauth_start(provider: str):
    p = PROVIDERS.get(provider)
    if p is None:
        raise HTTPException(404, "provedor desconhecido")
    if not p.configured():
        raise HTTPException(503, f"login com {provider} não configurado no servidor")
    state, verifier = _OAUTH_STATES.new(provider)
    return {"url": authorize_url(p, state, verifier), "state": state}


@app.get("/v1/auth/oauth/{provider}/callback")
def oauth_callback(provider: str, code: str = "", state: str = ""):
    """Retorno do provedor. Sem state válido (uso único) nada acontece."""
    p = PROVIDERS.get(provider)
    if p is None:
        raise HTTPException(404, "provedor desconhecido")
    if not (MULTI_TENANT_ENABLED and TENANT_STORE):
        raise HTTPException(503, "contas multi-tenant desabilitadas neste servidor")
    if not code:
        raise HTTPException(422, "código de autorização ausente")
    try:
        pend = _OAUTH_STATES.consume(state, provider)
        perfil = exchange_code(p, code, pend.verifier, transport=_OAUTH_TRANSPORT)
        email = email_of(perfil)
    except OAuthError as exc:
        raise HTTPException(401, str(exc)) from None

    nome = (perfil.get("name") or email.split("@")[0]).strip()
    senha_interna = secrets.token_urlsafe(32)      # conta social não usa senha
    tenant_id, user_id = TENANT_STORE.find_membership_by_email(email)
    if tenant_id is None:
        if not SIGNUP_ENABLED:
            raise HTTPException(403, "cadastro fechado neste servidor")
        tenant_id = TENANT_STORE.create_tenant(f"Agência de {nome}")
        user_id = TENANT_STORE.create_user(email, senha_interna)
        TENANT_STORE.add_member(tenant_id, user_id, "admin")
        SUBS.ensure(tenant_id)
        token = TENANT_STORE.login(email, senha_interna, tenant_id)
        novo = True
    else:
        token = TENANT_STORE.issue_token_for_user(user_id, tenant_id)
        novo = False
    return {"ok": True, "tenant_id": tenant_id, "access_token": token,
            "email": email, "conta_nova": novo}


# ===================================================================== MESA
# Mesa autônoma multiagente. UMA POR INQUILINO: nenhum tenant enxerga
# agentes, carteira, ordens ou ciclos de outro — isolamento é requisito do
# produto, não detalhe. Modo PAPER; esta superfície não liga capital real.
_DESKS: dict[str, dict] = {}

# Centro Inteligente de Alertas ⑤: fila durável por inquilino. Só o
# despachante grava; o teste injeta transportes por aqui para nunca tocar
# a internet de verdade.
_FILAS_ALERTAS: dict = {}
_TESTE_CANAL_TRANSPORTES: dict = {}


def _nova_fila_alertas(tenant_id: str, armazem=None):
    from tradingagents.desk.notificacao_viva import FilaDeAlertas
    if armazem is None:
        armazem = (_DESKS.get(tenant_id) or {}).get("armazem")
    doc = armazem.carregar(tenant_id, "alertas") if armazem else None
    return FilaDeAlertas.from_dict(doc)


def _fila_alertas(tenant_id: str):
    fila = _FILAS_ALERTAS.get(tenant_id)
    if fila is None:
        fila = _nova_fila_alertas(tenant_id)
        _FILAS_ALERTAS[tenant_id] = fila
    return fila


def _persistir_fila_alertas(tenant_id: str) -> None:
    fila = _FILAS_ALERTAS.get(tenant_id)
    armazem = (_DESKS.get(tenant_id) or {}).get("armazem")
    if fila is None or armazem is None:
        return
    try:
        # Snapshot sob a trava da fila: gravar durante um despacho em outra
        # thread serializaria estados no meio da entrega.
        with fila.trava:
            documento = fila.to_dict()
        armazem.salvar(tenant_id, "alertas", documento)
    except Exception:
        # A fila em memória segue viva; a próxima gravação tenta de novo.
        LOGGER.warning("não foi possível persistir a fila de alertas")


def _matriz_alertas(tenant_id: str):
    from tradingagents.desk.notificacao_viva import MatrizDeRoteamento
    armazem = (_DESKS.get(tenant_id) or {}).get("armazem")
    doc = armazem.carregar(tenant_id, "alertas_matriz") if armazem else None
    return MatrizDeRoteamento.from_dict(doc)


def _push_do_tenant(tenant_id: str):
    """Assinaturas push do dono + entregador VAPID pronto para uso.

    Multi-dispositivo (PWA): cada aparelho tem sua assinatura; o envio usa a
    chave VAPID privada do tenant. Assinatura expirada (404/410 do push
    service) é podada da configuração — nunca silenciosamente reutilizada.
    """
    from tradingagents.desk import push_web
    cfg = load_cfg(tenant_id)
    assinaturas = push_web.carregar_assinaturas(cfg)

    def podar(expiradas):
        ids = {a.identificador() for a in expiradas}
        atual = load_cfg(tenant_id)
        restantes = [a for a in push_web.carregar_assinaturas(atual)
                     if a.identificador() not in ids]
        save_cfg(push_web.salvar_assinaturas(atual, restantes), tenant_id)

    entregador = push_web.EntregadorPush(
        assinaturas,
        vapid_privada=cfg.get("ALERTA_VAPID_PRIVADA", ""),
        vapid_publica=cfg.get("ALERTA_VAPID_PUBLICA", ""),
        podar=podar)
    return assinaturas, entregador


def _persistir_diagnostico_push(tenant_id: str, assinaturas) -> None:
    """Grava no config o diagnóstico REAL da última entrega (sucesso, erro,
    falhas) — o painel recarrega o estado do armazém, não da memória do
    processo. Inscrições podadas nesta rodada não voltam (filtro por id)."""
    from tradingagents.desk import push_web
    try:
        cfg = load_cfg(tenant_id)
        atuais = push_web.carregar_assinaturas(cfg)
        diag = {a.identificador(): (a.ultimo_ok, a.ultimo_erro, a.falhas)
                for a in assinaturas}
        mudou = False
        for atual in atuais:
            valores = diag.get(atual.identificador())
            if valores is None:
                continue
            if (atual.ultimo_ok, atual.ultimo_erro, atual.falhas) != valores:
                atual.ultimo_ok, atual.ultimo_erro, atual.falhas = valores
                mudou = True
        if mudou:
            save_cfg(push_web.salvar_assinaturas(cfg, atuais), tenant_id)
    except Exception:
        # Diagnóstico é melhor-esforço: a entrega já aconteceu de verdade.
        LOGGER.warning("não foi possível persistir o diagnóstico do push")


_CHAVE_TESTES_CANAL = "NOTIF_TESTES_CANAL"


def _testes_canal_do_cfg(cfg: dict) -> dict:
    bruto = cfg.get(_CHAVE_TESTES_CANAL)
    if isinstance(bruto, str) and bruto.strip():
        try:
            bruto = json.loads(bruto)
        except ValueError:
            return {}
    if not isinstance(bruto, dict):
        return {}
    return {k: v for k, v in bruto.items() if isinstance(v, dict)}


def _testes_canal(tenant_id: str) -> dict:
    return _testes_canal_do_cfg(load_cfg(tenant_id))


def _registrar_teste_canal(tenant_id: str, nome: str, resultado: dict) -> None:
    """Registra o resultado do teste — inclusive FALHA — no config do
    inquilino: sobrevive ao reinício e nunca vira sucesso silencioso."""
    from tradingagents.desk.models import utcnow
    try:
        cfg = load_cfg(tenant_id)
        doc = _testes_canal_do_cfg(cfg)
        doc[nome] = {"em": utcnow().isoformat(),
                     "ok": bool(resultado.get("ok")),
                     "detalhe": str(resultado.get("detalhe")
                                    or resultado.get("erro") or "")[:160]}
        cfg[_CHAVE_TESTES_CANAL] = json.dumps(doc, ensure_ascii=False)
        save_cfg(cfg, tenant_id)
    except Exception:
        LOGGER.warning("não foi possível registrar o teste do canal")


def _despachar_alertas(tenant_id: str) -> dict:
    """Tenta entregar o que está pendente, respeitando backoff e rate limit."""
    from tradingagents.desk.canais_alerta import construir_canais
    from tradingagents.desk.notificacao_viva import Despachante
    fila = _fila_alertas(tenant_id)
    assinaturas, entregador = _push_do_tenant(tenant_id)
    canais = construir_canais(
        load_cfg(tenant_id),
        transportes=_TESTE_CANAL_TRANSPORTES.get(tenant_id),
        assinaturas_webpush=assinaturas,
        entregador_webpush=entregador)
    despachante = Despachante(fila, canais, matriz=_matriz_alertas(tenant_id))
    resultado = despachante.despachar()
    _persistir_diagnostico_push(tenant_id, assinaturas)
    _persistir_fila_alertas(tenant_id)
    return resultado


def _enfileirar_estado_da_mesa(tenant_id: str, alerta) -> None:
    _fila_alertas(tenant_id).enfileirar(
        tipo=alerta.codigo, severidade=alerta.severidade, titulo=alerta.titulo,
        detalhe=alerta.detalhe, acao=alerta.acao, fonte="mesa",
        dedupe_key=f"mesa:{alerta.codigo}")


def _rotulo_modo_execucao(broker_mode: str) -> str:
    """Rótulo HONESTO do modo: o alerta nunca pode sugerir corretora real
    quando a ordem saiu da simulação (achado N01: o texto dizia 'executou
    na corretora' para ordem PAPER)."""
    modo = (broker_mode or "paper").strip().lower()
    if modo == "live":
        return "REAL (LIVE)"
    if modo == "testnet":
        return "TESTNET (sem dinheiro real)"
    return "SIMULADO (PAPER)"


def montar_alerta_entrada(ordem_id: str, broker_mode: str) -> dict:
    """Texto do alerta de entrada conforme o MODO REAL da ordem."""
    modo = (broker_mode or "paper").strip().lower()
    if modo == "live":
        detalhe = "Ordem de compra executada na corretora REAL (LIVE)."
    elif modo == "testnet":
        detalhe = ("Ordem de compra executada no TESTNET da corretora — "
                   "ambiente de teste, sem dinheiro real.")
    else:
        detalhe = ("Ordem de compra executada na SIMULAÇÃO (paper) — "
                   "não foi enviada à corretora real.")
    return {"titulo": (f"[{_rotulo_modo_execucao(modo)}] "
                       f"Entrada confirmada: {ordem_id}"),
            "detalhe": detalhe,
            "acao": "A posição já aparece no gerente de saída global."}


def _alertas_do_ciclo(tenant_id: str, out: dict) -> None:
    """E2.5: saídas executadas e entradas confirmadas viram alerta."""
    from datetime import timedelta as _td

    from tradingagents.desk.models import utcnow
    fila = _fila_alertas(tenant_id)
    modo = str((_DESKS.get(tenant_id) or {}).get("broker_mode") or "paper")
    rotulo = _rotulo_modo_execucao(modo)
    for enc in out.get("encerradas", []) or []:
        if enc.get("estado") != "filled":
            continue
        motivo = str(enc.get("motivo") or enc.get("reason_code") or "")
        code = str(enc.get("reason_code") or "")
        tipo = "stop_atingido" if "STOP" in code.upper() else "saida_executada"
        fila.enfileirar(
            tipo=tipo, severidade="atencao",
            titulo=f"[{rotulo}] Saída executada: {enc.get('instrumento', '?')}",
            detalhe=(f"quantidade {enc.get('quantidade', '?')} a "
                     f"{enc.get('preco', '?')}" + (f" — {motivo}" if motivo else "")),
            acao="Confira a posição na aba Mesa de Agentes.",
            fonte=str(enc.get("fonte") or "mesa"), preco=str(enc.get("preco") or ""),
            dedupe_key=f"saida:{enc.get('ordem') or enc.get('instrumento')}",
            dados={"instrumento": enc.get("instrumento", ""),
                   "agente": enc.get("agente", ""),
                   "reason_code": code},
            validade=utcnow() + _td(hours=12))
    for o in out.get("ordens", []) or []:
        if o.get("estado") != "filled":
            continue
        aviso = montar_alerta_entrada(str(o.get("ordem", "")), modo)
        fila.enfileirar(
            tipo="confirmacao_entrada", severidade="informativo",
            titulo=aviso["titulo"], detalhe=aviso["detalhe"],
            acao=aviso["acao"],
            fonte="mesa", dedupe_key=f"entrada:{o.get('ordem')}",
            validade=utcnow() + _td(hours=12))


# Copiloto Manual (E3): modo PRÓPRIO do inquilino — não é estado da mesa.
# Read-only estrutural perante corretoras: este bloco não importa broker,
# não chama create_order/cancel_order e nenhuma rota daqui executa ordem.
_COPILOTOS: dict = {}


def _armazem_do_tenant(tenant_id: str):
    d = _DESKS.get(tenant_id) or {}
    armazem = d.get("armazem")
    if armazem is not None:
        return armazem
    from tradingagents.desk.storage import ArmazemDeEstado
    return ArmazemDeEstado(os.environ.get("DATABASE_URL"),
                           diretorio=DATA / "desks")


def _copiloto(tenant_id: str, armazem=None):
    from tradingagents.desk.manual import CopilotoManual
    cop = _COPILOTOS.get(tenant_id)
    if cop is None:
        armazem = armazem or _armazem_do_tenant(tenant_id)
        doc = armazem.carregar(tenant_id, "manual")
        cop = CopilotoManual.from_dict(doc)
        _COPILOTOS[tenant_id] = cop
    return cop


def _persistir_copiloto(tenant_id: str) -> None:
    cop = _COPILOTOS.get(tenant_id)
    if cop is None:
        return
    try:
        _armazem_do_tenant(tenant_id).salvar(tenant_id, "manual", cop.to_dict())
    except Exception:
        # O estado em memória segue vivo; a próxima gravação tenta de novo.
        LOGGER.warning("não foi possível persistir o copiloto manual")


def _tique_do_copiloto(tenant_id: str, cop, snaps_fn=None) -> dict:
    """Um tique do monitor manual com o MESMO feed da mesa.

    Read-only estrutural: compara preço com a política e ALERTA pela fila
    durável (E2). Nunca executa ordem, nunca fecha posição — alvo atingido
    vira alerta, não encerramento.
    """
    if snaps_fn is None:
        snaps_fn = (_DESKS.get(tenant_id) or {}).get("snaps_fn")
    if snaps_fn is None:
        return {}
    from tradingagents.desk.manual import QuoteManual
    from tradingagents.desk.models import utcnow
    try:
        snaps = snaps_fn()
    except Exception as exc:
        LOGGER.warning("copiloto manual: feed indisponível (%s)",
                       type(exc).__name__)
        return {}
    recebido = utcnow()
    quotes = {s.instrument: QuoteManual(
        preco=s.last, cutoff=s.cutoff, recebido_em=recebido, fonte=s.source,
        timeframe=s.timeframe, quality=s.quality) for s in snaps if s.closes}
    fila = _fila_alertas(tenant_id)
    resumo = cop.avaliar_posicoes(quotes,
                                  enfileirar=lambda **kw: fila.enfileirar(**kw))
    _persistir_copiloto(tenant_id)
    if resumo.get("alertas"):
        _persistir_fila_alertas(tenant_id)
        with contextlib.suppress(Exception):
            _despachar_alertas(tenant_id)
    return resumo


def _copiloto_para_painel(cop) -> dict:
    abertas = cop.posicoes_abertas()
    return {"ativo": cop.ativo,
            "total_abertas": len(abertas),
            "posicoes_abertas": [p.para_painel() for p in abertas],
            "avisos": (["modo copiloto ligado: a mesa autônoma não executa "
                        "ordens enquanto ele acompanha"] if cop.ativo else []),
            "como_usar": ("Registre aqui o que você JÁ executou na sua "
                          "corretora. O copiloto acompanha, calcula e alerta "
                          "— ele nunca opera por você."),
            "monitor": cop.saude_do_monitor()}


def _revisor_de_ia(tenant_id: str, cfg: dict):
    """Revisor do diário manual: usa a credencial já configurada pelo dono.

    Devolve None sem provedor — sem IA o diário continua vivo (a revisão é
    opcional por desenho). O texto da IA comenta os FATOS; ela nunca recebe
    permissão de inventar preço, alvo ou ordem.
    """
    if not cfg.get("TRADINGAGENTS_LLM_PROVIDER"):
        return None

    def revisar(fatos: dict) -> str:
        # Smart Setup (R7): com o bloqueio de orçamento ligado pelo dono, a
        # revisão por IA é a chamada que para — o diário continua vivo e a
        # entrada registra o motivo (registrar_revisao captura a exceção).
        motivo_bloqueio = _ia_bloqueio(tenant_id)
        if motivo_bloqueio:
            raise RuntimeError(motivo_bloqueio)
        # V9-SEC-07: credencial VIVA — a foto do boot não pode ressuscitar
        # provedor/chave removidos no painel entre a rota e a revisão.
        cfg_vivo = load_cfg(tenant_id)
        if not cfg_vivo.get("TRADINGAGENTS_LLM_PROVIDER"):
            raise RuntimeError("provedor de IA removido — revisão suspensa")
        from service.api.ia_provedores import consultar_ia

        prompt = (
            "Você revisa o diário de uma operação MANUAL já executada pelo "
            "dono na corretora dele. Comente os fatos abaixo em português, "
            "com objetividade: risco, gestão e disciplina. NUNCA sugira "
            "preço, alvo ou ordem nova e não invente dados que não estão "
            "nos fatos.\n"
            f"Fatos: {json.dumps(fatos, ensure_ascii=False)}")
        texto, meta = consultar_ia(cfg_vivo, prompt)
        with contextlib.suppress(Exception):
            IA_USO.registrar(tenant_id, meta)
        return texto

    return revisar


def _avaliar_escola_da_mesa(desk: dict, tenant_id: str, *,
                            agora: datetime | None = None) -> list[dict]:
    """Avalia evidência fechada da sala e encaminha formados.

    É chamada pelo callback persistido de cada ciclo. Assim, a Escola não
    depende de uma consulta manual do painel para avançar. Formar continua
    significando apenas entrar na fila bloqueada da Sala REAL: nenhum limite,
    autorização, broker ou ordem é criado aqui.
    """
    from tradingagents.desk.escola import EstadoDaEscola
    from tradingagents.desk.piloto import SupervisorDePiloto
    from tradingagents.desk.salas import (
        ContextoDeCertificacao,
        EscolaCentral,
        EscolaDoPiloto,
    )

    sala = desk.get("sala")
    armazem = desk.get("armazem")
    orchestrator = desk.get("orchestrator")
    if sala is None or armazem is None or orchestrator is None:
        return []
    desfechos = list(getattr(sala, "desfechos", ()) or ())
    alunos = sorted({
        (str(d.estrategia), str(d.versao or "1"), str(d.instrumento),
         str(d.horizonte or "1h"))
        for d in desfechos
        if d.estrategia and d.instrumento
    })
    if not alunos:
        return []

    central = EscolaCentral(armazem=armazem, tenant_id=tenant_id)
    resultados = []
    for estrategia, versao, ativo, horizonte in alunos:
        av = central.avaliar(
            sala=sala, estrategia=estrategia, versao=versao,
            ativo=ativo, horizonte=horizonte, agora=agora)
        encaminhamento = None
        if av.estado is EstadoDaEscola.FORMADA:
            candidatos = [
                t for t in orchestrator.traders
                if t.strategy == estrategia
                and str(t.spec.version) == versao
            ]
            if len(candidatos) == 1:
                contexto = ContextoDeCertificacao(
                    corretora=sala.contexto.corretora,
                    conta=sala.contexto.conta,
                    mercado=sala.contexto.mercado,
                    modo=sala.contexto.modo,
                    ativo=ativo, horizonte=horizonte,
                    payout=sala.contexto.payout)
                supervisor = SupervisorDePiloto(
                    armazem, tenant_id=tenant_id,
                    escola=EscolaDoPiloto(central, contexto))
                encaminhamento = supervisor.encaminhar_formado(
                    candidatos[0].spec.agent_id,
                    estrategia=estrategia, versao=versao,
                    contexto=contexto.como_dict(), agora=agora)
            else:
                encaminhamento = {
                    "estado": "nao_encaminhado",
                    "motivo": ("estratégia sem agente único na mesa; nada "
                               "foi liberado automaticamente"),
                }
        resultados.append({
            "estrategia": estrategia, "versao": versao,
            "ativo": ativo, "horizonte": horizonte,
            "estado": av.estado.value, "motivo": av.motivo,
            "amostras": av.amostras,
            "sala_real": encaminhamento,
        })
    return resultados


def _desk_boot(tenant_id: str) -> dict:
    from decimal import Decimal

    from tradingagents.desk.brokers import build_desk_broker
    from tradingagents.desk.execution import ExecutionCoordinator, PositionGuardian
    from tradingagents.desk.ledger import Ledger
    from tradingagents.desk.models import (
        AgentStage,
        DeskMandate,
        MarketSnapshot,
        Mode,
        utcnow as _now,
    )
    from tradingagents.desk.orchestrator import DeskOrchestrator
    from tradingagents.desk.risk import RiskEngine
    from tradingagents.desk.scheduler import DeskScheduler
    from tradingagents.desk.traders import default_desk

    cfg = load_cfg(tenant_id)
    raw = cfg.get("TRADINGAGENTS_DESK_INSTRUMENTS") or "BTC/USDT,ETH/USDT"
    universe = tuple(i.strip().upper() for i in raw.split(",") if i.strip())
    capital = Decimal(str(cfg.get("TRADINGAGENTS_DESK_CAPITAL") or "1000"))
    mandate = DeskMandate(mandate_id=f"desk-{tenant_id}", owner=tenant_id,
                          account=f"paper-{tenant_id}", mode=Mode.PAPER,
                          instruments=universe, capital=capital)
    from tradingagents.desk.learning import MemoriaDeDesempenho
    from tradingagents.desk.modo import ModoDaMesa, PosseDeExecucao
    from tradingagents.desk.storage import ArmazemDeEstado
    # Servidor é descartável; o aprendizado não pode ser. Com banco externo,
    # o histórico dos agentes sobrevive à troca de máquina.
    armazem = ArmazemDeEstado(os.environ.get("DATABASE_URL"),
                              diretorio=DATA / "desks")
    memoria = MemoriaDeDesempenho(DATA / "desks" / tenant_id / "aprendizado.json",
                                  armazem=armazem, tenant_id=tenant_id)
    # Evolução autônoma (E5): diário auditável por trader + experimentos
    # champion/challenger. Promoção automática só em PAPER; bloqueio por
    # deterioração entra no vigia (abaixo). Nunca altera execução/limites.
    from tradingagents.desk.evolucao import EvolucaoAutonoma
    evolucao = EvolucaoAutonoma(DATA / "desks" / tenant_id / "evolucao.json",
                                armazem=armazem, tenant_id=tenant_id,
                                memoria=memoria)
    exchange_cfg = (cfg.get("TRADINGAGENTS_EXCHANGE") or "binance").lower()
    # Trader de controle entra SÓ por opção explícita do dono
    # (TRADINGAGENTS_DESK_CONTROLE=1): é o piso estatístico de comparação,
    # não um trader a mais por padrão.
    incluir_controle = str(cfg.get("TRADINGAGENTS_DESK_CONTROLE") or "") \
        .strip().lower() in ("1", "true", "sim", "on", "yes")
    traders = default_desk(universe, memoria=memoria, exchange=exchange_cfg,
                           capital=capital, incluir_controle=incluir_controle)
    # ESTÁGIO INICIAL (mandato MAESTRO): os três traders preservados são
    # HOMOLOGADOS e operam; os sete novos nascem EXPERIMENTAIS (shadow) e NÃO
    # são habilitados sem evidência estatística. Promoções e pausas decididas
    # pelo dono ficam persistidas e sobrevivem ao reinício.
    estagios_salvos = armazem.carregar(tenant_id, "agentes") or {}
    estagios_antes: dict[str, str] = {}
    for t in traders:
        t.spec.move_to(AgentStage.VALIDATED)
        t.spec.move_to(AgentStage.SHADOW)
        registro = estagios_salvos.get(t.spec.agent_id) or {}
        salvo = registro.get("stage")
        if salvo in ("shadow", "paper_enabled", "paused"):
            if salvo != t.spec.stage.value:
                t.spec.move_to(AgentStage(salvo))
            estagios_antes[t.spec.agent_id] = registro.get("antes") or ""
        elif t.homologado:
            t.spec.move_to(AgentStage.PAPER_ENABLED)

    def _persistir_estagios():
        documento = {t.spec.agent_id: {"stage": t.spec.stage.value,
                                       "antes": estagios_antes.get(t.spec.agent_id, "")}
                     for t in traders}
        with contextlib.suppress(Exception):
            armazem.salvar(tenant_id, "agentes", documento)

    risk = RiskEngine(mandate)
    ledger = Ledger(opening_cash=mandate.capital)
    data_dir = DATA / "desks" / tenant_id
    # A autorização de LIVE é de CÓDIGO, não de configuração. O mandato
    # desta mesa é PAPER (acima) e `live_authorized` só se torna verdadeiro
    # por construção explícita em código auditado — salvar
    # TRADINGAGENTS_BROKER_MODE=live no painel NÃO basta para a mesa.
    # (Achado FIN-1 da auditoria independente.)
    broker, broker_mode = build_desk_broker(
        cfg, final_state={}, journal_path=str(data_dir / "broker.jsonl"),
        paper_price=Decimal("100"), live_authorized=mandate.live_authorized)
    coord = ExecutionCoordinator(mandate, ledger, risk,
                                 worker_id=f"worker-{tenant_id}", broker=broker)
    # UMA referência econômica desde o primeiro boot: sem isto o risco ficava
    # cego ao livro vivo (via mandate.capital e reservas), e o capital já
    # investido parecia disponível para novas entradas. O restore re-liga ao
    # livro reconstruído (execution.py).
    risk.attach_portfolio_ledger(ledger)
    # Identidade e destino do relatório de reconciliação.
    coord.tenant_id = tenant_id
    coord.armazem = armazem
    # A mesa passa a saber o que a corretora PODE provar; sem corretora real,
    # a matriz fica DESCONHECIDA — e desconhecida não vira confiança.
    if broker is not None and hasattr(broker, "capacidades"):
        try:
            coord.capacidades = broker.capacidades()
        except Exception as exc:
            # Falha na detecção NÃO vira confiança: a matriz continua
            # DESCONHECIDA e o motivo fica registrado, em vez de sumir.
            coord.capacidades_erro = f"{type(exc).__name__}: {exc}"[:200]
    # IA de contexto: usa o MESMO provedor que o cliente já configurou.
    # Sem provedor, a mesa opera sem verificação — e o ciclo registra isso.
    analista = None
    if cfg.get("TRADINGAGENTS_LLM_PROVIDER"):
        from tradingagents.desk.context import AnalistaDeContexto

        def _consultar(prompt: str) -> str:
            """Consulta o provedor do INQUILINO (núcleo: ia_provedores).

            O ambiente do processo NÃO é o transporte da credencial: ela vai
            explícita para o cliente, com janela de env curta sob lock —
            fechada assim que o cliente existe. Duas mesas de inquilinos
            diferentes nunca se cruzam. Registra latência e o provedor que
            DE FATO respondeu (com fallback configurado, pode não ser o
            principal). Com o bloqueio de orçamento ligado pelo dono, a
            consulta para ANTES da rede — a mesa segue sem verificação
            (a exceção vira parecer com motivo, no Analista).
            """
            motivo_bloqueio = _ia_bloqueio(tenant_id)
            if motivo_bloqueio:
                raise RuntimeError(
                    f"IA de contexto suspensa — {motivo_bloqueio}")
            # V9-SEC-07: lê a credencial VIVA — chave removida no painel não
            # pode ser ressuscitada pela foto do boot.
            cfg_vivo = load_cfg(tenant_id)
            from service.api.ia_provedores import consultar_ia

            inicio = time.perf_counter()
            ok = False
            provedor_registrado = cfg_vivo.get("TRADINGAGENTS_LLM_PROVIDER")
            try:
                texto, meta = consultar_ia(cfg_vivo, prompt)
                ok = True
                provedor_registrado = meta.get("provedor") or provedor_registrado
                # V8 §6: consumo/custo estimado por inquilino (informativo).
                with contextlib.suppress(Exception):
                    IA_USO.registrar(tenant_id, meta)
                return texto
            finally:
                # Painel (item 7): latência e saúde do provedor externo.
                # Registra o nome que o dono escolheu na tela (openrouter,
                # groq, gemini) — ou o fallback que assumiu quando o
                # principal falhou.
                _registrar_ia_contexto(tenant_id, provedor_registrado,
                                       (time.perf_counter() - inicio) * 1000.0,
                                       ok)

        analista = AnalistaDeContexto(_consultar)

    # BT-10: a mesa do app ganha SALA na escola (espelho financeiro) e
    # VIGIA de pilotos (bloqueio de entradas). LIVE não cria sala por esta
    # camada — operação real exige o fluxo de piloto autorizado.
    from tradingagents.desk.piloto import SupervisorDePiloto
    from tradingagents.desk.salas import ContextoDaSala, EscolaCentral, RegistroDeSalas
    sala_da_mesa = None
    sala_motivo = ""
    if broker_mode in ("paper", "testnet"):
        try:
            sala_da_mesa = RegistroDeSalas(
                armazem=armazem, tenant_id=tenant_id).abrir_sala(
                ContextoDaSala(corretora=exchange_cfg, conta=mandate.account,
                               mercado="spot", modo=broker_mode))
        except Exception as exc:
            sala_motivo = f"sala não registrada ({type(exc).__name__}: {exc})"[:200]
    else:
        sala_motivo = ("modo LIVE não cria sala por esta camada; operação "
                       "real exige o fluxo de piloto autorizado")
    _avisos_vigia: list[str] = []

    def _vigia_pilotos() -> dict[str, str]:
        """Quem o piloto bloqueia AGORA — reavaliado no contexto do pedido.
        Estado RELIDO do armazém a cada ciclo (instâncias novas): autorização
        e formatura mudam por fora deste processo, e cache de boot bloquearia
        ou liberaria a entrada errada. Falha vira bloqueio (o orquestrador já
        trata vigia quebrado como fail-closed); o aviso fica para auditoria."""
        try:
            sup = SupervisorDePiloto(armazem, tenant_id=tenant_id)
            viva = EscolaCentral(armazem=armazem, tenant_id=tenant_id)
            agentes_esperados = tuple(t.spec.agent_id for t in traders)
            bloqueados = sup.bloqueios(
                central=viva, exigir_autorizacao=(broker_mode == "live"),
                agentes_esperados=agentes_esperados)
            if broker_mode == "live":
                cfg_vivo = load_cfg(tenant_id)
                prova, prova_valida = _prova_leitura_real_armazem(
                    armazem, tenant_id, cfg_vivo)
                bloqueados.update(_bloqueios_de_conta_real(
                    sup, prova, prova_valida))
            # Evolução (E5): trader suspenso por deterioração bloqueia novas
            # entradas — o motivo do piloto vence quando os dois apontam.
            evo_viva = EvolucaoAutonoma(armazem=armazem, tenant_id=tenant_id)
            for agent_id, motivo in evo_viva.bloqueios().items():
                bloqueados.setdefault(agent_id, motivo)
            return bloqueados
        except Exception as exc:
            _avisos_vigia.append(f"{type(exc).__name__}: {exc}"[:160])
            del _avisos_vigia[:-10]
            raise

    orch = DeskOrchestrator(mandate, traders, worker_id=f"worker-{tenant_id}",
                            risk=risk,
                            positions_provider=lambda: coord.ledger.open_positions(),
                            memoria=memoria, exchange=exchange_cfg,
                            analista=analista, vigia=_vigia_pilotos,
                            sala=sala_da_mesa,
                            saidas_em_curso=coord.tem_saida_em_curso,
                            evolucao=evolucao)

    feed_kind = (cfg.get("TRADINGAGENTS_DESK_FEED") or "fixture").strip().lower()
    if feed_kind == "real":
        from tradingagents.desk.market import MarketFeedProviders, PublicMarketFeed
        prov = MarketFeedProviders(
            PublicMarketFeed(cfg.get("TRADINGAGENTS_EXCHANGE") or "binance"),
            universe, timeframe="1h", limit=240)
        snaps_fn, prices_fn = prov.snapshots, prov.prices
    else:
        def snaps_fn():
            """Fixture ROTULADA: prova a coordenação da mesa, não o mercado."""
            alta = tuple(Decimal(str(100 + i * 1.5)) for i in range(40))
            # cenário de reversão: precisa de histórico suficiente para o
            # trader LER o mercado — série curta vira abstenção, e com razão.
            queda = tuple([Decimal(str(100 + (i % 3) * 0.3)) for i in range(36)]
                          + [Decimal("88"), Decimal("86"), Decimal("84"),
                             Decimal("82")])
            return [MarketSnapshot(inst, "1h", _now(), alta if n == 0 else queda,
                                   source="fixture")
                    for n, inst in enumerate(universe)]

        def prices_fn():
            # Preço de execução vem do MESMO dado da decisão.
            return {s.instrument: s.last for s in snaps_fn() if s.closes}

    if hasattr(broker, "price_provider"):
        broker.price_provider = prices_fn

    # Estado financeiro anterior: restaurar e EXIGIR reconciliação antes de
    # qualquer exposição nova (UNKNOWN => BLOCK).
    recuperacao = coord.restaurar_estado(armazem.carregar(tenant_id, "oms"))

    # Diário de gestão (políticas, trailing, parciais): sem restaurar, a
    # posição sobreviveria ao reinício mas o GERENTE de saída morreria com o
    # processo. A verdade financeira continua sendo o diário (ledger vence).
    restauracao_livro = orch.restaurar_livro(
        armazem.carregar(tenant_id, "livro"), ledger=coord.ledger)

    # Centro Inteligente de Alertas ⑤: a fila durável é carregada agora; o
    # que ficou pendente no processo anterior volta a ser tentado (ciclo ou
    # rota) — alerta não morre junto com o processo.
    _FILAS_ALERTAS[tenant_id] = _nova_fila_alertas(tenant_id, armazem)

    # Modo da mesa e posse exclusiva de execução (#29, portabilidade): o
    # dono decide o que a mesa faz; quem segura a posse é quem submete ordem.
    modo = ModoDaMesa(armazem, tenant_id)
    posse = PosseDeExecucao(armazem, tenant_id)

    # Copiloto Manual (E3): estado próprio do inquilino, carregado do mesmo
    # armazém. Ligado, a mesa só observa — quem opera é o dono.
    copiloto = _copiloto(tenant_id, armazem)

    desk = {"orchestrator": orch, "ledger": coord.ledger, "coordinator": coord,
            "memoria": memoria, "evolucao": evolucao,
            "armazem": armazem, "recuperacao": recuperacao,
            "livro_restauracao": restauracao_livro,
            "guardian": PositionGuardian(coord), "cycles": [],
            "feed_kind": feed_kind, "broker_mode": broker_mode,
            "modo": modo, "posse": posse, "sala": sala_da_mesa,
            "sala_motivo": sala_motivo, "avisos_vigia": _avisos_vigia,
            "escola_avaliacoes": [],
            "scheduler": None, "estagios_antes": estagios_antes,
            "persistir_estagios": _persistir_estagios,
            "ia_modelo": cfg.get("TRADINGAGENTS_LLM_PROVIDER") or "",
            "manual": copiloto, "snaps_fn": snaps_fn}

    def _gravar_oms():
        armazem.salvar(tenant_id, "oms", coord.snapshot_estado())

    # WAL do OMS (FIN-08): gravação SÍNCRONA ligada ao coordenador. Aqui a
    # falha LEVANTA de propósito — o coordenador recusa a ordem que a
    # gravação não conseguiu tornar durável antes do envio.
    coord.persistencia = _gravar_oms

    def _persistir_oms():
        try:
            _gravar_oms()
        except Exception:
            HEALTH["avisos"].append(
                "não foi possível persistir o estado financeiro da mesa")

    def _record(out):
        _persistir_oms()
        # Escola automática: operações FECHADAS do ciclo alimentam a régua
        # contextual e um formando entra imediatamente na Sala REAL bloqueada.
        # Falha da Escola é observável, mas nunca derruba nem libera a mesa.
        try:
            avaliacoes = _avaliar_escola_da_mesa(desk, tenant_id)
            desk["escola_avaliacoes"] = avaliacoes
            out["escola"] = avaliacoes
        except Exception as exc:
            aviso = ("avaliação automática da Escola falhou: "
                     f"{type(exc).__name__}: {exc}")[:240]
            _avisos_vigia.append(aviso)
            del _avisos_vigia[:-10]
            out["escola_erro"] = aviso
        # A cada ciclo, avalia e ENTREGA alertas pela fila durável — é o que
        # acorda o dono sem ele precisar abrir o painel. A entrega tem
        # dedupe, backoff e rate limit; falha não derruba o ciclo.
        try:
            from tradingagents.desk.alerts import avaliar_estado
            coord_, led_, sched_ = desk["coordinator"], desk["ledger"], desk["scheduler"]
            corretora_ = getattr(coord_.broker, "bridge", None)
            alertas = avaliar_estado(
                pendencias_reconciliacao=[r.order_id for r in coord_.pending_reconciliation()],
                contabilidade_balanceada=led_.is_balanced(),
                perda_hoje=getattr(corretora_, "realized_loss_today", Decimal("0")),
                limite_perda=getattr(corretora_, "max_daily_loss", Decimal("0")),
                mesa_deveria_rodar=True, mesa_rodando=True,
                erros_do_ciclo=sched_.state.errors if sched_ else 0)
            for alerta in alertas:
                _enfileirar_estado_da_mesa(tenant_id, alerta)
            _alertas_do_ciclo(tenant_id, out)
            resultado = _despachar_alertas(tenant_id)
            if resultado.get("falhas"):
                METRICS.inc("alerts.delivery_failed", len(resultado["falhas"]))
                LOGGER.warning("alert delivery failed: %s", len(resultado["falhas"]))
        except Exception as exc:
            METRICS.inc("alerts.delivery_callback_failed")
            LOGGER.exception("alert callback failed: %s", type(exc).__name__)
            # O alerta não derruba o ciclo, mas a falha permanece observável.
        desk["cycles"].append({"em": _now().isoformat(), "origem": feed_kind,
                               "resumo": out.get("resumo", ""),
                               "propostas": len(out.get("propostas") or []),
                               "vetos": out.get("vetos", []),
                               "abstencoes": out.get("abstencoes", []),
                               "erros": out.get("erros", []),
                               "descartados": out.get("descartados", []),
                               "ordens": out.get("ordens", []),
                               "encerradas": out.get("encerradas", [])})
        del desk["cycles"][:-30]

    def _guarda_operacao():
        """Porteiro do laço + batimento do copiloto manual.

        O ciclo em observação NÃO chama on_cycle (não grava estado da mesa),
        então o tique do monitor manual vive aqui — uma vez por volta do
        laço, em qualquer desfecho da guarda. Falha do tique nunca derruba
        o laço nem muda o desfecho da guarda.
        """
        acao, motivo = _guarda_do_laco(modo, posse, copiloto=copiloto)
        if copiloto.ativo or copiloto.posicoes_abertas():
            with contextlib.suppress(Exception):
                _tique_do_copiloto(tenant_id, copiloto, snaps_fn)
        return acao, motivo

    desk["scheduler"] = DeskScheduler(
        orch, coord, snapshot_provider=snaps_fn, price_provider=prices_fn,
        state_path=data_dir / "state.json", on_cycle=_record,
        armazem=armazem, tenant_id=tenant_id,
        interval_seconds=int(os.environ.get("DESK_INTERVAL_SECONDS", "300")),
        guarda_operacao=_guarda_operacao)
    return desk


def _guarda_do_laco(modo, posse, copiloto=None) -> tuple[str, str]:
    """Porteiro fail-closed de cada ciclo autônomo (modo + posse).

    observador: analisa sem executar; pausada/bloqueada: nada roda; ativa:
    só executa com a posse — se outro processo (Oracle) a segura, este fica
    em silêncio até o vencimento. Guarda quebrado NÃO libera: falha = bloquear.
    Copiloto Manual ligado: a mesa autônoma NÃO executa — só observa; quem
    opera é o dono, e o copiloto acompanha o que ele declarou.
    """
    try:
        if copiloto is not None and getattr(copiloto, "ativo", False):
            return "observar", ""
        estado = modo.estado
        if estado is EstadoDaMesa.OBSERVADOR:
            return "observar", ""
        ok, motivo = modo.pode_executar()
        if not ok:
            return "bloquear", motivo
        ok, quem = posse.assumir()
        if not ok:
            return "bloquear", (f"posse de execução pertence a {quem} — outro "
                                "processo está executando esta mesa")
        return "executar", ""
    except Exception as exc:
        return "bloquear", f"guarda de modo/posse falhou ({type(exc).__name__})"


def _guarda_do_pedido(d: dict) -> None:
    """Antes de executar sob demanda: modo ativo E posse livre/desta máquina.

    Observador passa daqui sem tocar na posse (quem só olha não trava a
    execução de quem opera); quem submete ordem, assume a posse exclusiva.
    Copiloto Manual ligado bloqueia: no modo do dono, a mesa não opera."""
    cop = d.get("manual")
    if cop is not None and getattr(cop, "ativo", False):
        raise HTTPException(
            409, "modo Copiloto Manual ligado — a mesa autônoma não executa "
                 "ordens enquanto o copiloto acompanha suas operações "
                 "manuais (desligue o modo para devolver a mesa ao normal)")
    estado = d["modo"].estado
    if estado is EstadoDaMesa.OBSERVADOR:
        return                       # a rota decide o ciclo de observação
    ok, motivo = d["modo"].pode_executar()
    if not ok:
        raise HTTPException(409, motivo)
    ok, quem = d["posse"].assumir()
    if not ok:
        raise HTTPException(
            409, f"posse de execução pertence a {quem} — pare o outro "
                 "processo ou aguarde o vencimento (15 min sem renovação)")


def _desk_of(request: Request) -> dict:
    tid = request_tenant(request)
    parada = _parada(tid)
    if parada.bloqueia:
        raise HTTPException(
            423, f"parada de emergência {parada.estado.estado}: "
                 f"{parada.estado.motivo or 'acionada'} — reconcilie e libere "
                 "antes de operar novamente")
    desk = _DESKS.get(tid)
    if desk is None:
        try:
            desk = _desk_boot(tid)
        except BrokerSecurityError as exc:
            # Recusa deliberada da mesa (hoje: modo LIVE sem liberação de
            # código). Deixar virar 500 mudo esconderia do dono a razão —
            # e a razão é acionável: voltar para testnet.
            raise HTTPException(409, f"mesa recusou arrancar: {exc}"[:400]) from exc
        _DESKS[tid] = desk
    return desk


def _desk_reset(tenant_id: str) -> None:
    """Configuração mudou: parada controlada e reconstrução no próximo uso."""
    desk = _DESKS.pop(tenant_id, None)
    if desk and desk.get("scheduler") and desk["scheduler"].running:
        desk["scheduler"].stop()


_ROTULO_ESTAGIO = {"registered": "pesquisa", "validated": "validado",
                   "shadow": "experimental (paper)",
                   "paper_enabled": "aprovado (paper)",
                   "paused": "pausado", "retired": "bloqueado"}


def _agente_para_painel(t, d: dict) -> dict:
    """Linha do painel Mesa de Agentes: estado em PT-BR, evidência estatística
    e o motivo do último NO_TRADE — para o leigo, tão importante quanto a
    ordem enviada."""
    memoria = d.get("memoria")
    ok, motivo = (memoria.pode_promover(t.strategy) if memoria
                  else (False, "memória indisponível"))
    st = memoria.estatistica(t.strategy) if memoria else None
    ultimo = ""
    if d.get("cycles"):
        ciclo = d["cycles"][-1]
        for linha in ciclo.get("abstencoes", []):
            if linha.startswith(t.spec.agent_id + " · "):
                ultimo = linha.split(": ", 1)[-1]
                break
        if not ultimo:
            for linha in ciclo.get("vetos", []):
                if linha.startswith(t.spec.agent_id + ": "):
                    ultimo = linha.split(": ", 1)[-1]
                    break
    provedor = d.get("ia_modelo") or ""
    return {
        "id": t.spec.agent_id, "strategy": t.spec.strategy,
        "version": t.spec.version, "stage": t.spec.stage.value,
        "stage_label": _ROTULO_ESTAGIO.get(t.spec.stage.value, t.spec.stage.value),
        "tier": "homologado" if t.homologado else "experimental",
        "universe": list(t.universe), "timeframe": t.timeframe, "needs": t.needs,
        "regimes": [r.value for r in t.regimes_favoraveis],
        "modelo_ia": (f"{provedor} · contexto (veta ou reduz; nunca autoriza)"
                      if provedor else "desligado (núcleo determinístico)"),
        "evidencia": {
            "operacoes": st.amostras if st else 0,
            "maduro": bool(st and st.maduro),
            "taxa_acerto": round(st.taxa_acerto, 3) if st else 0.0,
            "retorno_medio": round(st.retorno_medio, 5) if st else 0.0,
            "elegivel": ok, "motivo": motivo},
        "ultimo_no_trade": ultimo,
    }


def _decisao_da_mesa(d) -> dict:
    """Decisão do ciclo (V8.1 §4) publicada no painel.

    Determinística e sem I/O de mercado; o kill switch (parada de
    emergência) barra ANTES, com 423 — quando esta função roda, a mesa
    já passou pela guarda. A perda do dia vem do freio durável do
    coordenador; limite só existe quando configurado (LIVE) — sem limite
    configurado não há bloqueio por este critério.
    """
    from tradingagents.desk.decisao import decidir_do_ciclo

    modo = d["modo"].resumo()
    posse = d["posse"].resumo()
    coord, led = d["coordinator"], d["ledger"]
    perda = Decimal("0")
    with contextlib.suppress(Exception):
        # freio indisponível: não inventa perda
        perda = Decimal(str(coord.estado_do_dia().get("perda_do_dia") or "0"))
    ponte = getattr(coord.broker, "bridge", None)
    limite = getattr(ponte, "max_daily_loss", None) or Decimal("0")
    return decidir_do_ciclo(
        mesa_ativa=bool(modo.get("pode_executar")),
        mesa_estado=str(modo.get("estado") or ""),
        motivo_do_modo=str(modo.get("motivo") or ""),
        posse_sou_eu=bool(posse.get("sou_eu")),
        posse_dono=str(posse.get("dono") or ""),
        pendencias_de_reconciliacao=len(coord.pending_reconciliation()),
        contabilidade_balanceada=bool(led.is_balanced()),
        perda_do_dia_atingida=bool(Decimal(str(limite)) > 0
                                   and perda >= Decimal(str(limite))),
        ultimo_ciclo=(d["cycles"][-1] if d.get("cycles") else None),
    )


@app.get("/v1/desk/status")
def desk_status(request: Request, _=Depends(auth), __=Depends(require_plan)):
    try:
        d = _desk_of(request)
    except HTTPException:
        raise                     # 423 da parada de emergência precisa chegar íntegro
    except Exception as exc:
        raise HTTPException(503, f"mesa indisponível: {type(exc).__name__}: {exc}"[:300]) from exc
    orch, led, coord = d["orchestrator"], d["ledger"], d["coordinator"]
    from tradingagents.desk.execution import latencias_ms
    return {
        "mandate": {"account": orch.mandate.account, "mode": orch.mandate.mode.value,
                    "capital": str(orch.mandate.capital),
                    "instruments": list(orch.mandate.instruments)},
        "agents": [_agente_para_painel(t, d) for t in orch.traders],
        "risk": {"reservado": str(orch.risk.reserved_total),
                 "disponivel": str(orch.risk.available),
                 "instrumentos_abertos": sorted(orch.risk.open_instruments())},
        # MO-2-C: carimbos UTC por estágio do OMS ("" = desconhecido, ex.:
        # ordem restaurada de documento antigo) + latências derivadas.
        "orders": [{"id": r.order_id, "instrumento": r.instrumento,
                    "estado": r.state.value, "qtd": str(r.filled_qty),
                    "preco": str(r.avg_price), "nota": r.note,
                    "criada_em": r.criada_em, "enviada_em": r.enviada_em,
                    "reconhecida_em": r.reconhecida_em,
                    "primeiro_fill_em": r.primeiro_fill_em,
                    "terminal_em": r.terminal_em,
                    "atualizada_em": r.atualizada_em,
                    "latencias_ms": latencias_ms(r)}
                   for r in coord.orders.values()],
        "pendencias_reconciliacao": [r.order_id for r in coord.pending_reconciliation()],
        "contabilidade": {"saldos": {k: str(v) for k, v in led.balances().items()},
                          "patrimonio": str(led.equity()),
                          "atribuicao": {k: str(v) for k, v in led.attribution().items()},
                          "balanceado": led.is_balanced()},
        "guardiao": d["guardian"].watchlist(),
        "posicoes": orch.livro_para_painel(),
        "livro_restauracao": d.get("livro_restauracao", {}),
        "agendador": d["scheduler"].status(),
        "modo": {**d["modo"].resumo(), "posse": d["posse"].resumo()},
        "decisao": _decisao_da_mesa(d),
        "escola": {"sala": (d["sala"].resumo() if d.get("sala") else None),
                   "motivo": d.get("sala_motivo", ""),
                   "avaliacoes_automaticas": d.get("escola_avaliacoes", []),
                   "avisos_vigia": d.get("avisos_vigia", [])[-5:]},
        "durabilidade": d["armazem"].estado() if d.get("armazem") else {},
        "fonte": {"dados": d["feed_kind"], "corretora": d["broker_mode"],
                  "aviso": ("mercado real (leitura pública, cutoff na vela fechada)"
                            if d["feed_kind"] == "real" else
                            "dados de FIXTURE — provam a mesa, não o mercado")},
        "ciclos": d["cycles"][-10:],
    }


@app.post("/v1/desk/cycle")
def desk_cycle(request: Request, _=Depends(auth), __=Depends(require_plan)):
    d = _desk_of(request)
    if d["modo"].estado is EstadoDaMesa.OBSERVADOR:
        # Observador: ciclo PURO — analisa e propõe, não submete, não grava
        # o estado financeiro (quem executa é quem grava).
        out = d["scheduler"].run_once(executar=False, persistir=False)
        return {"ok": True, "modo": "observador",
                "resumo": out.get("resumo", ""),
                "propostas": out.get("propostas", []),
                "escola": [],
                "ordens": [],       # explícito: nada foi enviado
                "encerradas": [],   # explícito: nenhuma saída executada
                "descartados": out.get("descartados", []),
                "vetos": out.get("vetos", []), "abstencoes": out.get("abstencoes", []),
                "erros": out.get("erros", [])}
    _guarda_do_pedido(d)
    out = d["scheduler"].run_once()
    return {"ok": True, "resumo": out.get("resumo", ""),
            "ordens": out.get("ordens", []),
            "encerradas": out.get("encerradas", []),
            "escola": out.get("escola", []),
            "descartados": out.get("descartados", []),
            "vetos": out.get("vetos", []), "abstencoes": out.get("abstencoes", []),
            "erros": out.get("erros", [])}


@app.post("/v1/desk/scheduler/{action}")
def desk_scheduler(action: str, request: Request, _=Depends(auth), __=Depends(require_plan)):
    """Liga/desliga o laço autônomo. Ligado, roda sozinho — fechar o
    navegador não interrompe nada. Start reconcilia ANTES de retomar —
    e respeita o modo e a posse (em observador, o laço só analisa)."""
    d = _desk_of(request)
    sched = d["scheduler"]
    if action == "start":
        _guarda_do_pedido(d)
        recuperacao = sched.recover()
        return {"ok": True, "iniciado": sched.start(), "recuperacao": recuperacao,
                "status": sched.status()}
    if action == "stop":
        sched.stop()
        return {"ok": True, "parada": "controlada — posições seguem sob o guardião",
                "a_encerrar": d["orchestrator"].drain(), "status": sched.status()}
    raise HTTPException(422, "ação deve ser start ou stop")


@app.get("/v1/desk/modo")
def desk_modo_obter(request: Request, _=Depends(auth)):
    """Modo da mesa + quem segura a posse de execução — visível ao dono."""
    d = _desk_of(request)
    return {**d["modo"].resumo(), "posse": d["posse"].resumo()}


@app.post("/v1/desk/modo")
async def desk_modo_definir(req: Request, _=Depends(auth),
                            __=Depends(require_plan)):
    """Muda o modo da mesa. Sair de BLOQUEADA exige desbloqueio explícito."""
    body = await _json_body(req)
    d = _desk_of(req)
    try:
        d["modo"].definir(str(body.get("estado") or ""),
                          motivo=str(body.get("motivo") or ""),
                          por="dono",
                          desbloqueio=bool(body.get("desbloqueio")))
    except ModoBloqueado as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**d["modo"].resumo(), "posse": d["posse"].resumo()}


def _trader_da_mesa(d: dict, agent_id: str):
    return next((t for t in d["orchestrator"].traders
                 if t.spec.agent_id == agent_id), None)


@app.post("/v1/desk/traders/{agent_id}/promover")
def desk_trader_promover(agent_id: str, request: Request,
                         _=Depends(auth), __=Depends(require_plan)):
    """Habilita um trader experimental — SOMENTE com evidência estatística.

    O mandato é explícito: trader sem resultado comprovado não é habilitado.
    O veredito sai das operações FECHADAS (memória), nunca de confiança."""
    d = _desk_of(request)
    t = _trader_da_mesa(d, agent_id)
    if t is None:
        raise HTTPException(404, f"trader {agent_id} não existe nesta mesa")
    if t.spec.stage is AgentStage.PAPER_ENABLED:
        return {"ok": True, "agent_id": agent_id, "stage": t.spec.stage.value,
                "motivo": "já habilitado"}
    ok, motivo = d["memoria"].pode_promover(t.strategy)
    if not ok:
        raise HTTPException(409, f"{agent_id} ainda não elegível: {motivo}")
    t.spec.move_to(AgentStage.PAPER_ENABLED)
    d["estagios_antes"][agent_id] = ""
    d["persistir_estagios"]()
    return {"ok": True, "agent_id": agent_id, "stage": t.spec.stage.value,
            "motivo": motivo}


@app.post("/v1/desk/traders/{agent_id}/pausar")
def desk_trader_pausar(agent_id: str, request: Request,
                       _=Depends(auth), __=Depends(require_plan)):
    """Pausa um trader: ele para de propor; posições abertas seguem sob o
    guardião e o estágio anterior é lembrado para a retomada."""
    d = _desk_of(request)
    t = _trader_da_mesa(d, agent_id)
    if t is None:
        raise HTTPException(404, f"trader {agent_id} não existe nesta mesa")
    if t.spec.stage is AgentStage.PAUSED:
        return {"ok": True, "agent_id": agent_id, "stage": t.spec.stage.value,
                "motivo": "já pausado"}
    antes = t.spec.stage.value
    d["estagios_antes"][agent_id] = antes
    t.spec.move_to(AgentStage.PAUSED)
    d["persistir_estagios"]()
    return {"ok": True, "agent_id": agent_id, "stage": t.spec.stage.value,
            "motivo": f"pausado (estava em {antes}) — posições seguem sob o guardião"}


@app.post("/v1/desk/traders/{agent_id}/retomar")
def desk_trader_retomar(agent_id: str, request: Request,
                        _=Depends(auth), __=Depends(require_plan)):
    """Retoma um trader pausado no estágio que ele tinha — sem pular etapas."""
    d = _desk_of(request)
    t = _trader_da_mesa(d, agent_id)
    if t is None:
        raise HTTPException(404, f"trader {agent_id} não existe nesta mesa")
    if t.spec.stage is not AgentStage.PAUSED:
        raise HTTPException(409, f"{agent_id} não está pausado "
                                 f"(estágio {t.spec.stage.value})")
    antes = d["estagios_antes"].get(agent_id) or "shadow"
    alvo = antes if antes in ("shadow", "paper_enabled") else "shadow"
    t.spec.move_to(AgentStage(alvo))
    d["estagios_antes"][agent_id] = ""
    d["persistir_estagios"]()
    return {"ok": True, "agent_id": agent_id, "stage": t.spec.stage.value,
            "motivo": f"retomado em {alvo}"}


@app.get("/v1/desk/feed")
def desk_feed(request: Request, _=Depends(auth)):
    """Diagnóstico honesto do feed: quantas velas, até quando, e a próxima
    ação concreta quando algo está errado."""
    d = _desk_of(request)
    cfg = load_cfg(request_tenant(request))
    if d["feed_kind"] != "real":
        return {"fonte": "fixture", "estado": "TREINO",
                "proxima_acao": "Em Configuração, Fonte de dados = Mercado real"}
    from tradingagents.desk.market import MarketFeedProviders, PublicMarketFeed
    feed = PublicMarketFeed(cfg.get("TRADINGAGENTS_EXCHANGE") or "binance")
    universe = tuple(d["orchestrator"].mandate.instruments)
    itens = []
    for snap in MarketFeedProviders(feed, universe).snapshots():
        ok = snap.quality == "ok"
        itens.append({"instrumento": snap.instrument, "velas": len(snap.closes),
                      "ate": snap.cutoff.isoformat() if snap.closes else None,
                      "qualidade": snap.quality, "pronto": ok,
                      "proxima_acao": "" if ok else (
                          "servidor sem acesso à exchange (saída bloqueada) ou "
                          "exchange fora do ar — teste outra exchange")})
    return {"fonte": "real", "exchange": cfg.get("TRADINGAGENTS_EXCHANGE") or "binance",
            "instrumentos": itens,
            "estado": "PRONTO" if all(i["pronto"] for i in itens) else "SEM DADOS"}


@app.get("/v1/desk/avaliacao")
def desk_evaluation(request: Request, _=Depends(auth)):
    """Avaliação a partir do que a mesa REALMENTE fez. Sem operações
    suficientes, diz isso — não inventa nota."""
    from decimal import Decimal as D
    d = _desk_of(request)
    led, coord = d["ledger"], d["coordinator"]
    atrib = led.attribution()
    fechadas = [r for r in coord.orders.values() if r.state.value == "filled"]
    por_agente: dict[str, dict] = {}
    for r in fechadas:
        ag = r.intent.reservation.agent_id or "desconhecido"
        e = por_agente.setdefault(ag, {"ordens": 0, "custos": D("0")})
        e["ordens"] += 1
        e["custos"] += r.fees
    linhas = [{"agente": ag, "ordens": e["ordens"], "custos": str(e["custos"]),
               "resultado_realizado": str(atrib.get(ag, D("0")))}
              for ag, e in sorted(por_agente.items())]
    ressalvas = []
    if len(fechadas) < 20:
        ressalvas.append(f"amostra pequena: {len(fechadas)} operações (< 20)")
    if d["feed_kind"] != "real":
        ressalvas.append("dados de fixture — não mede desempenho de mercado")
    if d["broker_mode"] == "paper":
        ressalvas.append("execução simulada: sem fila, slippage ou latência reais")
    if not any(D(x["resultado_realizado"]) != 0 for x in linhas):
        ressalvas.append("nenhuma posição FECHADA: resultado ainda não realizado")
    return {"por_agente": linhas, "conclusivo": not ressalvas, "ressalvas": ressalvas,
            "aviso": "retrospectivo e simulado; não é promessa de lucro"}


def _retrato_da_sala(agent_id: str, request: Request, instrumento: str,
                     timeframe: str) -> dict:
    """Sala individual do trader (TD-2): gráfico de mercado (OHLCV/volume/
    indicadores/S-R/marcações/sinais/abstenções) e gráfico de portfólio
    (curva, P&L, custos, drawdown, exposição, hit rate, expectativa,
    profit factor, Champion/Challenger).

    Leitura pura de fontes verificáveis (velas, diário, livro-razão,
    ciclos da mesa); fixture sempre ROTULADO como fixture. Sem caminho de
    ordem — quem autoriza operação é o dono."""
    from tradingagents.desk.market import (
        TIMEFRAME_SECONDS,
        MarketDataError,
        MarketFeedProviders,
        PublicMarketFeed,
    )
    from tradingagents.desk.models import utcnow
    from tradingagents.desk.sala_trader import (
        experimentos_do_trader,
        retrato_do_trader,
        velas_de_rows,
    )
    d = _desk_of(request)
    t = _trader_da_mesa(d, agent_id)
    if t is None:
        raise HTTPException(404, f"trader {agent_id} não existe nesta mesa")
    if timeframe not in TIMEFRAME_SECONDS:
        raise HTTPException(422, "timeframe deve ser um de: "
                            + ", ".join(sorted(TIMEFRAME_SECONDS)))
    universo = tuple(t.universe) or \
        tuple(d["orchestrator"].mandate.instruments)
    if instrumento and instrumento not in universo:
        raise HTTPException(422, "instrumento fora do universo do trader: "
                            f"{instrumento}")
    instrumento = instrumento or (universo[0] if universo else "")

    cfg = load_cfg(request_tenant(request))
    feed_kind = d.get("feed_kind", "fixture")
    velas = None
    fechamentos: list[str] = []
    qualidade = feed_kind
    if feed_kind == "real":
        feed = PublicMarketFeed(cfg.get("TRADINGAGENTS_EXCHANGE") or "binance")
        snap = MarketFeedProviders(feed, [instrumento], timeframe=timeframe,
                                   limit=240).snapshots()[0]
        qualidade = snap.quality
        fechamentos = [str(c) for c in snap.closes]
        try:
            # Mesma leitura pública do snapshot (cache de 30 s do feed):
            # somente leitura, nunca chave; vela em formação descartada.
            rows = feed.ohlcv(instrumento, timeframe, 240)
            velas = velas_de_rows(rows, timeframe, agora=utcnow())
        except MarketDataError:
            velas = None                    # cai para a linha dos fechamentos
        aviso_fonte = ("mercado real (leitura pública, somente leitura) — "
                       + qualidade)
        # MO-2-A: livro L2 público da MESMA corretora — leitura pura, com
        # estado honesto (indisponivel/vencida/nao_suportado) e validade.
        livro = feed.leitura_de_livro(instrumento).para_dict()
    else:
        snaps = [s for s in d["snaps_fn"]() if s.instrument == instrumento]
        if snaps:
            fechamentos = [str(c) for c in snaps[0].closes]
            qualidade = snaps[0].quality
        velas = None
        aviso_fonte = ("dados de FIXTURE (rotulados) — provam a mesa, não o "
                       "mercado; o gráfico mostra os fechamentos, sem OHLCV "
                       "inventado")
        livro = {"estado": "nao_aplicavel",
                 "motivo": "modo dados de treino (fixture) — sem livro de "
                           "ofertas",
                 "somente_leitura": True}

    led, coord = d["ledger"], d["coordinator"]
    evo = d.get("evolucao")
    diario = evo.diario(agent_id) if evo else []
    lotes = [lote for inst in universo for lote in led.position_lots(inst)]
    from tradingagents.desk.execution import latencias_ms
    ordens = [{"id": r.order_id, "agente": r.agente,
               "instrumento": r.instrumento, "lado": r.lado,
               "estado": r.state.value, "qtd": str(r.filled_qty),
               "aberta": r.nao_terminal,
               # MO-2-C: trilha temporal do OMS — carimbo real por estágio
               # ("" = desconhecido, ex.: ordem restaurada de doc antigo)
               "criada_em": r.criada_em, "enviada_em": r.enviada_em,
               "reconhecida_em": r.reconhecida_em,
               "primeiro_fill_em": r.primeiro_fill_em,
               "terminal_em": r.terminal_em,
               "atualizada_em": r.atualizada_em,
               "latencias_ms": latencias_ms(r)}
              for r in coord.orders.values()]
    experimentos = (experimentos_do_trader(evo.experimentos(),
                                           evo.resultados_dos_experimentos(),
                                           agent_id) if evo else [])
    atribuicao = {k: str(v) for k, v in led.attribution().items()}
    return retrato_do_trader(
        agent_id, trader=_agente_para_painel(t, d), instrumento=instrumento,
        timeframe=timeframe, velas=velas, fechamentos=fechamentos,
        fonte=feed_kind, aviso_fonte=aviso_fonte, qualidade=qualidade,
        diario=diario, transacoes=led.export_transactions(), lotes=lotes,
        ordens=ordens, ciclos=d.get("cycles", []), experimentos=experimentos,
        atribuicao=atribuicao,
        moeda=str(getattr(led, "currency", "") or "USDT"),
        modo=d["orchestrator"].mandate.mode.value, livro=livro)


@app.get("/v1/desk/evolucao")
def desk_evolucao(request: Request, _=Depends(auth)):
    """Caderno GLOBAL de experimentos da evolução (AC-1/A3) — leitura pura.

    O caderno por trader já vive na sala dele; aqui o dono vê a mesa
    inteira: desempenho dos traders e o registro de experimentos com
    resultado. Nada de escrita — evolução não altera código, permissões
    nem limites (o aviso viaja junto, sempre)."""
    return _desk_of(request)["evolucao"].relatorio()


@app.get("/v1/desk/traders/{agent_id}/sala")
def desk_trader_sala(agent_id: str, request: Request, _=Depends(auth),
                     instrumento: str = "", timeframe: str = "1h"):
    """Sala individual do trader (TD-2) — leitura pura; sem caminho de
    ordem."""
    return _retrato_da_sala(agent_id, request, instrumento, timeframe)


@app.post("/v1/desk/traders/{agent_id}/sala/leitura")
def desk_trader_sala_leitura(agent_id: str, request: Request,
                             _=Depends(auth), instrumento: str = "",
                             timeframe: str = "1h"):
    """IA lê o gráfico por SÉRIES ESTRUTURADAS (TD-3, task #92).

    Envia os NÚMEROS verificáveis da sala (fechamentos/velas, indicadores,
    níveis, marcações, curva) — nunca imagem, nunca ordem. Sem provedor
    configurado NADA é enviado e a resposta diz o motivo; a leitura é
    opcional por desenho, e o orçamento de IA do dono continua valendo."""
    from tradingagents.desk.sala_trader import prompt_de_leitura
    retrato = _retrato_da_sala(agent_id, request, instrumento, timeframe)
    tenant_id = request_tenant(request)
    cfg = load_cfg(tenant_id)
    if not cfg.get("TRADINGAGENTS_LLM_PROVIDER"):
        return {"ok": False,
                "motivo": ("sem provedor de IA configurado — a leitura por "
                           "IA é opcional; configure em Configuração → IA. "
                           "Nada foi enviado.")}
    motivo_bloqueio = _ia_bloqueio(tenant_id)
    if motivo_bloqueio:
        return {"ok": False, "motivo": motivo_bloqueio}
    from service.api.ia_provedores import consultar_ia
    try:
        texto, meta = consultar_ia(cfg, prompt_de_leitura(retrato))
    except Exception as exc:                    # rede/provedor: falha honesta
        return {"ok": False,
                "motivo": f"falha ao consultar a IA: {type(exc).__name__}"}
    with contextlib.suppress(Exception):
        IA_USO.registrar(tenant_id, meta)
    return {"ok": True, "leitura": texto,
            "provedor": str((meta or {}).get("provedor", "")),
            "aviso": ("leitura da IA sobre as séries estruturadas do "
                      "gráfico (números verificáveis — não é imagem); não é "
                      "promessa de lucro nem previsão exata de preços; quem "
                      "decide é o dono.")}


# ------------------------------------------------- escola central + salas
def _escola_of(request: Request) -> dict:
    """UMA escola central e o catálogo de SALAS do tenant. Construída a cada
    requisição: o estado vive no armazém, não em cache de processo — assim
    duas superfícies (painel e CLI) nunca divergem."""
    from decimal import Decimal as D

    from tradingagents.desk.maestro import MaestroCentral
    from tradingagents.desk.salas import EscolaCentral, RegistroDeSalas
    from tradingagents.desk.storage import ArmazemDeEstado
    tid = request_tenant(request)
    d = _DESKS.get(tid)
    armazem = d.get("armazem") if d else None
    if armazem is None:
        armazem = ArmazemDeEstado(os.environ.get("DATABASE_URL"),
                                  diretorio=DATA / "desks")
    cfg = load_cfg(tid)
    teto = cfg.get("TRADINGAGENTS_DESK_TETO_EXPOSICAO")
    registro = RegistroDeSalas(armazem=armazem, tenant_id=tid)
    central = EscolaCentral(armazem=armazem, tenant_id=tid)
    maestro = MaestroCentral(
        registro, teto_exposicao_global=(D(str(teto)) if teto else None))
    return {"armazem": armazem, "registro": registro, "central": central,
            "maestro": maestro, "tenant_id": tid}


def _contexto_da_sala(body: dict):
    from tradingagents.desk.salas import ContextoDaSala
    return ContextoDaSala(
        corretora=str(body.get("corretora") or ""),
        conta=str(body.get("conta") or ""),
        mercado=str(body.get("mercado") or "spot"),
        modo=str(body.get("modo") or "paper"),
        payout=(body.get("payout") if body.get("payout") not in (None, "")
                else None),
        classe=str(body.get("classe") or ""))


def _sala_por_chave(esc: dict, chave: str):
    """Sala pelo catálogo — abrir é idempotente, chave desconhecida é 404."""
    from tradingagents.desk.salas import SalaError
    if not chave:
        raise HTTPException(422, "informe a sala (chave corretora|conta|mercado|modo)")
    try:
        if chave in esc["registro"].salas:
            return esc["registro"].salas[chave]
    except SalaError as exc:
        raise HTTPException(422, str(exc)) from exc
    raise HTTPException(404, f"sala '{chave}' não existe — abra a sala primeiro")


@app.get("/v1/escola/central")
def escola_central(request: Request, _=Depends(auth)):
    """Relatório da escola central: certificados, estados e justificativas."""
    return _escola_of(request)["central"].relatorio()


@app.get("/v1/escola/salas")
def escola_salas(request: Request, _=Depends(auth)):
    """Salas independentes por corretora/conta/mercado/modo + maestro."""
    from tradingagents.desk.salas import corretoras_homologadas
    esc = _escola_of(request)
    return {"salas": esc["registro"].relatorio(),
            "corretoras": corretoras_homologadas(),
            "maestro": esc["maestro"].relatorio()}


@app.post("/v1/escola/salas")
async def escola_abrir_sala(req: Request, _=Depends(auth),
                            __=Depends(require_plan)):
    """Abre uma sala. Identidades são validadas contra o adaptador
    homologado: mercado errado, laboratório fora de paper ou modo LIVE
    são recusados com motivo — nunca silenciosamente ajustados."""
    from tradingagents.desk.salas import SalaError
    body = await _json_body(req)
    esc = _escola_of(req)
    try:
        sala = esc["registro"].abrir_sala(_contexto_da_sala(body))
    except SalaError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "sala": sala.resumo()}


@app.get("/v1/escola/salas/detalhe")
def escola_sala_detalhe(chave: str, request: Request, _=Depends(auth)):
    """Retrato da sala + priors recebidos (de onde veio, o que afirma)."""
    from tradingagents.desk.conhecimento import resumo_dos_priors
    esc = _escola_of(request)
    sala = _sala_por_chave(esc, chave)
    return {"sala": sala.resumo(), "priors": resumo_dos_priors(sala)}


@app.get("/v1/escola/salas/pacote")
def escola_pacote(chave: str, request: Request, estrategias: str = "",
                  _=Depends(auth)):
    """Exporta o pacote de conhecimento da sala — SÓ estatísticas."""
    from tradingagents.desk.conhecimento import montar_pacote
    esc = _escola_of(request)
    sala = _sala_por_chave(esc, chave)
    filtro = tuple(e.strip() for e in estrategias.split(",") if e.strip())
    return montar_pacote(sala, estrategias=filtro or None).como_dict()


@app.post("/v1/escola/salas/conhecimento")
async def escola_adotar_conhecimento(req: Request, _=Depends(auth),
                                     __=Depends(require_plan)):
    """Adota um pacote como PRIOR da sala de destino. Recusa eco da própria
    sala e qualquer documento com campos de certificação."""
    from datetime import datetime as _dt

    from tradingagents.desk.conhecimento import (
        AVISO_CONHECIMENTO,
        SCHEMA_CONHECIMENTO,
        PacoteDeConhecimento,
        adotar_conhecimento,
        campos_de_certificacao,
    )
    from tradingagents.desk.models import utcnow
    from tradingagents.desk.salas import SalaError
    body = await _json_body(req)
    esc = _escola_of(req)
    sala = _sala_por_chave(esc, str(body.get("chave") or ""))
    doc = body.get("pacote")
    if not isinstance(doc, dict):
        raise HTTPException(422, "informe o pacote de conhecimento")
    proibidos = campos_de_certificacao(doc)
    if proibidos:
        raise HTTPException(
            409, "pacote traz campos de certificação "
                 f"({', '.join(sorted(set(proibidos)))}): certificação não "
                 "atravessa salas — a formação exige evidência própria no "
                 "contexto de destino")
    try:
        gerado_em = _dt.fromisoformat(str(doc.get("gerado_em")))
    except (TypeError, ValueError):
        gerado_em = utcnow()
    pacote = PacoteDeConhecimento(
        origem=str(doc.get("origem") or ""),
        corretora=str(doc.get("corretora") or ""),
        mercado=str(doc.get("mercado") or ""),
        gerado_em=gerado_em,
        estatisticas=tuple(doc.get("estatisticas") or ()),
        aviso=str(doc.get("aviso") or AVISO_CONHECIMENTO),
        schema=str(doc.get("schema") or SCHEMA_CONHECIMENTO))
    try:
        registro = adotar_conhecimento(sala, pacote)
    except SalaError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "prior": registro}


@app.get("/v1/escola/certificacao")
def escola_certificacao(request: Request, estrategia: str, corretora: str,
                        conta: str = "", mercado: str = "spot",
                        modo: str = "", ativo: str = "",
                        horizonte: str = "1h", versao: str = "1",
                        payout: str = "", _=Depends(auth)):
    """Elegibilidade NO CONTEXTO informado. Um trader formado na Binance
    não é considerado formado na OKX — e nem em outra conta, outro modo
    (paper/testnet) ou outro payout: a resposta diz isso com todas as
    letras, em vez de fingir equivalência."""
    from tradingagents.desk.salas import (
        MODO_PAPER,
        ContextoDeCertificacao,
        SalaError,
    )
    esc = _escola_of(request)
    try:
        contexto = ContextoDeCertificacao(
            corretora=corretora, conta=conta, mercado=mercado,
            modo=modo or MODO_PAPER, ativo=ativo, horizonte=horizonte,
            payout=payout or None)
    except SalaError as exc:
        raise HTTPException(422, str(exc)) from exc
    ok, motivo = esc["central"].elegivel_para_live(
        estrategia, versao, contexto=contexto)
    return {"contexto": contexto.chave,
            "estado": esc["central"].estado_de(estrategia, versao,
                                               contexto=contexto),
            "elegivel": ok, "motivo": motivo,
            "aviso": ("elegibilidade habilita apenas o PROCESSO de "
                      "autorização; quem autoriza operação real é o "
                      "proprietário, com limites explícitos")}


@app.post("/v1/escola/avaliar")
async def escola_avaliar(req: Request, _=Depends(auth),
                         __=Depends(require_plan)):
    """Avalia uma estratégia NO CONTEXTO de uma sala: acumula amostras,
    candidata e forma. É o único caminho pelo qual a escola avança — e
    avançar aqui significa elegibilidade, nunca autorização de operar."""
    from tradingagents.desk.escola import EstadoDaEscola
    from tradingagents.desk.piloto import SupervisorDePiloto
    from tradingagents.desk.salas import (
        ContextoDeCertificacao,
        EscolaDoPiloto,
        SalaError,
    )
    body = await _json_body(req)
    esc = _escola_of(req)
    sala = _sala_por_chave(esc, str(body.get("chave") or ""))
    estrategia = str(body.get("estrategia") or "").strip()
    ativo = str(body.get("ativo") or "").strip()
    if not estrategia or not ativo:
        raise HTTPException(422, "estrategia e ativo são obrigatórios")
    try:
        av = esc["central"].avaliar(
            sala=sala, estrategia=estrategia,
            versao=str(body.get("versao") or "1"), ativo=ativo,
            horizonte=str(body.get("horizonte") or "1h"))
    except SalaError as exc:
        raise HTTPException(422, str(exc)) from exc
    encaminhamento = None
    if av.estado is EstadoDaEscola.FORMADA:
        d = _DESKS.get(request_tenant(req))
        candidatos = [] if d is None else [
            t for t in d["orchestrator"].traders
            if t.strategy == estrategia and str(t.spec.version) == av.versao]
        # A fila da Sala REAL não pode depender do cache do processo. Após
        # reinício, a Escola e suas provas continuam no armazém, enquanto
        # `_DESKS` nasce vazio. Reconstruímos somente o catálogo determinístico
        # de agentes padrão (sem iniciar scheduler, broker ou ordem) para que a
        # formatura ainda encontre o agente correto e seja encaminhada.
        if not candidatos:
            from tradingagents.desk.traders import default_desk
            cfg = load_cfg(request_tenant(req))
            incluir_controle = str(
                cfg.get("TRADINGAGENTS_DESK_CONTROLE") or ""
            ).strip().lower() in ("1", "true", "yes", "sim")
            candidatos = [
                t for t in default_desk(
                    (ativo,), exchange=sala.contexto.corretora,
                    incluir_controle=incluir_controle)
                if t.strategy == estrategia
                and str(t.spec.version) == av.versao
            ]
        if len(candidatos) == 1:
            contexto = ContextoDeCertificacao(
                corretora=sala.contexto.corretora,
                conta=sala.contexto.conta,
                mercado=sala.contexto.mercado,
                modo=sala.contexto.modo,
                ativo=ativo,
                horizonte=str(body.get("horizonte") or "1h"),
                payout=sala.contexto.payout)
            sup = SupervisorDePiloto(
                esc["armazem"], tenant_id=esc["tenant_id"],
                escola=EscolaDoPiloto(esc["central"], contexto))
            encaminhamento = sup.encaminhar_formado(
                candidatos[0].spec.agent_id,
                estrategia=estrategia, versao=av.versao,
                contexto=contexto.como_dict())
        else:
            encaminhamento = {
                "estado": "nao_encaminhado",
                "motivo": ("estratégia sem agente único na mesa; nada foi "
                           "liberado automaticamente"),
            }
    return {"ok": True, "avaliacao": {
        "chave": av.chave, "estrategia": av.estrategia, "versao": av.versao,
        "estado": av.estado.value, "motivo": av.motivo,
        "amostras": av.amostras, "n_efetivo": av.n_efetivo,
        "media": str(av.media), "ic_inferior": str(av.ic_inferior),
        "ic_superior": str(av.ic_superior), "aprovada": av.aprovada,
        "atualizada_em": av.atualizada_em},
        "sala_real": encaminhamento,
        "aviso": ("formação habilita apenas o PROCESSO de autorização; "
                  "negociação real continua exclusiva do proprietário")}


@app.get("/v1/escola/maestro")
def escola_maestro(request: Request, _=Depends(auth)):
    """Maestro Central: pausa global, exposição, conflitos entre salas."""
    return _escola_of(request)["maestro"].relatorio()


@app.post("/v1/escola/maestro/{acao}")
async def escola_maestro_acao(acao: str, req: Request, _=Depends(auth),
                              __=Depends(require_plan)):
    """Pausa/retoma GLOBAL das salas. Motivo é obrigatório — auditoria."""
    from tradingagents.desk.maestro import MaestroError
    if acao not in ("pausar", "retomar"):
        raise HTTPException(422, "ação deve ser pausar ou retomar")
    body = await _json_body(req)
    maestro = _escola_of(req)["maestro"]
    try:
        if acao == "pausar":
            maestro.pausar_global(str(body.get("motivo") or ""))
        else:
            maestro.retomar_global(str(body.get("motivo") or ""))
    except MaestroError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, **maestro.relatorio()}


# ------------------------------------------------- painel global (TD-1)
def _moeda_da_sala(sala, resumo: dict) -> str:
    """Moeda da sala pela cadeia VERIFICÁVEL: livro → classe → cripto.
    Nada de chute: sem fonte confirmável, fica não declarada — e moeda
    não declarada nunca entra em soma (sem conversão identificada)."""
    if getattr(sala, "ledger", None) is not None:
        return str(sala.ledger.currency or "")
    from tradingagents.desk.mercados import classe_de
    classe = str(resumo.get("classe") or "")
    if classe:
        try:
            return classe_de(classe).moeda
        except Exception:
            return ""
    ctx = resumo.get("contexto") or {}
    if str(ctx.get("corretora") or "") in ("binance", "bybit", "okx"):
        chave = ("cripto_futuros" if ctx.get("mercado") == "futuros"
                 else "cripto_spot")
        try:
            return classe_de(chave).moeda
        except Exception:
            return ""
    return ""


@app.get("/v1/maestro/painel")
def maestro_painel(request: Request, _=Depends(auth)):
    """Painel Global do Maestro: retrato consolidado da agência INTEIRA.

    PAPER/TESTNET/LIVE segregados; moedas nunca somadas sem conversão
    identificada e verificável; LIVE sempre declarado bloqueado. A mesa
    segue o caminho normal (parada de emergência vira motivo declarado,
    não cegueira: a supervisão global continua visível)."""
    from tradingagents.desk.painel_global import retrato_global
    tid = request_tenant(request)
    esc = _escola_of(request)
    mesa_motivo = ""
    try:
        d = _desk_of(request)
    except HTTPException as exc:
        d = _DESKS.get(tid)
        mesa_motivo = str(exc.detail)
    except Exception as exc:      # mesa indisponível não cega a supervisão
        d = _DESKS.get(tid)
        mesa_motivo = f"{type(exc).__name__}: {exc}"[:200]

    agentes, posicoes, ciclo = [], [], None
    contabilidade: dict = {}
    risco: dict = {}
    fonte: dict = {}
    alertas: list = []
    modo_mesa, moeda_mesa = "paper", "USDT"
    if d is not None:
        orch, led = d["orchestrator"], d["ledger"]
        agentes = [_agente_para_painel(t, d) for t in orch.traders]
        posicoes = orch.livro_para_painel()
        contabilidade = {
            "saldos": {k: str(v) for k, v in led.balances().items()},
            "patrimonio": str(led.equity()), "balanceado": led.is_balanced()}
        risco = {"reservado": str(orch.risk.reserved_total),
                 "disponivel": str(orch.risk.available),
                 "instrumentos_abertos": sorted(orch.risk.open_instruments())}
        modo_mesa = orch.mandate.mode.value
        moeda_mesa = str(getattr(led, "currency", "") or "USDT")
        ciclo = d["cycles"][-1] if d.get("cycles") else None
        fonte = {"dados": d.get("feed_kind", ""),
                 "corretora": d.get("broker_mode", ""),
                 "aviso": ("mercado real (leitura pública, cutoff na vela "
                           "fechada)" if d.get("feed_kind") == "real" else
                           "dados de FIXTURE — provam a mesa, não o mercado")}
        with contextlib.suppress(Exception):
            alertas = [{"codigo": a.codigo, "severidade": a.severidade,
                        "titulo": a.titulo, "detalhe": a.detalhe,
                        "acao": a.acao} for a in _estado_para_alertas(d)]

    salas = []
    for chave, sala in sorted(esc["registro"].salas.items()):
        try:
            r = sala.resumo()
        except Exception:
            continue
        ctx = r.get("contexto") or {}
        salas.append({"chave": r.get("chave") or chave,
                      "corretora": ctx.get("corretora", ""),
                      "conta": ctx.get("conta", ""),
                      "mercado": ctx.get("mercado", ""),
                      "modo": ctx.get("modo", ""),
                      "moeda": _moeda_da_sala(sala, r),
                      "saldo_realizado": r.get("saldo_realizado", "0"),
                      "taxas_acumuladas": r.get("taxas_acumuladas", "0"),
                      "posicoes_abertas": r.get("posicoes_abertas") or {},
                      "laboratorio": r.get("laboratorio"),
                      "tem_ledger": bool(r.get("tem_ledger"))})

    retrato = retrato_global(
        agentes=agentes, posicoes=posicoes, contabilidade=contabilidade,
        salas=salas, ciclo=ciclo, fonte=fonte, alertas=alertas,
        maestro=esc["maestro"].relatorio(), risco=risco,
        moeda_mesa=moeda_mesa, modo_mesa=modo_mesa)
    retrato["mesa_iniciada"] = d is not None
    retrato["mesa_motivo"] = mesa_motivo
    return retrato


# ------------------------------------------------------------ pilotos
def _supervisor_of(request: Request, escola=None):
    """Supervisor de pilotos do tenant. A elegibilidade, quando consultada,
    é SEMPRE de um contexto de certificação explícito."""
    from tradingagents.desk.piloto import SupervisorDePiloto
    esc = _escola_of(request)
    sup = SupervisorDePiloto(esc["armazem"], tenant_id=esc["tenant_id"],
                             escola=escola)
    return sup


@app.get("/v1/escola/pilotos")
def escola_pilotos(request: Request, _=Depends(auth)):
    """Cadeia do piloto por agente + modo do piloto + gates esperados."""
    from tradingagents.desk.piloto import GATES_ESPERADOS, MODO_DO_PILOTO
    esc = _escola_of(request)
    sup = _supervisor_of(request)
    return {"modo": MODO_DO_PILOTO, "gates_esperados": list(GATES_ESPERADOS),
            "autorizacoes": sup.relatorio(),
            "encaminhamentos": sup.encaminhamentos(),
            "vigia": sup.vigiar(central=esc["central"]),
            "aviso": ("a formação não habilita negociação real; autorizar "
                      "piloto é ato exclusivo do proprietário")}


@app.post("/v1/escola/pilotos/solicitar")
async def escola_piloto_solicitar(req: Request, _=Depends(auth),
                                  __=Depends(require_plan)):
    """Registra o PEDIDO do proprietário. Só aceita aluno FORMADO no
    contexto de certificação informado — e isso não autoriza nada ainda."""
    from decimal import Decimal as D

    from tradingagents.desk.piloto import SupervisorDePiloto
    from tradingagents.desk.salas import (
        MODO_PAPER,
        ContextoDeCertificacao,
        EscolaDoPiloto,
        SalaError,
    )
    body = await _json_body(req)

    def _decimal(campo: str) -> D:
        valor = body.get(campo)
        if valor in (None, ""):
            raise HTTPException(422, f"{campo} é obrigatório para o pedido")
        try:
            return D(str(valor))
        except Exception as exc:
            raise HTTPException(422, f"{campo} precisa ser numérico") from exc

    try:
        prazo = int(body.get("prazo_dias") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "prazo_dias precisa ser um número inteiro") from exc
    esc = _escola_of(req)
    try:
        contexto_doc = body.get("contexto_certificacao")
        if isinstance(contexto_doc, dict) and contexto_doc:
            contexto = ContextoDeCertificacao.de_dict(contexto_doc)
        else:
            contexto = ContextoDeCertificacao(
                corretora=str(body.get("corretora") or ""),
                conta=str(body.get("conta") or ""),
                mercado=str(body.get("mercado") or "spot"),
                modo=str(body.get("modo") or MODO_PAPER),
                ativo=str(body.get("ativo") or ""),
                horizonte=str(body.get("horizonte") or "1h"),
                payout=body.get("payout") or None)
    except SalaError as exc:
        raise HTTPException(422, str(exc)) from exc
    corretora_alvo = str(body.get("corretora") or "").strip().lower()
    conta_alvo = str(body.get("conta") or "").strip()
    if not corretora_alvo or not conta_alvo:
        raise HTTPException(422, "corretora e conta de destino são obrigatórias")
    if corretora_alvo != contexto.corretora:
        raise HTTPException(
            409, "a conta REAL deve usar a mesma corretora do contexto que "
                 "certificou a estratégia")
    conta_real = (body.get("conta_real") is True
                  or conta_alvo != contexto.conta)
    broker_identity_fingerprint = ""
    if conta_real:
        cfg = load_cfg(request_tenant(req))
        prova, valida = _prova_leitura_real(req, cfg)
        if not (valida and prova
                and prova.get("authenticated_read")
                and str(prova.get("exchange") or "").strip().lower()
                == corretora_alvo
                and str(prova.get("conta") or "").strip() == conta_alvo):
            raise HTTPException(
                409, "a conta REAL de destino precisa de prova autenticada "
                     "somente leitura válida antes de receber o piloto")
        broker_identity_fingerprint = str(
            prova.get("broker_identity_fingerprint") or "")
    sup = SupervisorDePiloto(
        esc["armazem"], tenant_id=esc["tenant_id"],
        escola=EscolaDoPiloto(esc["central"], contexto))
    try:
        aut = sup.solicitar(
            str(body.get("agent_id") or ""),
            estrategia=str(body.get("estrategia") or ""),
            versao=str(body.get("versao") or "1"),
            corretora=corretora_alvo,
            conta=conta_alvo,
            capital_maximo=_decimal("capital_maximo"),
            perda_diaria_maxima=_decimal("perda_diaria_maxima"),
            perda_total_maxima=_decimal("perda_total_maxima"),
            prazo_dias=prazo,
            condicoes_de_suspensao=tuple(body.get("condicoes_de_suspensao")
                                         or ()),
            contexto=contexto.como_dict(), conta_real=conta_real,
            broker_identity_fingerprint=broker_identity_fingerprint)
    except (ValueError, SalaError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "autorizacao": aut.como_dict()}


def _gates_do_ambiente(tenant_id: str, d: dict) -> list[dict]:
    """Gates lidos do ESTADO REAL da mesa — não de checkboxes do usuário.
    `envio_real_desativado` é a verdade desta versão: nenhuma rota envia
    ordem real; LIVE continua bloqueado por construção."""
    from tradingagents.desk.piloto import SupervisorDePiloto
    posse = d["posse"].resumo()
    return SupervisorDePiloto.gates_de_ativacao(
        single_owner=bool(posse.get("valida") and posse.get("sou_eu")),
        kill_switch=not _parada(tenant_id).bloqueia,
        risco=bool(getattr(d["orchestrator"], "risk", None)),
        reconciliacao=not d["coordinator"].pending_reconciliation(),
        corretora=bool(d["broker_mode"]),
        envio_real_desativado=(d["broker_mode"] != "live"))


@app.post("/v1/escola/pilotos/{agent_id}/autorizar")
def escola_piloto_autorizar(agent_id: str, request: Request,
                            _=Depends(auth), __=Depends(require_plan)):
    """Ato EXPLÍCITO do proprietário. Os gates são medidos no estado real
    da mesa; qualquer reprovação devolve o motivo exato."""
    d = _desk_of(request)
    esc = _escola_of(request)
    sup = _supervisor_of(request)
    pedido = sup.de(agent_id)
    if pedido is not None and pedido.conta_real:
        cfg = load_cfg(request_tenant(request))
        prova, valida = _prova_leitura_real(request, cfg)
        if not _prova_real_serve_piloto(prova, valida, pedido):
            raise HTTPException(
                409, "a autorização exige prova autenticada somente leitura "
                     "válida e recente para a mesma corretora e conta REAL")
    try:
        aut = sup.autorizar(agent_id,
                            gates=_gates_do_ambiente(request_tenant(request),
                                                     d),
                            central=esc["central"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "autorizacao": aut.como_dict(),
            "lembrete": ("piloto em modo paper (simulado) — envio real "
                         "continua desativado")}


@app.post("/v1/escola/pilotos/{agent_id}/revogar")
async def escola_piloto_revogar(agent_id: str, req: Request,
                                _=Depends(auth), __=Depends(require_plan)):
    """Encerra o piloto com motivo auditável. Sem motivo, não revoga."""
    body = await _json_body(req)
    sup = _supervisor_of(req)
    try:
        aut = sup.revogar(agent_id, motivo=str(body.get("motivo") or ""))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "autorizacao": aut.como_dict()}


@app.post("/v1/backup")
def backup_now(_=Depends(auth)):
    """Pacote de recuperação em /data/backups. O download é pelo volume — o
    serviço não expõe segredos por HTTP."""
    from tradingagents.desk.backup import BackupError, create_backup
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        out = create_backup(DATA, DATA / "backups" / f"dz23-backup-{stamp}.tar.gz")
    except BackupError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, **out}


@app.get("/v1/backups")
def list_backups(_=Depends(auth)):
    d = DATA / "backups"
    if not d.exists():
        return {"backups": []}
    return {"backups": sorted(
        ({"arquivo": p.name, "bytes": p.stat().st_size} for p in d.glob("*.tar.gz")),
        key=lambda x: x["arquivo"], reverse=True)}


@app.get("/v1/primeiros-passos")
def onboarding(request: Request, _=Depends(auth)):
    """Checklist do usuário final: o que já está pronto e o que falta, com a
    ação concreta de cada item. Configuração é DO TENANT."""
    cfg = load_cfg(request_tenant(request))
    prov = (cfg.get("TRADINGAGENTS_LLM_PROVIDER") or "").strip().lower()
    chave = {"groq": "GROQ_API_KEY", "google": "GOOGLE_API_KEY",
             "mistral": "MISTRAL_API_KEY", "openrouter": "OPENROUTER_API_KEY",
             "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
             "deepseek": "DEEPSEEK_API_KEY"}.get(prov)
    tem_ia = bool(prov and (prov == "ollama" or (chave and cfg.get(chave))))
    try:
        tem_analise = bool(JOB_STORE.list(request_tenant(request), limit=1))
    except Exception:
        tem_analise = False
    corretora_treino_pronta, corretora_treino_faltando = (
        _credenciais_execucao_prontas(cfg))
    desk = _DESKS.get(request_tenant(request))
    itens = [
        {"passo": "Escolher a IA e colar a chave", "pronto": tem_ia,
         "acao": "Em Configuração: Groq é grátis (console.groq.com/keys)"},
        {"passo": "Dados de mercado (opcional)",
         "pronto": bool(cfg.get("FRED_API_KEY") or cfg.get("ALPHA_VANTAGE_API_KEY")),
         "acao": "Chaves gratuitas do FRED e Alpha Vantage enriquecem a análise"},
        {"passo": "Fazer a primeira análise", "pronto": tem_analise,
         "acao": "Em Análises: ativo (ex.: BTC/USDT) + data e iniciar"},
        {"passo": "Corretora de treino",
         "pronto": corretora_treino_pronta,
         "acao": ("Credenciais completas da corretora de treino"
                  if corretora_treino_pronta else
                  "Informe: " + ", ".join(corretora_treino_faltando))},
        {"passo": "Ligar a mesa de agentes",
         "pronto": bool(desk and desk["scheduler"] and desk["scheduler"].running),
         "acao": "Em Mesa de Agentes: Ligar mesa autônoma"},
        {"passo": "Guardar um backup",
         "pronto": (DATA / "backups").exists() and any((DATA / "backups").glob("*.tar.gz")),
         "acao": "Em Mesa de Agentes: Gerar backup"},
    ]
    return {"itens": itens, "concluidos": sum(1 for i in itens if i["pronto"]),
            "total": len(itens),
            "saude": {"status": "degraded" if HEALTH["avisos"] else "ok",
                      "avisos": HEALTH["avisos"]}}


@app.get("/v1/admin/database")
def database_health(_=Depends(auth)):
    """Diagnóstico do banco: origem, backend em uso e — se for Supabase —
    se o projeto responde. Nenhuma chave aparece na resposta."""
    from tradingagents.infra.supabase import check_rest, config_from_env
    out = {"origem": DATABASE_ORIGEM, "backend": HEALTH["armazenamento"],
           "avisos": HEALTH["avisos"]}
    try:
        cfg = config_from_env()
    except Exception as exc:
        out["supabase"] = {"ok": False, "detalhe": str(exc)}
        return out
    if cfg is None:
        out["supabase"] = {"configurado": False,
                           "proxima_acao": "defina SUPABASE_PROJECT_REF e "
                                           "SUPABASE_DB_PASSWORD para usar o "
                                           "banco gerenciado"}
        return out
    chave = os.environ.get("SUPABASE_SERVICE_KEY", "")
    out["supabase"] = {"configurado": True, "projeto": cfg.project_ref,
                       **check_rest(cfg, chave)}
    return out


# ============================================================ DINHEIRO REAL
# Autorizado pelo dono. Três fechaduras independentes: a frase exata, o teto
# por ordem e o teto de perda diária — mais a parada de emergência.
from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE  # noqa: E402  # isort: skip

# Parada de emergência DURÁVEL. O conjunto em memória anterior desarmava
# sozinho a cada reinício — o dono acreditava ter parado tudo e não tinha.
# ALIAS obrigatório: este KillSwitch (do desk, por armazém/tenant) é OUTRA
# classe que o `execution.KillSwitch` importado no topo. Importá-lo com o
# mesmo nome REBINDAVA o global `KillSwitch`, e o `TenantRuntime` (que resolve
# o nome em tempo de chamada) passava a construir a classe errada — sem
# `.state` —, quebrando `/v1/office/snapshot` e `/v1/office/events` com 500.
from tradingagents.desk.killswitch import (  # noqa: E402
    KillSwitch as DeskKillSwitch,
    KillSwitchError,
)


def _relatorio_de_reconciliacao(tenant_id: str) -> dict:
    """Relatório do SERVIDOR sobre o estado financeiro do inquilino.

    É esta função — e não o corpo da requisição — que decide se é seguro
    liberar a parada. Sem mesa carregada, o resultado é 'não seguro':
    desconhecido nunca vira permissão.
    """
    d = _DESKS.get(tenant_id)
    if d is None:
        # Carrega o estado SEM passar pelo portão da parada: examinar é
        # justamente o que precisa ser possible enquanto está parado. Se nem
        # isso funcionar, o resultado é 'não seguro' — nunca otimista.
        try:
            d = _desk_boot(tenant_id)
            _DESKS[tenant_id] = d
        except Exception as exc:
            return {"seguro": False,
                    "pendencias": [f"estado financeiro não pôde ser lido "
                                   f"({type(exc).__name__}): liberar seria "
                                   "apostar que não há nada em aberto"]}
    coord = d["coordinator"]
    pendencias = []
    incertas = [r.order_id for r in coord.pending_reconciliation()]
    if incertas:
        pendencias.append(f"{len(incertas)} ordem(ns) em estado incerto: "
                          + ", ".join(incertas[:5]))
    restauradas = getattr(coord, "posicoes_restauradas", {}) or {}
    if restauradas:
        pendencias.append(f"{len(restauradas)} posição(ões) restauradas ainda "
                          "não conferidas com a corretora")
    if getattr(coord, "reconciliacao_pendente", False):
        pendencias.append("portão de reconciliação ligado pelo restore")
    # FRESHNESS: um relatório SAFE antigo não autoriza para sempre. Se o
    # estado financeiro mudou depois dele, ele deixa de valer.
    # A validade do relatório é decidida no coordenador, que conhece a
    # versão do estado e a identidade da mesa — o serviço não reimplementa.
    valido, motivo = coord.relatorio_valido()
    if not valido:
        pendencias.append(motivo)
    if not coord.ledger.is_balanced():
        pendencias.append("livro-razão desbalanceado")
    return {"seguro": not pendencias, "pendencias": pendencias}


def _parada(tenant_id: str) -> DeskKillSwitch:
    from tradingagents.desk.storage import ArmazemDeEstado
    d = _DESKS.get(tenant_id)
    armazem = d.get("armazem") if d else ArmazemDeEstado(
        os.environ.get("DATABASE_URL"), diretorio=DATA / "desks")
    return DeskKillSwitch(armazem, tenant_id)


def _teto_positivo(cfg: dict, chave: str) -> bool:
    """Teto de dinheiro real só é teto como NÚMERO finito > 0 — a mesma
    exigência da ponte CCXT (`_decimal_env`). "0", "-5", "abc", "inf" e
    "NaN" não podem aparecer como pronto no painel."""
    try:
        valor = Decimal(str(cfg.get(chave) or ""))
    except (InvalidOperation, ValueError):
        return False
    return valor.is_finite() and valor > 0


def _credenciais_execucao_prontas(cfg: dict) -> tuple[bool, list[str]]:
    """Valida os campos exigidos pela corretora sem iniciar conexão.

    Chave+segredo não bastam nas corretoras que usam passphrase. O checklist
    deve falhar antes da construção do CCXT, com a pendência exata, em vez de
    apresentar um falso verde que só seria descoberto ao iniciar a mesa.
    """
    from service.personal_exchange_catalog import (
        required_execution_credentials,
    )

    exchange = str(cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
    try:
        exigidos = required_execution_credentials(exchange)
    except KeyError:
        return False, ["corretora suportada"]
    if not exigidos:
        return False, ["adaptador de execução homologado"]
    vinculo = str(
        cfg.get("EXCHANGE_CREDENTIAL_EXCHANGE") or "").strip().lower()
    if vinculo != exchange:
        return False, ["nova chave e novo segredo desta corretora"]
    rotulos = {
        "EXCHANGE_API_KEY": "chave",
        "EXCHANGE_API_SECRET": "segredo",
        "EXCHANGE_API_PASSWORD": "passphrase",
    }
    faltando = [rotulos[campo] for campo in exigidos
                if not str(cfg.get(campo) or "").strip()]
    return not faltando, faltando


def _fill_de_corretora_externa(d, *, exchange: str,
                               credential_fingerprint: str,
                               credential_bound_at: str):
    """Ordem PREENCHIDA com id de exchange de verdade.

    O simulador local grava id `paper-*` (PaperBroker); o id do
    CCXTDeskBroker é o id opaco devolvido pela corretora externa. Neste
    lançamento a única corretora externa construível é a testnet (LIVE é
    selado por código), então um fill com id externo é a evidência honesta
    de "já operou em testnet". Registro sem id, id do simulador ou estado
    desconhecido NÃO conta."""
    if not d:
        return None
    try:
        vinculado_em = datetime.fromisoformat(
            str(credential_bound_at or "").replace("Z", "+00:00"))
        if vinculado_em.tzinfo is None:
            return None
        vinculado_em = vinculado_em.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None
    for r in d["coordinator"].orders.values():
        if r.state.value != "filled":
            continue
        externo = str(getattr(r, "external_id", "") or "")
        if not externo or externo.startswith("paper-"):
            continue
        origem_exchange = str(
            getattr(r, "testnet_exchange", "") or "").strip().lower()
        origem_fingerprint = str(
            getattr(r, "testnet_credential_fingerprint", "") or "")
        if (origem_exchange != exchange
                or not _fingerprint_sha256_valido(origem_fingerprint)
                or not hmac.compare_digest(
                    origem_fingerprint, credential_fingerprint)):
            continue
        terminal = str(getattr(r, "terminal_em", "") or "")
        if not _horario_testnet_valido(terminal):
            continue
        try:
            terminal_em = datetime.fromisoformat(
                terminal.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if terminal_em <= vinculado_em:
            continue
        return r
    return None


_CHAVE_HOMOLOGACAO_TESTNET = "homologacao_testnet"
_SCHEMA_HOMOLOGACAO_TESTNET = "dz23.homologacao-testnet/1"
_TOLERANCIA_RELOGIO_TESTNET = timedelta(minutes=5)


def _env_testnet(cfg: dict) -> dict:
    return {k: cfg.get(k, "") for k in (
        "TRADINGAGENTS_EXCHANGE", "EXCHANGE_API_KEY",
        "EXCHANGE_API_SECRET", "EXCHANGE_API_PASSWORD", "EXCHANGE_API_UID")}


def _horario_testnet_valido(valor, agora: datetime | None = None) -> bool:
    """Aceita prova histórica, mas nunca horário inválido ou no futuro."""
    try:
        instante = datetime.fromisoformat(
            str(valor or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if instante.tzinfo is None:
        return False
    atual = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return instante.astimezone(timezone.utc) <= (
        atual + _TOLERANCIA_RELOGIO_TESTNET)


def _fingerprint_sha256_valido(valor) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(valor or "")))


def _prova_testnet(request: Request, cfg: dict) -> tuple[dict, bool]:
    """Lê a homologação durável da corretora atualmente selecionada."""
    exchange = str(cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
    esc = _escola_of(request)
    provas = esc["armazem"].carregar(
        esc["tenant_id"], _CHAVE_HOMOLOGACAO_TESTNET)
    if not isinstance(provas, dict):
        provas = {}
    prova = provas.get(exchange)
    fingerprint_atual = testnet_credential_fingerprint(_env_testnet(cfg))
    credencial_compativel = bool(
        isinstance(prova, dict)
        and _fingerprint_sha256_valido(prova.get("credential_fingerprint"))
        and hmac.compare_digest(
            str(prova["credential_fingerprint"]), fingerprint_atual))
    horario_valido = bool(
        isinstance(prova, dict)
        and _horario_testnet_valido(prova.get("verified_at")))
    ordem_identificada = bool(
        isinstance(prova, dict)
        and _fingerprint_sha256_valido(
            prova.get("external_order_fingerprint")))
    valida = bool(
        isinstance(prova, dict)
        and prova.get("schema") == _SCHEMA_HOMOLOGACAO_TESTNET
        and str(prova.get("exchange") or "").strip().lower() == exchange
        and prova.get("filled_external_order") is True
        and prova.get("reconciled") is True
        and credencial_compativel
        and horario_valido
        and ordem_identificada)
    if not isinstance(prova, dict):
        return ({"valida": False,
                 "motivo": f"sem prova TESTNET reconciliada para {exchange}"},
                False)
    if valida:
        motivo = "ordem TESTNET preenchida e reconciliada na mesma corretora"
    elif not credencial_compativel:
        motivo = ("credenciais TESTNET foram alteradas; repita um fill e a "
                  "reconciliação nesta corretora")
    elif not horario_valido:
        motivo = "prova TESTNET com horário inválido; refaça a homologação"
    elif not ordem_identificada:
        motivo = "prova TESTNET sem identidade de ordem verificável"
    else:
        motivo = "prova TESTNET inválida ou incompatível"
    return ({
        "valida": valida,
        "exchange": prova.get("exchange"),
        "verified_at": prova.get("verified_at"),
        "mode": "testnet",
        "filled_external_order": bool(prova.get("filled_external_order")),
        "reconciled": bool(prova.get("reconciled")),
        "motivo": motivo,
    }, valida)


def _registrar_prova_testnet(request: Request, d: dict,
                             resultado: dict) -> dict | None:
    """Persiste prova somente após fill externo e reconciliação segura.

    A prova é vinculada à corretora; ids de ordem e credenciais entram apenas
    como SHA-256 e nunca voltam pela API. Trocar a configuração para LIVE pode
    destruir a mesa em memória sem apagar a homologação já concluída.
    """
    if resultado.get("seguro") is not True:
        return None
    cfg = load_cfg(request_tenant(request))
    exchange = str(cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
    if (str(d.get("broker_mode") or "").lower() != "testnet"
            or str(cfg.get("TRADINGAGENTS_BROKER_MODE") or "").lower()
            != "testnet"):
        return None
    coord = d.get("coordinator")
    broker = getattr(coord, "broker", None)
    bridge = getattr(broker, "bridge", None)
    if (getattr(bridge, "mode", None) != "testnet"
            or getattr(bridge, "exchange_name", None) != exchange):
        return None
    fingerprint_atual = testnet_credential_fingerprint(_env_testnet(cfg))
    fill = _fill_de_corretora_externa(
        d, exchange=exchange,
        credential_fingerprint=fingerprint_atual,
        credential_bound_at=str(
            cfg.get("EXCHANGE_CREDENTIAL_BOUND_AT") or ""))
    if fill is None:
        return None
    external_id = str(getattr(fill, "external_id", "") or "")
    agora = datetime.now(timezone.utc).isoformat()
    prova = {
        "schema": _SCHEMA_HOMOLOGACAO_TESTNET,
        "exchange": exchange,
        "mode": "testnet",
        "filled_external_order": True,
        "reconciled": True,
        "verified_at": agora,
        "credential_fingerprint": fingerprint_atual,
        "external_order_fingerprint": hashlib.sha256(
            f"{exchange}\0{external_id}".encode()).hexdigest(),
    }
    esc = _escola_of(request)
    provas = esc["armazem"].carregar(
        esc["tenant_id"], _CHAVE_HOMOLOGACAO_TESTNET)
    if not isinstance(provas, dict):
        provas = {}
    provas[exchange] = prova
    esc["armazem"].salvar(
        esc["tenant_id"], _CHAVE_HOMOLOGACAO_TESTNET, provas)
    return {
        "valida": True, "exchange": exchange, "mode": "testnet",
        "filled_external_order": True, "reconciled": True,
        "verified_at": agora,
    }


_CHAVE_PREPARACAO_REAL = "preparacao_real_leitura"
_SCHEMA_PREPARACAO_REAL = "dz23.preparacao-real-leitura/2"
_VALIDADE_PREPARACAO_REAL = timedelta(hours=24)
_TOLERANCIA_RELOGIO_PREPARACAO_REAL = timedelta(minutes=5)


def _env_leitura_real(cfg: dict) -> dict:
    return {k: cfg.get(k, "") for k in (
        "TRADINGAGENTS_EXCHANGE", "REAL_READ_ONLY_API_KEY",
        "REAL_READ_ONLY_API_SECRET", "REAL_READ_ONLY_API_PASSWORD",
        "REAL_READ_ONLY_API_UID", "REAL_READ_ONLY_EXCHANGE",
        "IQ_OPTION_MCP_BEARER_TOKEN")}


def _prova_leitura_real_recente(prova: dict, agora: datetime | None = None) -> bool:
    """Acesso privado precisa ser comprovado de novo a cada 24 horas.

    Timestamp ausente, sem fuso, inválido ou muito no futuro é recusado. Isso
    impede que uma prova antiga ou editada sobreviva indefinidamente só porque
    a impressão da credencial continua igual.
    """
    try:
        verificada = datetime.fromisoformat(
            str(prova.get("verificada_em") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if verificada.tzinfo is None:
        return False
    atual = agora or datetime.now(timezone.utc)
    verificada = verificada.astimezone(timezone.utc)
    atual = atual.astimezone(timezone.utc)
    idade = atual - verificada
    return (-_TOLERANCIA_RELOGIO_PREPARACAO_REAL <= idade
            <= _VALIDADE_PREPARACAO_REAL)


def _prova_leitura_real_armazem(armazem, tenant_id: str,
                                cfg: dict) -> tuple[dict | None, bool]:
    """Carrega a prova e informa se ela ainda pertence à credencial atual."""
    prova = armazem.carregar(tenant_id, _CHAVE_PREPARACAO_REAL)
    if not isinstance(prova, dict):
        return None, False
    atual = read_only_credential_fingerprint(_env_leitura_real(cfg))
    valida = bool(
        prova.get("schema") == _SCHEMA_PREPARACAO_REAL
        and prova.get("credential_fingerprint")
        and prova.get("broker_identity_verified") is True
        and _fingerprint_sha256_valido(
            prova.get("broker_identity_fingerprint"))
        and hmac.compare_digest(str(prova["credential_fingerprint"]), atual)
        and prova.get("exchange") == (
            cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
        and _prova_leitura_real_recente(prova)
    )
    return prova, valida


def _prova_leitura_real(request: Request, cfg: dict) -> tuple[dict | None, bool]:
    esc = _escola_of(request)
    return _prova_leitura_real_armazem(
        esc["armazem"], esc["tenant_id"], cfg)


def _prova_real_serve_piloto(prova: dict | None, valida: bool, piloto) -> bool:
    """Confirma conta, corretora e restrições no instante de usar a prova."""
    return bool(
        valida and prova
        and prova.get("authenticated_read") is True
        and prova.get("broker_identity_verified") is True
        and prova.get("sem_trade_declarado") is True
        and prova.get("sem_saque_declarado") is True
        and prova.get("provider_mutating_tools_exposed") is not True
        and str(prova.get("exchange") or "").strip().lower()
        == str(getattr(piloto, "corretora", "") or "").strip().lower()
        and str(prova.get("conta") or "").strip()
        == str(getattr(piloto, "conta", "") or "").strip()
        and hmac.compare_digest(
            str(prova.get("broker_identity_fingerprint") or ""),
            str(getattr(piloto, "broker_identity_fingerprint", "") or ""))
    )


def _bloqueios_de_conta_real(supervisor, prova: dict | None,
                             valida: bool) -> dict[str, str]:
    """Bloqueios adicionais do vigia LIVE, calculados por piloto."""
    bloqueios = {}
    for piloto in supervisor.autorizacoes():
        estado = getattr(getattr(piloto, "estado", None), "value", "")
        if estado != "piloto_autorizado":
            continue
        if not getattr(piloto, "conta_real", False):
            bloqueios[piloto.agent_id] = (
                "piloto sem vínculo explícito com conta REAL")
        elif not _prova_real_serve_piloto(prova, valida, piloto):
            bloqueios[piloto.agent_id] = (
                "prova somente leitura da conta REAL ausente, "
                "expirada ou incompatível")
    return bloqueios


def _prova_publica(prova: dict | None, valida: bool) -> dict:
    if not prova:
        return {"registrada": False, "valida": False}
    recente = _prova_leitura_real_recente(prova)
    expira_em = ""
    try:
        verificada = datetime.fromisoformat(
            str(prova.get("verificada_em") or "").replace("Z", "+00:00"))
        if verificada.tzinfo is not None:
            expira_em = (verificada.astimezone(timezone.utc)
                         + _VALIDADE_PREPARACAO_REAL).isoformat()
    except (TypeError, ValueError):
        pass
    if valida:
        aviso = "prova válida para a credencial atualmente salva"
    elif not recente:
        aviso = "prova expirada ou com horário inválido; refaça a comprovação"
    else:
        aviso = "prova obsoleta: corretora ou credencial foi alterada"
    return {
        "registrada": True,
        "valida": valida,
        "exchange": prova.get("exchange", ""),
        "conta": prova.get("conta", ""),
        "verificada_em": prova.get("verificada_em", ""),
        "expira_em": expira_em,
        "authenticated_read": bool(prova.get("authenticated_read")),
        "broker_identity_verified": bool(
            prova.get("broker_identity_verified")),
        "read_methods": list(prova.get("read_methods") or ()),
        "provider_mutating_tools_exposed": bool(
            prova.get("provider_mutating_tools_exposed")),
        "sem_trade_declarado": bool(prova.get("sem_trade_declarado")),
        "sem_saque_declarado": bool(prova.get("sem_saque_declarado")),
        "aviso": aviso,
    }


def _decimal_positivo(valor) -> Decimal | None:
    """Converte um limite financeiro sem aceitar infinito/NaN/zero."""
    try:
        numero = Decimal(str(valor or ""))
    except (InvalidOperation, ValueError):
        return None
    return numero if numero.is_finite() and numero > 0 else None


def _certificacoes_real(request: Request, cfg: dict,
                        prova: dict | None, prova_valida: bool) -> list[dict]:
    """Revalida estratégia, conta e risco para a preparação REAL.

    A consulta é fail-closed: uma autorização antiga só aparece pronta se a
    Escola ainda a sustenta e se os limites globais cabem dentro dos limites
    explicitamente concedidos ao piloto. Isto continua sendo preparação; não
    muda mandato, modo da mesa nem capacidade de enviar ordens.
    """
    if not (prova_valida and prova and prova.get("authenticated_read")
            and prova.get("broker_identity_verified") is True
            and _fingerprint_sha256_valido(
                prova.get("broker_identity_fingerprint"))
            and prova.get("sem_trade_declarado")
            and prova.get("sem_saque_declarado")
            and prova.get("provider_mutating_tools_exposed") is not True):
        return []
    try:
        esc = _escola_of(request)
        sup = _supervisor_of(request)
        sup.vigiar(central=esc["central"])
        relatorio = sup.relatorio()
        identidades_fn = getattr(sup, "identidades_de_conta", None)
        identidades = (identidades_fn() if callable(identidades_fn) else {
            str(p.get("authorization_id") or ""): str(
                p.get("broker_identity_fingerprint") or "")
            for p in relatorio
        })
    except Exception:
        return []
    exchange = (cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
    conta = str(prova.get("conta") or "").strip()
    teto_ordem = _decimal_positivo(cfg.get("LIVE_MAX_ORDER_VALUE"))
    teto_diario = _decimal_positivo(cfg.get("LIVE_MAX_DAILY_LOSS"))
    saida = []
    for piloto in relatorio:
        if piloto.get("estado") != "piloto_autorizado":
            continue
        if piloto.get("conta_real") is not True:
            continue
        if str(piloto.get("corretora") or "").strip().lower() != exchange:
            continue
        if str(piloto.get("conta") or "").strip() != conta:
            continue
        if not hmac.compare_digest(
                str(identidades.get(str(
                    piloto.get("authorization_id") or "")) or ""),
                str(prova.get("broker_identity_fingerprint") or "")):
            continue
        capital = _decimal_positivo(piloto.get("capital_maximo"))
        perda_dia = _decimal_positivo(piloto.get("perda_diaria_maxima"))
        perda_total = _decimal_positivo(piloto.get("perda_total_maxima"))
        motivos = []
        if not all((capital, perda_dia, perda_total)):
            motivos.append("limites do piloto ausentes ou inválidos")
        elif perda_dia > perda_total:
            motivos.append("perda diária do piloto excede a perda total")
        if teto_ordem is None or (capital is not None and teto_ordem > capital):
            motivos.append("teto global por ordem não cabe no capital do piloto")
        if teto_diario is None or (
                perda_dia is not None and teto_diario > perda_dia):
            motivos.append("perda diária global excede o limite do piloto")
        saida.append({
            "agent_id": piloto.get("agent_id"),
            "authorization_id": piloto.get("authorization_id"),
            "estrategia": piloto.get("estrategia"),
            "versao": piloto.get("versao"),
            "corretora": piloto.get("corretora"),
            "conta": piloto.get("conta"),
            "contexto_certificacao": dict(
                piloto.get("contexto_certificacao") or {}),
            "valido_ate": piloto.get("valido_ate"),
            "risco_compativel": not motivos,
            "pendencias_risco": motivos,
        })
    return saida


def _reconciliacao_para_prontidao(tenant_id: str) -> dict:
    """Obtém a reconciliação sem transformar estado desconhecido em pronto."""
    try:
        relatorio = _relatorio_de_reconciliacao(tenant_id)
    except Exception as exc:
        return {"seguro": False,
                "pendencias": ["reconciliação indisponível ("
                                f"{type(exc).__name__})"]}
    if not isinstance(relatorio, dict):
        return {"seguro": False,
                "pendencias": ["relatório de reconciliação inválido"]}
    return {"seguro": bool(relatorio.get("seguro")),
            "pendencias": list(relatorio.get("pendencias") or ())}


def _parada_desarmada_para_prontidao(tenant_id: str) -> bool:
    """Falha ao ler o kill switch significa bloqueio, nunca permissão."""
    try:
        return not _parada(tenant_id).bloqueia
    except Exception:
        return False


def _recuperacao_local_para_prontidao() -> dict:
    """Prova de restore é lida sem restaurar ou alterar o serviço ativo."""
    try:
        from tradingagents.desk.recovery import validate_local_restore_proof
        return validate_local_restore_proof(DATA)
    except Exception as exc:
        return {
            "valida": False,
            "verificado_em": "",
            "cobre_postgresql_externo": False,
            "motivo": f"prova de recuperação indisponível ({type(exc).__name__})",
        }


def _recuperacao_postgres_para_prontidao() -> dict:
    """Exige restore lógico somente quando há PostgreSQL configurado."""
    from tradingagents.desk.postgres_recovery import (
        not_required_status,
        validate_postgres_restore_proof,
    )

    database_url = (DATABASE_URL or os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        return not_required_status()
    try:
        return validate_postgres_restore_proof(DATA, database_url)
    except Exception as exc:
        return {
            "necessaria": True,
            "valida": False,
            "verificado_em": "",
            "cobre_postgresql_externo": True,
            "motivo": f"prova PostgreSQL indisponível ({type(exc).__name__})",
        }


def _observabilidade_para_prontidao() -> dict:
    """Valida evidência temporal; uma consulta isolada nunca aprova o gate."""
    try:
        from tradingagents.desk.operational_observability import (
            validate_observability_proof,
        )
        return validate_observability_proof(
            DATA,
            expected_instance_id=METRICS.snapshot(detailed=True)[
                "process_instance_id"
            ],
        )
    except Exception as exc:
        return {
            "valida": False,
            "verificado_em": "",
            "idade_maxima_horas": 24,
            "motivo": f"prova de observabilidade indisponível ({type(exc).__name__})",
        }


_CATEGORIAS_LIBERACAO_REAL = (
    "acao_do_proprietario",
    "prova_externa",
    "controle_operacional",
    "liberacao_de_codigo",
)


def _estado_de_liberacao_real(checklist: list[dict]) -> dict:
    """Agrupa bloqueios sem confundir preparação com autorização LIVE."""
    bloqueios = {categoria: [] for categoria in _CATEGORIAS_LIBERACAO_REAL}
    for item in checklist:
        if item.get("pronto"):
            continue
        categoria = str(item.get("categoria") or "controle_operacional")
        if categoria not in bloqueios:
            categoria = "controle_operacional"
        bloqueios[categoria].append(str(item.get("item") or "item sem nome"))
    sem_selo = any(
        bloqueios[categoria]
        for categoria in _CATEGORIAS_LIBERACAO_REAL
        if categoria != "liberacao_de_codigo")
    if sem_selo:
        estado = "preparacao_pendente"
    elif bloqueios["liberacao_de_codigo"]:
        estado = "aguardando_liberacao_de_codigo"
    else:
        estado = "liberado"
    return {
        "schema": "dz23.liberacao-real/1",
        "estado": estado,
        "pre_requisitos_sem_selo_concluidos": not sem_selo,
        "exige_confirmacao_no_momento_da_acao": True,
        "quantidade_por_categoria": {
            categoria: len(itens) for categoria, itens in bloqueios.items()},
        "bloqueios_por_categoria": bloqueios,
    }


def _resumo_sala_real(request: Request) -> dict:
    """Retrato sem segredos da fila Escola -> Sala REAL.

    A consulta nunca concede mandato. Mesmo uma autorização de piloto opera
    no modo simulado enquanto o selo LIVE permanece fechado.
    """
    from tradingagents.desk.piloto import MODO_DO_PILOTO

    base = {
        "disponivel": False,
        "modo_do_piloto": MODO_DO_PILOTO,
        "envio_real_habilitado": False,
        "encaminhamentos_total": 0,
        "autorizacoes_total": 0,
        "estados_encaminhamento": {},
        "estados_autorizacao": {},
    }
    try:
        esc = _escola_of(request)
        supervisor = _supervisor_of(request)
        supervisor.vigiar(central=esc["central"])
        encaminhamentos = supervisor.encaminhamentos()
        autorizacoes = supervisor.relatorio()
    except Exception:
        return {**base, "aviso": (
            "estado da sala REAL indisponível; prontidão permanece bloqueada")}

    def _contar(registros: list[dict]) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for registro in registros:
            estado = str(registro.get("estado") or "desconhecido")
            contagem[estado] = contagem.get(estado, 0) + 1
        return contagem

    return {
        **base,
        "disponivel": True,
        "encaminhamentos_total": len(encaminhamentos),
        "autorizacoes_total": len(autorizacoes),
        "estados_encaminhamento": _contar(encaminhamentos),
        "estados_autorizacao": _contar(autorizacoes),
        "aviso": ("formatura apenas encaminha; limites e autorização explícita "
                  "do proprietário continuam obrigatórios"),
    }


@app.post("/v1/live/preparacao-leitura")
async def live_preparacao_leitura(req: Request, _=Depends(auth),
                                  __=Depends(require_plan)):
    """Comprova acesso autenticado REAL sem executar ação de escrita.

    A declaração de que a chave não possui trade/saque é do proprietário;
    a prova técnica confirma somente que a chave separada consegue ler. O
    cliente CCXT usado aqui nunca alcança o caminho de envio de ordens.
    """
    body = await _json_body(req)
    conta = str(body.get("conta") or "").strip()
    if not conta:
        raise HTTPException(422, "identifique a conta REAL que será preparada")
    broker_account_id = str(body.get("broker_account_id") or "").strip()
    if not broker_account_id:
        raise HTTPException(
            422, "informe o identificador real exibido pela corretora")
    if body.get("confirmo_sem_trade") is not True:
        raise HTTPException(
            422, "confirme que a chave REAL separada não possui permissão de trade")
    if body.get("confirmo_sem_movimentacao") is not True:
        raise HTTPException(
            422, "confirme que a chave REAL separada não possui permissão de saque")
    cfg = load_cfg(request_tenant(req))
    exchange = str(cfg.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
    exchange_da_credencial = str(
        cfg.get("REAL_READ_ONLY_EXCHANGE") or "").strip().lower()
    if not exchange_da_credencial or exchange_da_credencial != exchange:
        raise HTTPException(
            409, "a credencial somente leitura não está vinculada à "
                 "corretora selecionada; escolha a corretora e informe a "
                 "chave e o segredo correspondentes")
    env = _env_leitura_real(cfg)
    env["REAL_READ_ONLY_ACCOUNT_ID"] = broker_account_id
    try:
        if exchange == "iq_option":
            resultado = await probe_iq_option_read_only_account_async(env)
        else:
            resultado = probe_read_only_account(env)
    except (BrokerSecurityError, IqOptionReadOnlyError) as exc:
        raise HTTPException(409, str(exc)) from exc
    agora = datetime.now(timezone.utc).isoformat()
    prova = {
        "schema": _SCHEMA_PREPARACAO_REAL,
        "exchange": resultado["exchange"],
        "conta": conta,
        "authenticated_read": bool(resultado["authenticated_read"]),
        "broker_identity_verified": bool(
            resultado["broker_identity_verified"]),
        "broker_identity_fingerprint": resultado[
            "broker_identity_fingerprint"],
        "read_methods": list(resultado.get("read_methods") or ()),
        "provider_mutating_tools_exposed": bool(
            resultado.get("provider_mutating_tools_exposed")),
        "sem_trade_declarado": True,
        "sem_saque_declarado": True,
        "credential_fingerprint": resultado["credential_fingerprint"],
        "verificada_em": agora,
        "aviso": (
            "acesso de leitura comprovado; o token do provedor também anuncia "
            "ferramentas mutáveis, que o DZ23 não expõe — permissões mínimas "
            "continuam bloqueadas"
            if resultado.get("provider_mutating_tools_exposed") else
            "acesso de leitura comprovado; ausência de trade/saque foi "
            "declarada pelo proprietário e deve ser conferida também no "
            "painel da corretora"),
    }
    esc = _escola_of(req)
    esc["armazem"].salvar(esc["tenant_id"], _CHAVE_PREPARACAO_REAL, prova)
    return {"ok": True, "preparacao": _prova_publica(prova, True),
            "aviso": prova["aviso"]}


def _live_status_payload(request: Request) -> dict:
    """Diz, sem rodeio, se este inquilino pode operar dinheiro real e o que
    falta para poder."""
    cfg = load_cfg(request_tenant(request))
    credenciais_execucao_ok, credenciais_execucao_faltando = (
        _credenciais_execucao_prontas(cfg))
    prova_leitura, prova_valida = _prova_leitura_real(request, cfg)
    prova_testnet, prova_testnet_valida = _prova_testnet(request, cfg)
    acesso_leitura_ok = bool(
        prova_valida and prova_leitura.get("authenticated_read"))
    permissoes_minimas_ok = bool(
        acesso_leitura_ok and prova_leitura.get("sem_trade_declarado")
        and prova_leitura.get("sem_saque_declarado")
        and prova_leitura.get("provider_mutating_tools_exposed") is not True)
    identidade_ok = bool(
        acesso_leitura_ok
        and prova_leitura.get("broker_identity_verified") is True)
    certificacoes = _certificacoes_real(
        request, cfg, prova_leitura, prova_valida)
    pilotos_no_contexto = certificacoes
    risco_certificado = any(p["risco_compativel"] for p in certificacoes)
    reconciliacao = _reconciliacao_para_prontidao(request_tenant(request))
    parada_desarmada = _parada_desarmada_para_prontidao(
        request_tenant(request))
    recuperacao_local = _recuperacao_local_para_prontidao()
    recuperacao_postgres = _recuperacao_postgres_para_prontidao()
    observabilidade = _observabilidade_para_prontidao()
    frase_ok = (cfg.get("LIVE_TRADING_CONFIRM") or "").strip() == LIVE_CONFIRM_PHRASE
    checklist = [
        {"item": "Frase de autorização digitada", "pronto": frase_ok,
         "categoria": "acao_do_proprietario",
         "acao": f"Digite exatamente: {LIVE_CONFIRM_PHRASE}"},
        {"item": "Chaves da corretora configuradas",
         "pronto": credenciais_execucao_ok,
         "categoria": "acao_do_proprietario",
         "acao": ("Credenciais completas para a corretora selecionada"
                  if credenciais_execucao_ok else
                  "Informe: " + ", ".join(credenciais_execucao_faltando))},
        {"item": "Acesso REAL de leitura recente e permissões mínimas registradas",
         "pronto": permissoes_minimas_ok,
         "categoria": "prova_externa",
         "acao": (
             "O token IQ ainda anuncia ferramentas mutáveis no provedor. O "
             "DZ23 chama apenas list_balances, mas o gate exige um token com "
             "escopo read-only verificável antes de considerar permissões "
             "mínimas prontas"
             if prova_leitura and prova_leitura.get(
                 "provider_mutating_tools_exposed") is True else
             "Use uma chave REAL separada, sem trade e sem saque; o servidor "
             "fará apenas consultas autenticadas e exigirá renovação da prova "
             "a cada 24 horas. Confira as permissões também no painel da "
             "corretora")},
        {"item": "Identidade da conta REAL confirmada",
         "pronto": identidade_ok,
         "categoria": "prova_externa",
         "acao": ("Informe o ID real exibido pela corretora; ele será "
                  "comparado em memória e somente um hash será persistido")},
        {"item": "Estratégia e conta com piloto autorizado vigente",
         "pronto": bool(pilotos_no_contexto),
         "categoria": "acao_do_proprietario",
         "acao": ("Forme o agente na Escola Central deste contexto, configure "
                  "capital e perdas e faça a autorização explícita do piloto")},
        {"item": "Limites globais compatíveis com o piloto autorizado",
         "pronto": risco_certificado,
         "categoria": "controle_operacional",
         "acao": ("Defina os tetos globais dentro do capital e da perda diária "
                  "autorizados para ao menos um piloto desta conta")},
        {"item": "Teto por ordem definido",
         "pronto": _teto_positivo(cfg, "LIVE_MAX_ORDER_VALUE"),
         "categoria": "acao_do_proprietario",
         "acao": "Defina LIVE_MAX_ORDER_VALUE (número finito maior que zero)"},
        {"item": "Perda máxima diária definida",
         "pronto": _teto_positivo(cfg, "LIVE_MAX_DAILY_LOSS"),
         "categoria": "acao_do_proprietario",
         "acao": "Defina LIVE_MAX_DAILY_LOSS (número finito maior que zero)"},
        {"item": "Ativação do modo LIVE somente na etapa final",
         "pronto": (cfg.get("TRADINGAGENTS_BROKER_MODE") or "") == "live",
         "categoria": "liberacao_de_codigo",
         "acao": ("Durante a preparação, mantenha PAPER/TESTNET. Na ativação "
                  "final auditada, o modo muda para LIVE junto com o selo de "
                  "código e a confirmação do proprietário")},
        {"item": "Já operou em testnet",
         "pronto": prova_testnet_valida,
         "categoria": "prova_externa",
         "acao": ("Rode a mesa com a corretora TESTNET (CCXT) e veja uma ordem "
                  "preenchida de verdade; depois reconcilie. A prova fica "
                  "vinculada à corretora — PAPER e teste em outra corretora "
                  "não contam")},
        {"item": "Reconciliação financeira atual sem pendências",
         "pronto": reconciliacao["seguro"],
         "categoria": "prova_externa",
         "acao": ("Reconcilie ordens, posições, caixa e livro-razão; relatório "
                  "ausente, antigo ou divergente nunca vira aprovação")},
        {"item": "Parada de emergência desarmada e disponível",
         "pronto": parada_desarmada,
         "categoria": "controle_operacional",
         "acao": ("Resolva a causa da parada, reconcilie o estado e libere-a "
                  "explicitamente antes de qualquer operação")},
        {"item": "Backup local restaurado e verificado recentemente",
         "pronto": recuperacao_local["valida"],
         "categoria": "controle_operacional",
         "acao": ("Gere o backup e execute scripts/verify_local_restore.py; "
                  "a prova vale por 30 dias e não substitui o restore do "
                  "PostgreSQL externo")},
        {"item": "Backup PostgreSQL restaurado e verificado recentemente",
         "pronto": recuperacao_postgres["valida"],
         "categoria": "controle_operacional",
         "acao": (
             "Não aplicável: esta instalação não usa PostgreSQL externo"
             if not recuperacao_postgres["necessaria"] else
             "Pare as escritas, forneça um banco vazio e distinto em "
             "POSTGRES_RESTORE_DATABASE_URL e execute "
             "scripts/verify_postgres_restore.py; a prova vale por 30 dias")},
        {"item": "Observabilidade contínua e alerta externo comprovados",
         "pronto": observabilidade["valida"],
         "categoria": "controle_operacional",
         "acao": ("Configure o monitor externo e execute "
                  "scripts/verify_observability_window.py por ao menos 1 hora; "
                  "a prova vale por 24 horas e nunca autoriza LIVE")},
        # O selo de CÓDIGO da mesa (ADR-006). Sem ele, mesmo com todos os
        # itens acima verdes, a mesa recusa arrancar em LIVE — e é bom que
        # recuse: o painel nunca é a última fechadura de dinheiro real.
        {"item": "Liberação de código para operar dinheiro real na mesa",
         "pronto": MESA_LIVE_LIBERADA,
         "categoria": "liberacao_de_codigo",
         "acao": ("Indisponível neste lançamento: exige liberação de código "
                  "auditada do dono (ADR-006); a mesa recusa corretora LIVE "
                  "sem ela — mantenha o modo em testnet")},
    ]
    faltam = [i for i in checklist if not i["pronto"]]
    estado_de_liberacao = _estado_de_liberacao_real(checklist)
    sala_real = _resumo_sala_real(request)
    return {"pode_operar_real": not faltam, "checklist": checklist,
            "pendencias": len(faltam),
            "estado_de_liberacao": estado_de_liberacao,
            "sala_real": sala_real,
            "preparacao_leitura": _prova_publica(prova_leitura, prova_valida),
            "homologacao_testnet": prova_testnet,
            "reconciliacao": reconciliacao,
            "recuperacao_local": recuperacao_local,
            "recuperacao_postgresql": recuperacao_postgres,
            "observabilidade_operacional": observabilidade,
            "certificacoes_estrategia_conta": certificacoes,
            "pilotos_autorizados_no_contexto": [
                {"agent_id": p.get("agent_id"),
                 "estrategia": p.get("estrategia"),
                 "versao": p.get("versao"), "conta": p.get("conta")}
                for p in pilotos_no_contexto],
            "aviso": ("Dinheiro real: perdas são suas e irreversíveis. O "
                      "sistema não promete lucro.")}


@app.get("/v1/live/status")
def live_status(request: Request, _=Depends(auth)):
    """Checklist detalhado da preparação para dinheiro real."""
    return _live_status_payload(request)


@app.get("/v1/operational-readiness")
def operational_readiness(request: Request, _=Depends(auth)):
    """Preflight autenticado por tenant, somente leitura e fail-closed.

    Esta rota consolida o contrato que o status público não pode revelar. Ela
    não altera configuração, não concede mandato e não possui caminho de ordem.
    """
    dinheiro_real = _live_status_payload(request)
    envio_real = bool(
        dinheiro_real.get("pode_operar_real") and MESA_LIVE_LIBERADA)
    return {
        "schema": "dz23.operational-readiness/1",
        "escopo": "INQUILINO_AUTENTICADO",
        "status": "ready" if envio_real else "blocked",
        "modo_atual": "LIVE" if envio_real else "PAPER",
        "invariantes": {
            "consulta_envia_ordens": False,
            "selo_de_codigo_live": MESA_LIVE_LIBERADA,
            "envio_real_habilitado": envio_real,
            "exige_confirmacao_no_momento_da_acao": True,
        },
        "dinheiro_real": dinheiro_real,
    }


@app.post("/v1/live/parada-emergencia")
def live_emergency(request: Request, _=Depends(auth)):
    """Botão de pânico: bloqueia envio de ordens reais na hora.

    Esta rota NUNCA pode falhar: se a mesa nem inicia (por exemplo, a
    corretora está mal configurada), parar é trivialmente verdade — e ainda
    assim marcamos a parada, porque o dono clicou para parar."""
    tid = request_tenant(request)
    parada = _parada(tid)
    parada.acionar("acionada pelo painel", por=tid)
    d = _DESKS.get(tid)
    if d is None:
        return {"ok": True, "parado": True, "a_encerrar": [],
                "aviso": "nenhuma mesa ativa; envios bloqueados para este "
                         "inquilino até você liberar"}
    corretora = d["coordinator"].broker
    if hasattr(corretora, "bridge"):
        corretora.bridge.emergency_stop("painel")
    d["orchestrator"].pause_entries()
    sched = d["scheduler"]
    if sched.running:
        sched.stop()
    return {"ok": True, "parado": True,
            "a_encerrar": d["orchestrator"].drain(),
            "aviso": "entradas paradas e envios bloqueados; posições abertas "
                     "seguem sob o guardião — encerre-as conscientemente"}


@app.post("/v1/live/liberar")
async def live_resume(req: Request, _=Depends(auth)):
    """Sair da parada de emergência exige a frase — não é clique distraído."""
    body = await _json_body(req)
    if str(body.get("frase", "")).strip() != LIVE_CONFIRM_PHRASE:
        raise HTTPException(422, f"repita exatamente: {LIVE_CONFIRM_PHRASE}")
    tid = request_tenant(req)
    parada = _parada(tid)
    # A EVIDÊNCIA DE RECONCILIAÇÃO É DO SERVIDOR, NUNCA DO PEDIDO.
    # Antes, um "reconciliado": true no corpo bastava para destravar dinheiro
    # real — o próprio cliente afirmando que conferiu. Agora o servidor
    # verifica o estado do coordenador: ordem incerta, ordem aberta ou
    # posição restaurada mantêm a parada.
    relatorio = _relatorio_de_reconciliacao(tid)
    if not relatorio["seguro"]:
        raise HTTPException(409, {
            "erro": "reconciliação pendente — liberar agora arriscaria operar "
                    "sobre posição ou ordem desconhecida",
            "pendencias": relatorio["pendencias"],
            "acao": "Confira as ordens e posições na corretora e reconcilie "
                    "em Mesa de Agentes antes de liberar",
        })
    try:
        parada.liberar(frase_correta=True, reconciliado=True, por=tid)
    except KillSwitchError as exc:
        raise HTTPException(409, str(exc)) from None
    _DESKS.pop(tid, None)          # reconstrói limpo no próximo uso
    return {"ok": True, "liberado": True,
            "aviso": "envios liberados; confira os tetos antes de retomar"}


@app.get("/v1/desk/aprendizado")
def desk_learning(request: Request, _=Depends(auth)):
    """O que os agentes aprenderam — visível, não caixa-preta."""
    d = _desk_of(request)
    mem = d.get("memoria")
    linhas = mem.relatorio() if mem else []
    return {"estrategias": linhas,
            "amostra_minima": __import__("tradingagents.desk.learning",
                                         fromlist=["AMOSTRA_MINIMA"]).AMOSTRA_MINIMA,
            "como_funciona": ("Cada operação FECHADA vira estatística por "
                              "estratégia e regime. Abaixo da amostra mínima o "
                              "comportamento não muda; histórico ruim reduz "
                              "exposição; histórico bom NÃO aumenta além do "
                              "teto do seu mandato."),
            "aviso": "desempenho passado não prevê o futuro"}


@app.get("/v1/desk/viabilidade")
def desk_viability(request: Request, _=Depends(auth)):
    """A conta que decide tudo: depois de taxa, spread e slippage, esta mesa
    ganha dinheiro? Usa o histórico REAL quando existe; senão, mostra o que
    seria preciso para empatar."""
    from decimal import Decimal as D

    from tradingagents.desk.costs import custo_de_operar, viabilidade
    d = _desk_of(request)
    mem, orch = d.get("memoria"), d["orchestrator"]
    exchange = d.get("broker_mode") and (load_cfg(request_tenant(request))
                                         .get("TRADINGAGENTS_EXCHANGE") or "binance")
    linhas = []
    for trader in orch.traders:
        st = mem.estatistica(trader.strategy) if mem else None
        instrumento = orch.mandate.instruments[0] if orch.mandate.instruments else "BTC/USDT"
        custo = custo_de_operar(instrumento, exchange or "binance",
                                orch.mandate.capital * D("0.15"))
        if st and st.maduro:
            acerto = D(str(round(st.taxa_acerto, 4)))
            ganho = D(str(abs(round(st.retorno_medio, 6)))) or D("0.01")
            perda = ganho
            origem = f"histórico real ({st.amostras} operações)"
        else:
            acerto, ganho, perda = D("0.5"), trader.movimento_alvo, trader.movimento_alvo / 2
            origem = "estimativa do alvo da estratégia (sem histórico maduro)"
        v = viabilidade(acerto, ganho, perda, custo)
        linhas.append({"estrategia": trader.strategy, "origem": origem,
                       "custo_por_operacao": f"{v['custo_por_operacao']:.2%}",
                       "esperanca_liquida": f"{v['esperanca_liquida']:.2%}",
                       "acerto_minimo": f"{v['acerto_minimo_para_empatar']:.0%}",
                       "viavel": v["viavel"], "veredito": v["veredito"]})
    return {"exchange": exchange or "binance", "estrategias": linhas,
            "como_ler": ("Se a esperança líquida é negativa, a estratégia perde "
                         "dinheiro NA MÉDIA mesmo acertando — o custo come o "
                         "resultado. Reduza custo (exchange mais barata), "
                         "aumente o alvo, ou não opere."),
            "aviso": "expectativa calculada; não é previsão"}


@app.get("/v1/quant/status")
def quant_status(request: Request, _=Depends(auth)):
    """Painel da camada quant/IA: o que está instalado, em que papel, e a
    garantia de governança (itens 7 e 9)."""
    from tradingagents.quant import disponibilidade
    return {
        "modelos": disponibilidade(),
        "quantitativo": ("features, ensemble, calibração de confiança e "
                         "monitoramento de drift ativos — tudo determinístico"),
        "governanca": ("nenhum modelo de IA envia ordens, altera limites de "
                       "risco ou promove estratégias para LIVE"),
        "aviso": ("modelos experimentais NÃO são presumidos superiores às "
                  "estratégias matemáticas; promoção exige comparação "
                  "champion/challenger com amostra mínima"),
        "ia_contexto": _IA_CONTEXTO.get(request_tenant(request)),
    }


# =============================================================== ALERTAS ⑥
def _notificador(tenant_id: str):
    from tradingagents.desk.alerts import Notificador
    cfg = load_cfg(tenant_id)
    chave = f"_notificador_{tenant_id}"
    atual = _DESKS.get(tenant_id, {}).get(chave)
    if atual is None:
        atual = Notificador(
            telegram_token=cfg.get("ALERTA_TELEGRAM_TOKEN", ""),
            telegram_chat=cfg.get("ALERTA_TELEGRAM_CHAT", ""),
            webhook_url=cfg.get("ALERTA_WEBHOOK_URL", ""))
        if tenant_id in _DESKS:
            _DESKS[tenant_id][chave] = atual
    return atual


def _estado_para_alertas(d: dict) -> list:
    from decimal import Decimal as D

    from tradingagents.desk.alerts import avaliar_estado
    coord, led, sched = d["coordinator"], d["ledger"], d["scheduler"]
    corretora = getattr(coord.broker, "bridge", None)
    return avaliar_estado(
        pendencias_reconciliacao=[r.order_id for r in coord.pending_reconciliation()],
        contabilidade_balanceada=led.is_balanced(),
        perda_hoje=getattr(corretora, "realized_loss_today", D("0")),
        limite_perda=getattr(corretora, "max_daily_loss", D("0")),
        mesa_deveria_rodar=not sched.state.stopped,
        mesa_rodando=sched.running,
        erros_do_ciclo=sched.state.errors)


@app.get("/v1/alertas")
def listar_alertas(request: Request, _=Depends(auth)):
    """O que exigiria sua atenção AGORA, e o histórico do que já avisou."""
    tid = request_tenant(request)
    d = _desk_of(request)
    alertas = _estado_para_alertas(d)
    fila = _fila_alertas(tid)
    from tradingagents.desk.canais_alerta import construir_canais
    assinaturas, _entregador = _push_do_tenant(tid)
    canais = construir_canais(load_cfg(tid),
                              transportes=_TESTE_CANAL_TRANSPORTES.get(tid),
                              assinaturas_webpush=assinaturas)
    historico = [{"em": a.criado_em, "codigo": a.tipo,
                  "severidade": a.severidade, "titulo": a.titulo,
                  "detalhe": a.detalhe, "acao": a.acao, "estado": a.estado,
                  "vencido": a.vencido()}
                 for a in sorted(fila.alertas, key=lambda x: x.criado_em,
                                 reverse=True)[:20]]
    return {"ativos": [{"codigo": a.codigo, "severidade": a.severidade,
                        "titulo": a.titulo, "detalhe": a.detalhe, "acao": a.acao}
                       for a in alertas],
            # "canal configurado" = canal EXTERNO com credencial. O painel e
            # o toast do Windows não contam: existem sem configuração.
            "canal_configurado": any(c.configurado()
                                     and c.nome not in ("painel", "windows")
                                     for c in canais),
            "historico": historico,
            "como_configurar": ("Em Configuração → Notificações: Telegram, "
                                "Windows, painel na caixa de entrada, Discord "
                                "e WhatsApp (opcionais)")}


@app.post("/v1/alertas/verificar")
def disparar_alertas(request: Request, _=Depends(auth)):
    """Avalia, enfileira (com dedupe) e entrega o que estiver pendente."""
    tid = request_tenant(request)
    d = _desk_of(request)
    alertas = _estado_para_alertas(d)
    for alerta in alertas:
        _enfileirar_estado_da_mesa(tid, alerta)
    resultado = _despachar_alertas(tid)
    return {"avaliados": len(alertas),
            "enviados": [e["id"] for e in resultado.get("entregues", [])],
            "falhas": [f["id"] for f in resultado.get("falhas", [])],
            "adiados": resultado.get("adiados", []),
            "expirados": resultado.get("expirados", 0)}


# ================================================ CENTRO DE NOTIFICAÇÕES ⑤
# O dono liga, testa e vê o status de cada canal SEM terminal. Segredos
# nunca aparecem: as respostas dizem apenas configurado/não configurado.
@app.get("/v1/notificacoes/canais")
def canais_de_notificacao(request: Request, _=Depends(auth)):
    tid = request_tenant(request)
    from tradingagents.desk.canais_alerta import construir_canais
    assinaturas, _entregador = _push_do_tenant(tid)
    canais = construir_canais(load_cfg(tid),
                              transportes=_TESTE_CANAL_TRANSPORTES.get(tid),
                              assinaturas_webpush=assinaturas)
    ultimos = _testes_canal(tid)
    return {"canais": [{"nome": c.nome, "prioridade": c.prioridade,
                        "configurado": c.configurado(),
                        "disponivel": c.disponivel(),
                        "detalhe": c.detalhe(),
                        "ultimo_teste": (ultimos.get(c.nome) or {}).get(
                            "em", ""),
                        "ultimo_teste_ok": (ultimos.get(c.nome) or {}).get(
                            "ok")}
                       for c in canais],
            "aviso": ("sem nenhum canal externo configurado o alerta fica "
                      "na Caixa de entrada do painel — nunca se perde, mas "
                      "só aparece quando você abre o painel")}


@app.post("/v1/notificacoes/testar")
async def testar_canal_de_notificacao(req: Request, _=Depends(auth)):
    """Teste de entrega REAL do canal escolhido — com evidência (latência,
    resultado). Não marca como funcionando só porque aceitou chamada."""
    tid = request_tenant(req)
    body = await _json_body(req)
    nome = str(body.get("canal") or "").strip()
    from tradingagents.desk.canais_alerta import construir_canais
    from tradingagents.desk.models import utcnow
    assinaturas, entregador = _push_do_tenant(tid)
    canais = {c.nome: c for c in construir_canais(
        load_cfg(tid), transportes=_TESTE_CANAL_TRANSPORTES.get(tid),
        assinaturas_webpush=assinaturas, entregador_webpush=entregador)}
    canal = canais.get(nome)
    if canal is None:
        raise HTTPException(400, "canal desconhecido — opções: "
                                 + ", ".join(canais))
    if not canal.configurado():
        raise HTTPException(400, f"canal {nome} ainda não configurado — "
                                 f"{canal.detalhe()}")
    if nome == "email":
        resultado = canal.enviar_relatorio(
            "Teste do canal de e-mail (relatórios)",
            "Mensagem de teste do Centro de Alertas. Alertas NUNCA vão por "
            "e-mail; este canal serve apenas a relatórios.")
    else:
        from datetime import timedelta

        from tradingagents.desk.notificacao_viva import AlertaVivo
        teste = AlertaVivo(
            tipo="teste_de_canal", severidade="informativo",
            titulo=f"Teste do canal {nome}",
            detalhe="Mensagem de teste do Centro de Alertas",
            acao="Nada a fazer — é apenas um teste",
            fonte="painel", validade=utcnow() + timedelta(minutes=10))
        try:
            resultado = canal.enviar(teste)
        except Exception as exc:
            resultado = {"ok": False,
                         "erro": f"{type(exc).__name__}: {exc}"[:160]}
    if nome == "webpush":
        _persistir_diagnostico_push(tid, assinaturas)
    _registrar_teste_canal(tid, nome, resultado)
    if not resultado.get("ok"):
        METRICS.inc("notif.teste_falhou")
        raise HTTPException(502, "entrega falhou: "
                                 + str(resultado.get("erro") or "sem detalhe"))
    return {"ok": True, "canal": nome,
            "detalhe": resultado.get("detalhe", ""),
            "parcial": bool(resultado.get("parcial")),
            "latencia_ms": resultado.get("latencia_ms", 0),
            "em": utcnow().isoformat(),
            "aviso": ("teste de entrega confirmado — o canal está de pé "
                      "para os alertas reais")}


@app.get("/v1/notificacoes/inbox")
def inbox_de_notificacoes(request: Request, _=Depends(auth)):
    tid = request_tenant(request)
    _desk_of(request)
    fila = _fila_alertas(tid)
    caixa = fila.inbox()
    caixa["fila"] = len(fila.pendentes())
    # Auditoria recente de tentativas — o dono vê o que foi tentado e falhou.
    tentativas = [t for a in fila.alertas for t in a.tentativas]
    tentativas.sort(key=lambda t: t.get("em", ""), reverse=True)
    caixa["auditoria"] = tentativas[:8]
    caixa["como_configurar"] = "Configuração → Notificações"
    return caixa


@app.post("/v1/notificacoes/{alerta_id}/ack")
def ack_de_notificacao(alerta_id: str, request: Request, _=Depends(auth)):
    tid = request_tenant(request)
    fila = _fila_alertas(tid)
    alerta = fila.marcar_lido(alerta_id)
    if alerta is None:
        raise HTTPException(404, "alerta não encontrado")
    _persistir_fila_alertas(tid)
    return {"ok": True, "id": alerta.id, "lido_em": alerta.lido_em}


@app.get("/v1/notificacoes/matriz")
def ler_matriz_de_notificacao(request: Request, _=Depends(auth)):
    tid = request_tenant(request)
    _desk_of(request)
    return _matriz_alertas(tid).to_dict()


@app.put("/v1/notificacoes/matriz")
async def salvar_matriz_de_notificacao(req: Request, _=Depends(auth)):
    """Roteamento por trader/estratégia/corretora/ativo/severidade."""
    from tradingagents.desk.notificacao_viva import MatrizDeRoteamento
    tid = request_tenant(req)
    d = _desk_of(req)
    body = await _json_body(req)
    matriz = MatrizDeRoteamento(body.get("regras") or [])
    d["armazem"].salvar(tid, "alertas_matriz", {"regras": matriz.regras})
    return {"ok": True, **matriz.to_dict()}


# ===================================================== PWA + PUSH (celular)
# O painel instalado no celular recebe os MESMOS alertas do Centro (nunca
# um segundo sistema). Chaves VAPID e inscrições ficam no config do dono
# (cifradas em repouso, SEC-1); estas rotas NUNCA ecoam a chave privada
# nem o endpoint das inscrições.
@app.get("/v1/push/estado")
def push_estado(request: Request, _=Depends(auth)):
    from tradingagents.desk import push_web
    tid = request_tenant(request)
    estado = push_web.estado_publico(load_cfg(tid))
    estado["como_usar"] = (
        "1) gere as chaves VAPID; 2) no celular, toque em Ativar "
        "notificações e aceite o pedido do navegador; 3) confirme em "
        "Testar canal. No iPhone o push só funciona com o app na Tela de "
        "Início (aba comum do Safari não entrega).")
    return estado


@app.post("/v1/push/vapid/gerar")
async def push_vapid_gerar(req: Request, _=Depends(auth)):
    """Gera o par VAPID. Sem `forcar`, não sobrescreve chaves existentes —
    trocar a chave invalida TODAS as inscrições já feitas."""
    from tradingagents.desk import push_web
    tid = request_tenant(req)
    body = await _json_body(req)
    cfg = load_cfg(tid)
    if str(cfg.get(push_web.CHAVE_VAPID_PRIVADA) or "").strip() and not body.get("forcar"):
        raise HTTPException(409, "já existem chaves VAPID — trocá-las "
                                 "invalida a inscrição dos aparelhos; "
                                 "repita com forcar=true para substituir")
    try:
        par = push_web.gerar_chaves_vapid()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    cfg[push_web.CHAVE_VAPID_PUBLICA] = par["publica"]
    cfg[push_web.CHAVE_VAPID_PRIVADA] = par["privada_pem"]
    cfg["ALERTA_WEBPUSH"] = "true"
    if body.get("forcar"):
        cfg[push_web.CHAVE_ASSINATURAS] = "[]"
    save_cfg(cfg, tid)
    return {"ok": True, "publica": par["publica"],
            "assinaturas_zeradas": bool(body.get("forcar"))}


@app.post("/v1/push/assinar")
async def push_assinar(req: Request, _=Depends(auth)):
    """Registra a inscrição do navegador desta instalação do PWA."""
    from tradingagents.desk import push_web
    from tradingagents.desk.models import utcnow
    tid = request_tenant(req)
    body = await _json_body(req)
    cfg = load_cfg(tid)
    if not str(cfg.get(push_web.CHAVE_VAPID_PUBLICA) or "").strip():
        raise HTTPException(409, "gere as chaves VAPID antes de inscrever "
                                 "um aparelho")
    nova = push_web.AssinaturaPush.from_dict(body or {})
    motivo = nova.validar()
    if motivo:
        raise HTTPException(400, motivo)
    assinaturas = push_web.carregar_assinaturas(cfg)
    alvo = nova.identificador()
    existente = next((a for a in assinaturas
                      if a.identificador() == alvo), None)
    if existente is None:
        if len(assinaturas) >= push_web.LIMITE_DISPOSITIVOS:
            raise HTTPException(409, f"limite de {push_web.LIMITE_DISPOSITIVOS} "
                                     "aparelhos atingido — cancele um antes "
                                     "de inscrever outro")
        nova.criado_em = utcnow().isoformat(timespec="seconds")
        assinaturas.append(nova)
    else:
        # Reassinar o MESMO aparelho é rotina (o navegador troca as chaves):
        # preserva o histórico e atualiza as chaves.
        existente.p256dh, existente.auth = nova.p256dh, nova.auth
        if nova.rotulo:
            existente.rotulo = nova.rotulo
    push_web.salvar_assinaturas(cfg, assinaturas)
    save_cfg(cfg, tid)
    return {"ok": True, "id": alvo, "total": len(assinaturas),
            "aviso": ("mande um teste para confirmar que o push CHEGA "
                      "(Testar canal) — inscrição sozinha não prova entrega")}


@app.post("/v1/push/cancelar")
async def push_cancelar(req: Request, _=Depends(auth)):
    """Remove a inscrição de um aparelho pelo id curto mostrado no painel."""
    from tradingagents.desk import push_web
    tid = request_tenant(req)
    body = await _json_body(req)
    alvo = str(body.get("id") or "").strip()
    if not alvo:
        raise HTTPException(422, "informe o id do aparelho")
    cfg = load_cfg(tid)
    assinaturas = push_web.carregar_assinaturas(cfg)
    restantes = [a for a in assinaturas if a.identificador() != alvo]
    if len(restantes) == len(assinaturas):
        raise HTTPException(404, "aparelho não encontrado")
    save_cfg(push_web.salvar_assinaturas(cfg, restantes), tid)
    return {"ok": True, "removidos": 1, "total": len(restantes)}


@app.post("/v1/telegram/webhook")
async def webhook_telegram(req: Request):
    """Callbacks de botão do Telegram. Segredo do webhook autentica a
    chamada (fail-closed sem segredo configurado). Botão NÃO opera na
    corretora: só abre detalhes, registra leitura ou intenção."""
    tid = request_tenant(req)
    cfg = load_cfg(tid)
    segredo = str(cfg.get("ALERTA_TELEGRAM_WEBHOOK_SECRET") or "").strip()
    recebido = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not segredo or not hmac.compare_digest(recebido, segredo):
        raise HTTPException(
            401, "webhook sem segredo válido — configure "
                 "ALERTA_TELEGRAM_WEBHOOK_SECRET e use-o no setWebhook")
    body = await _json_body(req)
    from tradingagents.desk.notificacao_viva import processar_callback_telegram
    resultado = processar_callback_telegram(body, _fila_alertas(tid))
    _persistir_fila_alertas(tid)
    return resultado


# ============================================ COPILOTO MANUAL ⑤ (E3, leitura)
# O dono registra o que JÁ executou na corretora dele; o copiloto acompanha,
# calcula e ALERTA. Nenhum endpoint deste bloco executa ordem — a ausência
# de broker no grafo é garantida por teste estrutural (AST) em manual.py.

def _manual_http(exc: Exception) -> HTTPException:
    from tradingagents.desk.manual import ConflitoDeVersao, PosicaoNaoEncontrada
    if isinstance(exc, PosicaoNaoEncontrada):
        return HTTPException(404, str(exc))
    if isinstance(exc, ConflitoDeVersao):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


@app.get("/v1/manual/modo")
def manual_modo_obter(request: Request, _=Depends(auth)):
    tid = request_tenant(request)
    return _copiloto_para_painel(_copiloto(tid))


@app.put("/v1/manual/modo")
async def manual_modo_definir(req: Request, _=Depends(auth)):
    """Liga/desliga o Copiloto Manual. Ligado = a mesa autônoma não executa;
    o copiloto apenas acompanha e alerta. Desligar devolve a mesa ao normal."""
    tid = request_tenant(req)
    body = await _json_body(req)
    ativo = body.get("ativo")
    if not isinstance(ativo, bool):
        raise HTTPException(422, 'informe {"ativo": true|false} — booleano')
    cop = _copiloto(tid)
    anterior = cop.definir_ativo(ativo)
    _persistir_copiloto(tid)
    return {"ok": True, "anterior": anterior, **_copiloto_para_painel(cop)}


@app.post("/v1/manual/positions")
async def manual_criar_posicao(req: Request, _=Depends(auth)):
    """Registra uma posição que o dono JÁ abriu na corretora dele."""
    tid = request_tenant(req)
    body = await _json_body(req)
    cop = _copiloto(tid)
    try:
        pos, aplicado = cop.criar_posicao(
            instrumento=body.get("instrumento", ""),
            direcao=body.get("direcao", ""),
            quantidade=body.get("quantidade"),
            preco_entrada=body.get("preco_entrada"),
            entrada_em=body.get("entrada_em"),
            origem_preco=str(body.get("origem_preco") or "declarado"),
            tese=body.get("tese") or "",
            stop=body.get("stop"), alvos=body.get("alvos") or (),
            invalidacao=body.get("invalidacao"),
            expira_em=body.get("expira_em"),
            trailing_pct=body.get("trailing_pct"),
            intervalo_s=body.get("intervalo_s"),
            custos_entrada=body.get("custos_entrada") or 0,
            idempotency_key=str(body.get("idempotency_key") or ""))
    except ValueError as exc:
        raise _manual_http(exc) from None
    _persistir_copiloto(tid)
    return {"ok": True, "criada": aplicado, "posicao": pos.para_painel()}


@app.get("/v1/manual/positions")
def manual_listar_posicoes(request: Request, estado: str = "",
                           _=Depends(auth)):
    tid = request_tenant(request)
    cop = _copiloto(tid)
    if cop.ativo:
        # Tique preguiçoso: painel aberto também movimenta o monitor —
        # throttled por posição (intervalo_s), nunca com forcar.
        with contextlib.suppress(Exception):
            _tique_do_copiloto(tid, cop)
    itens = cop.listar(estado or None)
    return {"ativo": cop.ativo,
            "posicoes": [p.para_painel() for p in itens],
            "resumo": {"total": len(itens),
                       "abertas": len(cop.posicoes_abertas()),
                       "monitor": cop.saude_do_monitor()},
            "como_usar": _copiloto_para_painel(cop)["como_usar"]}


@app.get("/v1/manual/positions/{posicao_id}")
def manual_obter_posicao(posicao_id: str, request: Request, _=Depends(auth)):
    tid = request_tenant(request)
    cop = _copiloto(tid)
    try:
        pos = cop.obter(posicao_id)
    except ValueError as exc:
        raise _manual_http(exc) from None
    return {"posicao": pos.para_painel(),
            "eventos": [e.doc() for e in pos.eventos],
            "historico_politica": pos.historico_politica[-10:],
            "snapshots": pos.snapshots[-5:],
            "revisoes": cop.revisoes(posicao_id)[-5:]}


@app.post("/v1/manual/positions/{posicao_id}/events")
async def manual_registrar_evento(posicao_id: str, req: Request,
                                  _=Depends(auth)):
    """Registra o que o dono EXECUTOU (preço executado é obrigatório —
    cotação de tela nunca substitui). Idempotente por idempotency_key."""
    tid = request_tenant(req)
    body = await _json_body(req)
    cop = _copiloto(tid)
    try:
        evento, aplicado = cop.registrar_evento(
            posicao_id, tipo=body.get("tipo", ""),
            quantidade=body.get("quantidade"), preco=body.get("preco"),
            custos=body.get("custos") or 0,
            fonte=str(body.get("fonte") or "declarado"),
            motivo=body.get("motivo") or "", em=body.get("em"),
            idempotency_key=str(body.get("idempotency_key") or ""),
            expected_version=body.get("expected_version"))
        pos = cop.obter(posicao_id)
    except ValueError as exc:
        raise _manual_http(exc) from None
    _persistir_copiloto(tid)
    return {"ok": True, "aplicado": aplicado, "evento": evento.doc(),
            "posicao": pos.para_painel()}


@app.put("/v1/manual/positions/{posicao_id}/monitoring-policy")
async def manual_definir_politica(posicao_id: str, req: Request,
                                  _=Depends(auth)):
    from tradingagents.desk.manual import PoliticaManual
    tid = request_tenant(req)
    body = await _json_body(req)
    cop = _copiloto(tid)
    try:
        politica = PoliticaManual.from_doc({
            "stop": body.get("stop"), "alvos": body.get("alvos") or [],
            "invalidacao": body.get("invalidacao"),
            "expira_em": body.get("expira_em"),
            "trailing_pct": body.get("trailing_pct"),
            "intervalo_s": body.get("intervalo_s") or 120})
        pos = cop.definir_politica(posicao_id, politica,
                                   expected_version=body.get("expected_version"))
    except ValueError as exc:
        raise _manual_http(exc) from None
    _persistir_copiloto(tid)
    return {"ok": True, "posicao": pos.para_painel()}


@app.post("/v1/manual/positions/{posicao_id}/pause")
async def manual_pausar(posicao_id: str, req: Request, _=Depends(auth)):
    tid = request_tenant(req)
    body = await _json_body(req)
    cop = _copiloto(tid)
    try:
        pos = cop.pausar(posicao_id, motivo=str(body.get("motivo") or ""))
    except ValueError as exc:
        raise _manual_http(exc) from None
    _persistir_copiloto(tid)
    return {"ok": True, "posicao": pos.para_painel()}


@app.post("/v1/manual/positions/{posicao_id}/resume")
def manual_retomar(posicao_id: str, request: Request, _=Depends(auth)):
    tid = request_tenant(request)
    cop = _copiloto(tid)
    try:
        pos = cop.retomar(posicao_id)
    except ValueError as exc:
        raise _manual_http(exc) from None
    _persistir_copiloto(tid)
    return {"ok": True, "posicao": pos.para_painel()}


@app.post("/v1/manual/positions/{posicao_id}/revisao")
async def manual_revisao(posicao_id: str, req: Request, _=Depends(auth)):
    """Revisão de diário: a IA do inquilino comenta os FATOS; sem provedor
    configurado a entrada é registrada sem revisão (nunca inventa preço)."""
    tid = request_tenant(req)
    body = await _json_body(req)
    cop = _copiloto(tid)
    try:
        cop.obter(posicao_id)
    except ValueError as exc:
        raise _manual_http(exc) from None
    revisor = _revisor_de_ia(tid, load_cfg(tid))
    entrada = cop.registrar_revisao(
        posicao_id, revisor=revisor, observacao=body.get("observacao") or "")
    _persistir_copiloto(tid)
    return {"ok": True, "entrada": entrada, "ia_configurada": revisor is not None}


@app.get("/v1/manual/positions/{posicao_id}/alerts")
def manual_alertas_da_posicao(posicao_id: str, request: Request,
                              _=Depends(auth)):
    tid = request_tenant(request)
    cop = _copiloto(tid)
    try:
        cop.obter(posicao_id)
    except ValueError as exc:
        raise _manual_http(exc) from None
    fila = _fila_alertas(tid)
    itens = [a for a in fila.alertas
             if str((a.dados or {}).get("posicao_id") or "") == posicao_id
             or a.dedupe_key.startswith(f"manual:{posicao_id}:")]
    itens.sort(key=lambda a: a.criado_em, reverse=True)
    return {"posicao_id": posicao_id, "total": len(itens),
            "alertas": [{"id": a.id, "tipo": a.tipo,
                         "severidade": a.severidade, "titulo": a.titulo,
                         "detalhe": a.detalhe, "acao": a.acao,
                         "estado": a.estado, "ocorrencias": a.ocorrencias,
                         "criado_em": a.criado_em, "lido_em": a.lido_em,
                         "vencido": a.vencido()} for a in itens[:50]]}


@app.post("/v1/manual/alerts/{alerta_id}/ack")
def manual_ack(alerta_id: str, request: Request, _=Depends(auth)):
    """Marca um alerta do copiloto como lido — só alertas DESTE modo."""
    tid = request_tenant(request)
    fila = _fila_alertas(tid)
    alerta = next((a for a in fila.alertas if a.id == alerta_id), None)
    if alerta is None or alerta.fonte != "copiloto_manual":
        raise HTTPException(404, "alerta manual não encontrado")
    alerta = fila.marcar_lido(alerta_id)
    _persistir_fila_alertas(tid)
    return {"ok": True, "id": alerta.id, "lido_em": alerta.lido_em}


@app.get("/v1/manual/monitor/health")
def manual_monitor_health(request: Request, _=Depends(auth)):
    tid = request_tenant(request)
    cop = _copiloto(tid)
    if cop.ativo:
        with contextlib.suppress(Exception):
            _tique_do_copiloto(tid, cop)
    d = _DESKS.get(tid) or {}
    return {"monitor": cop.saude_do_monitor(),
            "mesa": {"modo": (d["modo"].resumo() if d.get("modo") else None),
                     "observando_por_copiloto": cop.ativo},
            "fonte_de_dados": d.get("feed_kind", ""),
            "aviso": ("o copiloto NUNCA executa ordem: ele acompanha, "
                      "calcula e alerta; quem opera é você")}


@app.get("/v1/manual/sinais")
def manual_sinais(request: Request, _=Depends(auth), instrumento: str = "",
                  timeframe: str = "1h"):
    """Análise determinística do mercado para o Copiloto Manual (TD-4).

    Read-only por desenho: NÃO cria nem cancela ordens, NÃO boota a mesa,
    NÃO chama IA. Lê a MESMA fonte do monitor (fixture rotulado ou mercado
    real somente-leitura) e devolve oportunidade, riscos, custos, horizonte
    e invalidação — ou o motivo declarado de cada peça que falta."""
    from tradingagents.desk.copiloto_sinais import analisar_mercado
    from tradingagents.desk.market import (
        TIMEFRAME_SECONDS,
        MarketFeedProviders,
        PublicMarketFeed,
    )
    instrumento = (instrumento or "").strip()
    if not instrumento:
        raise HTTPException(
            422, "informe o instrumento — ex.: &instrumento=BTC/USDT")
    if timeframe not in TIMEFRAME_SECONDS:
        raise HTTPException(422, "timeframe deve ser um de: "
                            + ", ".join(sorted(TIMEFRAME_SECONDS)))
    tid = request_tenant(request)
    d = _DESKS.get(tid) or {}
    feed_kind = d.get("feed_kind", "fixture")
    fechamentos: list[str] = []
    preco_atual = None
    qualidade = feed_kind
    if feed_kind == "real":
        cfg = load_cfg(tid)
        try:
            feed = PublicMarketFeed(cfg.get("TRADINGAGENTS_EXCHANGE")
                                    or "binance")
            snap = MarketFeedProviders(feed, [instrumento],
                                       timeframe=timeframe,
                                       limit=240).snapshots()[0]
            qualidade = snap.quality
            fechamentos = [str(c) for c in snap.closes]
        except Exception as exc:                # fonte fora: motivo honesto
            qualidade = f"indisponível ({type(exc).__name__})"
        aviso_fonte = ("mercado real (leitura pública, somente leitura) — "
                       + qualidade)
    else:
        snaps = []
        snaps_fn = d.get("snaps_fn")
        if snaps_fn is not None:
            with contextlib.suppress(Exception):
                snaps = [s for s in snaps_fn() if s.instrument == instrumento]
        if snaps:
            fechamentos = [str(c) for c in snaps[0].closes]
            qualidade = snaps[0].quality
        aviso_fonte = ("dados de FIXTURE (rotulados) — provam a mesa, não "
                       "o mercado; a análise mostra o que existe, sem número "
                       "inventado")
    if fechamentos:
        preco_atual = fechamentos[-1]
    return analisar_mercado(
        instrumento=instrumento, timeframe=timeframe,
        fechamentos=fechamentos, fonte=feed_kind, qualidade=qualidade,
        aviso_fonte=aviso_fonte, preco_atual=preco_atual)


# ======================================================= TRANSPARÊNCIA ⑦
@app.get("/publico/{tenant_id}")
def pagina_publica(tenant_id: str):
    """Página PÚBLICA com o histórico real — inclusive as perdas.

    Só existe se o dono ligar (TRANSPARENCIA_PUBLICA=true). Mostra desempenho
    e comportamento; NUNCA saldo, chave, ordem individual ou dado de conta.
    Num mercado cheio de promessa vazia, mostrar o resultado real — com as
    perdas — é o argumento de venda mais forte que existe.
    """
    if not _tenant_id_valido(tenant_id):
        raise HTTPException(404, "agência não encontrada")
    cfg = load_cfg(tenant_id)
    if (cfg.get("TRANSPARENCIA_PUBLICA") or "").strip().lower() != "true":
        raise HTTPException(404, "esta agência não publicou seus resultados")
    d = _DESKS.get(tenant_id)
    if d is None:
        try:
            d = _desk_boot(tenant_id)
            _DESKS[tenant_id] = d
        except Exception:
            raise HTTPException(503, "dados indisponíveis no momento") from None
    mem, led, coord = d.get("memoria"), d["ledger"], d["coordinator"]
    fechadas = [r for r in coord.orders.values() if r.state.value == "filled"]
    return {
        "agencia": cfg.get("TRANSPARENCIA_NOME") or "Agência DZ23",
        "modo": d["orchestrator"].mandate.mode.value,
        "fonte_de_dados": d.get("feed_kind", "fixture"),
        "estrategias": (mem.relatorio() if mem else []),
        "operacoes_executadas": len(fechadas),
        "instrumentos": list(d["orchestrator"].mandate.instruments),
        "contabilidade_fecha": led.is_balanced(),
        "compromissos": [
            "Mostramos o histórico real, incluindo as perdas.",
            "A mesa opera em modo simulado/testnet salvo indicação contrária.",
            "Nada aqui é recomendação de investimento.",
            "Desempenho passado não prevê resultado futuro.",
        ],
        "o_que_nao_mostramos": ["saldo", "chaves", "ordens individuais",
                                "dados de conta"],
    }


# ===================================================== MUDANÇA DE SERVIDOR
@app.get("/v1/mudanca/exportar")
def exportar_tudo(request: Request, _=Depends(auth)):
    """Exporta tudo o que importa do inquilino, pelo navegador — SEM segredos.

    Servidor provisório: isto é o que você leva embora. A configuração vai
    sem chaves de IA e sem credenciais de corretora: segredo não viaja em
    arquivo de texto simples. Ao restaurar, as credenciais são informadas
    de novo no painel do servidor novo.
    """
    tid = request_tenant(request)
    d = _DESKS.get(tid)
    armazem = d.get("armazem") if d else None
    sub = SUBS.get(tid)
    cfg = load_cfg(tid)
    segredos = sorted(k for k in cfg if k in SECRETish)
    cfg_publica = {k: v for k, v in cfg.items() if k not in SECRETish}
    pacote = {
        "formato": "dz23.mudanca/2",
        "tenant_id": tid,
        "exportado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "configuracao": cfg_publica,
        "segredos_omitidos": segredos,
        "aprendizado": (armazem.carregar(tid, "aprendizado") if armazem else None),
        "mesa": (armazem.carregar(tid, "mesa") if armazem else None),
        "assinatura": (sub.to_row() if sub else None),
        "aviso": ("Este arquivo NÃO contém segredos: chaves de IA e de "
                  "corretora ficam de fora, de propósito. No servidor novo, "
                  "informe cada credencial no painel (Configuração → APIs)."),
    }
    return pacote


@app.post("/v1/mudanca/importar")
async def importar_tudo(req: Request, _=Depends(auth)):
    """Restaura num servidor novo. Idempotente: importar duas vezes não
    duplica nem corrompe.

    Segredos NUNCA entram por aqui: pacote antigo (/1) que traga chaves tem
    os segredos descartados e relatados — credencial se digita no painel do
    servidor novo, nunca se restaura de arquivo."""
    pacote = await _json_body(req)
    if pacote.get("formato") not in ("dz23.mudanca/1", "dz23.mudanca/2"):
        raise HTTPException(422, "formato de pacote desconhecido")
    tid = request_tenant(req)
    restaurado = []

    cfg = pacote.get("configuracao") or {}
    if cfg:
        limpo = {k: v for k, v in cfg.items()
                 if k in ALLOWED_KEYS and k not in SECRETish
                 and isinstance(v, str) and v.strip()}
        descartados = sorted(k for k in cfg if k in SECRETish)
        if limpo:
            save_cfg({**load_cfg(tid), **limpo}, tid)
            restaurado.append(f"configuração ({len(limpo)} itens)")
        if descartados:
            restaurado.append(
                f"{len(descartados)} segredo(s) descartado(s) por segurança — "
                "informe no painel: " + ", ".join(descartados))
    omitidos = pacote.get("segredos_omitidos") or []
    if omitidos:
        restaurado.append(
            f"{len(omitidos)} segredo(s) não viajam no pacote — "
            "informe no painel")

    _desk_reset(tid)
    d = _desk_of(req)
    armazem = d.get("armazem")
    if armazem:
        if pacote.get("aprendizado"):
            armazem.salvar(tid, "aprendizado", pacote["aprendizado"])
            restaurado.append(
                f"aprendizado ({len(pacote['aprendizado'].get('desfechos', []))} "
                "operações)")
        if pacote.get("mesa"):
            armazem.salvar(tid, "mesa", pacote["mesa"])
            restaurado.append("estado da mesa")

    assin = pacote.get("assinatura")
    if assin:
        from tradingagents.billing.woovi import Subscription
        try:
            SUBS.save(Subscription.from_row({**assin, "tenant_id": tid}))
            restaurado.append("assinatura")
        except Exception:
            pass

    _desk_reset(tid)          # recarrega já com o que foi restaurado
    return {"ok": True, "restaurado": restaurado,
            "proxima_acao": "Abra a Mesa de Agentes e confira o aprendizado"}


@app.post("/v1/desk/reconciliar")
def desk_reconciliar(request: Request, _=Depends(auth)):
    """Confere ordens incertas COM A CORRETORA e libera o portão se estiver
    tudo certo. Sem acesso à corretora, o portão permanece fechado."""
    d = _DESKS.get(request_tenant(request))
    if d is None:
        raise HTTPException(409, "mesa não carregada")
    coord = d["coordinator"]
    corretora = coord.broker

    def consultar(client_order_id: str):
        if not hasattr(corretora, "fetch_order_by_client_id"):
            raise RuntimeError("corretora não expõe consulta por ordem")
        return corretora.fetch_order_by_client_id(client_order_id)

    def consultar_posicoes():
        if not hasattr(corretora, "fetch_positions"):
            raise RuntimeError("corretora não expõe consulta de posições")
        return corretora.fetch_positions()

    def consultar_fills(client_order_id: str, exchange_order_id: str = ""):
        if not hasattr(corretora, "fetch_order_fills"):
            raise RuntimeError("corretora não expõe consulta de execuções")
        return corretora.fetch_order_fills(client_order_id, exchange_order_id)

    def consultar_ordens_abertas():
        if not hasattr(corretora, "fetch_open_orders"):
            raise RuntimeError("corretora não expõe consulta de ordens abertas")
        return corretora.fetch_open_orders()

    def consultar_caixa():
        if not hasattr(corretora, "fetch_cash"):
            raise RuntimeError("corretora não expõe consulta de caixa")
        return corretora.fetch_cash()

    tem_abertas = hasattr(corretora, "fetch_open_orders")
    tem_consulta = hasattr(corretora, "fetch_order_by_client_id")
    tem_posicoes = hasattr(corretora, "fetch_positions")
    tem_fills = hasattr(corretora, "fetch_order_fills")
    tem_caixa = hasattr(corretora, "fetch_cash")
    resultado = coord.reconciliar(
        consultar if tem_consulta else None,
        consultar_posicoes=consultar_posicoes if tem_posicoes else None,
        consultar_fills=consultar_fills if tem_fills else None,
        consultar_ordens_abertas=(consultar_ordens_abertas if tem_abertas
                                  else None),
        consultar_caixa=consultar_caixa if tem_caixa else None)
    prova_testnet = _registrar_prova_testnet(request, d, resultado)
    return {**resultado,
            **({"homologacao_testnet": prova_testnet}
               if prova_testnet is not None else {}),
            "acao": ("portão liberado: pode operar" if resultado["seguro"] else
                     "confira as divergências na corretora antes de operar")}
