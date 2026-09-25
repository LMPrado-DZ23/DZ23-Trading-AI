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
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.routing import Match

from tradingagents.agency import AgencyTask, build_llm_handler
from tradingagents.agency.store import AgencyTaskStore
from tradingagents.agency_runtime import AgencyRuntime
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


def _resolve_data_dir() -> Path:
    requested = Path(os.environ.get("DATA_DIR", "/data"))
    try:
        requested.mkdir(parents=True, exist_ok=True)
        return requested
    except OSError as exc:
        fallback = Path(os.environ.get("TMPDIR", "/tmp")) / "dz23-data"
        fallback.mkdir(parents=True, exist_ok=True)
        logging.getLogger("tradingagents.service").warning(
            "DATA_DIR indisponível (%s); usando fallback temporário", type(exc).__name__)
        return fallback


DATA = _resolve_data_dir()
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
MULTI_TENANT_REQUESTED = os.environ.get("MULTI_TENANT_ENABLED", "false").lower() == "true"
MULTI_TENANT_ENABLED = os.environ.get("MULTI_TENANT_ENABLED", "false").lower() == "true"
# SAAS_MODE (§7): trava fail-closed do produto SaaS. Ligado, EXIGE multi-tenant
# + signing key + billing store transacional (DATABASE_URL/Postgres). Config
# ausente NUNCA cai no modo gratuito legado — o boot falha alto e claro, em vez
# de degradar em silêncio para um modo sem cobrança.
from tradingagents import portable, saas_config as _saas  # noqa: E402

SAAS_MODE = _saas.is_truthy(os.environ.get("SAAS_MODE"))
_TENANT_SIGNING_KEY = os.environ.get("TENANT_TOKEN_SIGNING_KEY")
TENANT_STORE = None
if MULTI_TENANT_ENABLED:
    # Fora do SAAS_MODE, configuração errada NÃO mata o contêiner: desliga o
    # multi-tenant, mantém o serviço de pé e diz como corrigir. Em SAAS_MODE, ao
    # contrário, propaga o erro (fail-closed) — ver _enforce_saas_mode().
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
        if SAAS_MODE:
            raise RuntimeError(
                f"SAAS_MODE ligado mas o multi-tenant não inicializou: {_exc}. "
                "SaaS não pode cair no modo legado — corrija a configuração "
                "(TENANT_TOKEN_SIGNING_KEY 32+, DATABASE_URL) e reinicie.") from _exc
        MULTI_TENANT_ENABLED = False
        HEALTH["avisos"].append(
            f"multi-tenant desligado: {_exc}. Defina TENANT_TOKEN_SIGNING_KEY "
            "com 32+ caracteres aleatórios e reinicie para habilitar contas.")
TENANT_BOOTSTRAP_TOKEN = os.environ.get("TENANT_BOOTSTRAP_TOKEN", "")


def _enforce_saas_mode() -> None:
    """Trava fail-closed do SAAS_MODE (§7). Chamada DEPOIS que TENANT_STORE e o
    billing store (SUBS) existem. Em SAAS_MODE exige, sem exceção:
      - MULTI_TENANT_ENABLED de fato ligado (não caiu para legado);
      - TENANT_STORE inicializado;
      - signing key presente;
      - DATABASE_URL (banco transacional) — SQLite não sustenta SaaS;
      - billing store REAL (não o sentinela _UnavailableStore).
    Qualquer requisito ausente aborta o boot — nunca degrada para o modo
    gratuito legado."""
    if not SAAS_MODE:
        return
    faltas = _saas.missing_saas_requirements(
        multi_tenant=MULTI_TENANT_ENABLED,
        tenant_store_ok=TENANT_STORE is not None,
        signing_key=_TENANT_SIGNING_KEY,
        database_url=os.environ.get("DATABASE_URL"),
        billing_store_ok=not isinstance(SUBS, _UnavailableStore),
    )
    if faltas:
        raise RuntimeError(
            "SAAS_MODE ligado mas faltam requisitos (fail-closed, sem cair no "
            "modo gratuito legado): " + "; ".join(faltas) + ".")
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
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY", "OPENAI_COMPATIBLE_API_KEY",
    "FRED_API_KEY", "ALPHA_VANTAGE_API_KEY",
    "TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE",
    "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET",
    "TRADINGAGENTS_DESK_FEED", "TRADINGAGENTS_DESK_BROKER",
    "TRADINGAGENTS_DESK_INSTRUMENTS", "TRADINGAGENTS_DESK_CAPITAL",
    "LIVE_MAX_ORDER_VALUE", "LIVE_MAX_DAILY_LOSS",
    "LIVE_TRADING_CONFIRM",
    "ALERTA_TELEGRAM_TOKEN", "ALERTA_TELEGRAM_CHAT", "ALERTA_WEBHOOK_URL",
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
    "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET",
    "ALERTA_TELEGRAM_TOKEN", "ALERTA_TELEGRAM_CHAT", "ALERTA_WEBHOOK_URL",
    "LIVE_TRADING_CONFIRM",
}
# Rede de segurança: qualquer chave futura com cara de credencial também
# entra, mesmo que alguém esqueça de declará-la.
SECRETish = tuple(sorted(
    {k for k in ALLOWED_KEYS
     if k in SEGREDOS_DECLARADOS
     or k.endswith(("_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_WEBHOOK_URL"))}))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
bearer = HTTPBearer(auto_error=False)


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

_TRUSTED_PROXIES = frozenset(
    p.strip() for p in os.environ.get("TRUSTED_PROXIES", "").split(",") if p.strip())


def _client_ip(request: Request) -> str:
    """Resolve o IP apenas quando o peer é um proxy explicitamente confiável."""
    peer = request.client.host if request.client else "?"
    trusted_networks = []
    for item in os.environ.get("TRUSTED_PROXY_NETWORKS", "").split(","):
        item = item.strip()
        if item:
            try:
                trusted_networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                LOGGER.warning("ignoring invalid TRUSTED_PROXY_NETWORKS entry")
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        peer_ip = None
    trusted = peer in _TRUSTED_PROXIES or (
        peer_ip is not None and any(peer_ip in network for network in trusted_networks))
    if not trusted:
        return peer
    forwarded = [item.strip() for item in request.headers.get("x-forwarded-for", "").split(",") if item.strip()]
    for candidate in reversed(forwarded):
        if candidate not in _TRUSTED_PROXIES:
            return candidate
    return forwarded[0] if forwarded else peer

def _tenant_permission(request: Request) -> str:
    # §10: encerrar a PRÓPRIA sessão é direito de todo usuário autenticado —
    # 'read' é a capacidade que todos os papéis têm (inclui viewer e auditor).
    if request.url.path == "/v1/auth/logout":
        return "read"
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
    if not PASSWORD:
        raise HTTPException(500, "defina ACCESS_PASSWORD no ambiente")
    ip = _client_ip(request)
    rec = _FAILS.get(ip)
    if rec and rec["n"] >= MAX_FAILS:
        wait = int(rec["until"] - time.time())
        if wait > 0:
            raise HTTPException(429, f"muitas tentativas erradas — espera {wait // 60 + 1} min")
        _FAILS.pop(ip, None)
    if not c or not hmac.compare_digest(c.credentials, PASSWORD):
        r = _FAILS.setdefault(ip, {"n": 0, "until": 0})
        r["n"] += 1
        r["until"] = time.time() + LOCK_SECONDS
        raise HTTPException(401, "não autenticado")
    _FAILS.pop(ip, None)
    return True


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
    email = str(body.get("email", ""))
    password = str(body.get("password", ""))
    tenant_id = str(body.get("tenant_id", "")).strip()
    ip = _client_ip(req)
    # §4: rate limit PERSISTENTE por (IP, e-mail) — sobrevive a restart e é
    # compartilhado entre instâncias. Bloqueio com backoff exponencial.
    espera = TENANT_STORE.login_lock_seconds(ip, email)
    if espera > 0:
        raise HTTPException(429, {"erro": "muitas tentativas; tente mais tarde",
                                  "retry_after_s": espera})
    try:
        if tenant_id:
            token = TENANT_STORE.login(email, password, tenant_id)
            TENANT_STORE.clear_login_failures(ip, email)
            return {"access_token": token, "token_type": "bearer", "expires_in": 3600}
        # SEM Tenant ID: o backend descobre o workspace pelo e-mail+senha. Se
        # houver vários, devolve a seleção (só após a senha correta — não enumera).
        resultado = TENANT_STORE.login_by_email(email, password)
    except (KeyError, AuthError):
        TENANT_STORE.record_login_failure(ip, email)
        # Anti-enumeração: mensagem GENÉRICA, idêntica para e-mail inexistente,
        # senha errada ou usuário sem vínculo com o tenant.
        raise HTTPException(401, "credenciais inválidas") from None
    TENANT_STORE.clear_login_failures(ip, email)
    if "token" in resultado:
        return {"access_token": resultado["token"], "token_type": "bearer",
                "expires_in": 3600, "tenant_id": resultado["tenant_id"]}
    return {"select_workspace": resultado["select"]}


@app.get("/v1/auth/status")
def auth_status():
    return {
        "multi_tenant": MULTI_TENANT_ENABLED,
        "bootstrap_required": bool(MULTI_TENANT_ENABLED and TENANT_STORE and TENANT_STORE.tenant_count() == 0),
        "live_trading": False,
    }


@app.get("/v1/auth/me")
def auth_me(principal=Depends(auth)):
    if isinstance(principal, Principal):
        return {"user_id": principal.user_id, "email": principal.email, "tenant_id": principal.tenant_id, "role": principal.role, "permissions": sorted(principal.permissions)}
    return {"user_id": "legacy", "email": "legacy", "tenant_id": "legacy", "role": "admin", "permissions": ["admin"]}


@app.post("/v1/auth/logout")
def tenant_logout(request: Request, c: HTTPAuthorizationCredentials = Depends(bearer), _=Depends(auth)):
    # §10: logout revoga o bearer E o step-up da PRÓPRIA sessão. Revogar um sid
    # inexistente é no-op idempotente (UPDATE 0 linhas, não levanta). Mas FALHA de
    # PERSISTÊNCIA (banco fora) NÃO pode ser apresentada como revogação confirmada:
    # nesse caso respondemos 503, porque o bearer pode continuar válido.
    if MULTI_TENANT_ENABLED and TENANT_STORE and c:
        principal = getattr(request.state, "principal", None)
        try:
            TENANT_STORE.revoke(c.credentials)                       # invalida o bearer
            if isinstance(principal, Principal):
                TENANT_STORE.revoke_step_up_for_session(principal.token_id)  # §5
        except Exception as exc:  # noqa: BLE001 — persistência falhou; não mentir
            raise HTTPException(
                503, "logout não confirmado (falha ao revogar a sessão); tente novamente"
            ) from exc
    return {"ok": True}


@app.post("/v1/auth/step-up")
async def auth_step_up(req: Request, principal=Depends(auth)):
    """Reautenticação forte (§1): valida a credencial AGORA e emite um token de
    step-up aleatório, escopado por `purpose`, curto (<=5min) e single-use. As
    operações sensíveis (export/import) exigem esse token, não a senha crua.
    A senha e o token NÃO são logados."""
    body = await _json_body(req)
    senha = str(body.get("password", ""))
    purpose = str(body.get("purpose", "export")).strip().lower()
    if purpose not in _STEPUP_PURPOSES:
        raise HTTPException(422, f"purpose deve ser um de {sorted(_STEPUP_PURPOSES)}")
    if isinstance(principal, Principal) and TENANT_STORE:
        # §5: export/import exige ADMIN já na EMISSÃO; rate-limit persistente;
        # token vinculado à SESSÃO (principal.token_id).
        if not principal.can("admin"):
            raise HTTPException(403, "step-up de export/import exige administrador")
        # §6: conta OAuth-only não tem senha do usuário — step-up por senha não é
        # fluxo válido. Bloqueia export/import até haver reautenticação do provedor.
        if TENANT_STORE.is_oauth_only(principal.user_id):
            raise HTTPException(403, {"erro": "conta OAuth: export/import exige "
                                              "reautenticação pelo provedor (não implementada)",
                                      "blocked_by": "STEP_UP_PROVIDER_REAUTH_REQUIRED"})
        ip = _client_ip(req)
        espera = TENANT_STORE.login_lock_seconds(ip, principal.email)
        if espera > 0:
            raise HTTPException(429, {"erro": "muitas tentativas de step-up",
                                      "retry_after_s": espera})
        try:
            TENANT_STORE._verify_credentials(principal.email, senha)
        except AuthError:
            TENANT_STORE.record_login_failure(ip, principal.email)
            raise HTTPException(401, "credencial inválida para step-up") from None
        TENANT_STORE.clear_login_failures(ip, principal.email)
        token = TENANT_STORE.create_step_up(principal.tenant_id, principal.user_id,
                                            purpose, session_id=principal.token_id)
    else:
        if not PASSWORD or not hmac.compare_digest(senha, PASSWORD):
            raise HTTPException(401, "credencial inválida para step-up")
        token = _legacy_stepup_create(purpose)
    return {"step_up_token": token, "purpose": purpose,
            "expires_in": 300, "single_use": True}


@app.post("/v1/admin/tenants")
async def create_tenant(req: Request, _=Depends(auth)):
    if not MULTI_TENANT_ENABLED or not TENANT_STORE:
        raise HTTPException(404, "multi-tenant desabilitado")
    # §9: criar tenant é operação da PLATAFORMA — tenant-admin NÃO pode.
    require_platform_admin(req)
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
    require_admin(req)                              # só admin do tenant
    body = await _json_body(req)
    # §9: valida o ALVO e o papel ANTES de criar nada (sem usuário órfão).
    target_tenant = str(body.get("tenant_id", request_tenant(req)))
    if target_tenant != request_tenant(req):
        raise HTTPException(403, "administrador só gerencia o próprio tenant")
    role = str(body.get("role", "viewer"))
    if "email" not in body or "password" not in body:
        raise HTTPException(422, "email e password obrigatórios")
    try:
        user_id = TENANT_STORE.add_user_to_tenant(   # atômico + validado
            target_tenant, str(body["email"]), str(body["password"]), role)
    except AuthError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"user_id": user_id, "tenant_id": target_tenant, "role": role}


@app.get("/v1/admin/users")
def list_tenant_users(request: Request, _=Depends(auth)):
    if not MULTI_TENANT_ENABLED or not TENANT_STORE:
        return {"users": []}
    require_admin(request)                           # §7: diagnóstico do tenant é de admin
    return {"users": TENANT_STORE.list_members(request_tenant(request))}


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# ENTITLEMENTS CENTRAL: toda rota MUTÁVEL (POST/PUT/PATCH/DELETE) é classificada
# em `write_policy` (determinístico, sem IA), por (método, TEMPLATE de rota) —
# não por prefixo genérico, que poderia liberar uma ação perigosa dentro da
# mesma rota. O guard resolve o template casado e aplica a política:
#   ALLOW_READONLY -> passa;  BLOCK_READONLY / não-classificada -> bloqueia
#   (fail-closed);  ACTION_AWARE -> o handler decide (ele pode ler o corpo com
#   segurança e validar a AÇÃO no servidor, ex.: kill-switch engage vs reset).
# Rota mutável nova sem classificação cai no bloqueio; `tests/test_route_policy`
# falha se alguém esquecer de classificá-la. (Os métodos mutáveis vêm de
# WP.MUTATING_METHODS, lidos em tempo de request para não depender da ordem de
# import — o módulo write_policy é importado mais abaixo, com o resto do billing.)


def _tenant_autenticado(request: Request):
    """Resolve o tenant do bearer SEM depender do fluxo de rota. Token ausente
    ou inválido -> None (a auth da rota devolve 401; o gate não interfere)."""
    if not MULTI_TENANT_ENABLED or not TENANT_STORE:
        return None
    authz = request.headers.get("authorization", "")
    if authz[:7].lower() != "bearer ":
        return None
    try:
        return TENANT_STORE.authenticate(authz[7:].strip()).tenant_id
    except Exception:                                # noqa: BLE001
        return None


def _template_da_rota(request: Request) -> str:
    """Template FastAPI da rota casada (ex.: '/v1/jobs/{jid}/cancel'), para a
    política classificar por rota EXATA — não por prefixo. Sem casar, devolve o
    caminho cru (a política não terá entrada e o guard bloqueia por padrão)."""
    for route in request.app.routes:
        try:
            match, _ = route.matches(request.scope)
        except Exception:                            # noqa: BLE001
            continue
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


def _conta_readonly_do_request(request: Request) -> str | None:
    """Estado somente-leitura da conta do bearer, ou None quando não há sessão
    válida (aí a auth da rota devolve 401; o gate não intervém)."""
    tenant = _tenant_autenticado(request)
    if tenant is None:
        return None
    estado, _ = estado_da_conta(tenant)
    return estado if ACCT.is_read_only(estado) else None


@app.middleware("http")
async def entitlements_guard(request, call_next):
    if MULTI_TENANT_ENABLED and request.method.upper() in WP.MUTATING_METHODS:
        acesso = WP.policy_for(request.method, _template_da_rota(request))
        # ALLOW passa; ACTION_AWARE é decidida no handler; BLOCK/None bloqueia.
        if acesso not in (WP.WriteAccess.ALLOW_READONLY, WP.WriteAccess.ACTION_AWARE):
            estado = _conta_readonly_do_request(request)
            if estado is not None:
                return JSONResponse(status_code=402, content={
                    "erro": "conta somente-leitura",
                    "estado": estado,
                    "acao": "Assine em Plano e pagamento para voltar a "
                            "operar. Seus dados continuam salvos.",
                })
    return await call_next(request)

# Provedores gratuitos/locais de primeira classe: todos falam o protocolo
# OpenAI-compatível do núcleo, cada um com sua URL e sua chave própria
# (a credencial dedicada OPENAI_COMPATIBLE_API_KEY é herança do Gate A:
# um backend redirecionado jamais recebe a chave da OpenAI de verdade).
FREE_PROVIDERS = {
    "groq":       {"url": "https://api.groq.com/openai/v1", "key_from": "GROQ_API_KEY",
                   "deep": "llama-3.3-70b-versatile", "quick": "llama-3.1-8b-instant"},
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


def tenant_cfg_path(tenant_id: str = "legacy") -> Path:
    if tenant_id == "legacy":
        return CFG_PATH
    path = DATA / "tenants" / tenant_id / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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
            cfg.update(json.loads(path.read_text()))
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


def configure_agency_llm(runtime, tenant_id: str) -> str | None:
    """Configura handlers LLM por instância; retorna diagnóstico sem segredo."""
    if all(agent_id in runtime.agency.executor.handlers for agent_id in _AGENCY_LLM_AGENT_IDS):
        return None
    try:
        cfg = load_cfg(tenant_id)
        conf, resolved_env = resolve_llm(cfg)
        provider = str(conf.get("llm_provider", "")).strip()
        model = str(conf.get("quick_think_llm") or conf.get("deep_think_llm") or "").strip()
        if not provider or not model:
            return "LLM não configurado para a agência"
        api_key = resolved_env.get("OPENAI_COMPATIBLE_API_KEY")
        if not api_key:
            for key_name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
                if cfg.get(key_name):
                    api_key = cfg[key_name]
                    break
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
            if agent_id in runtime.agency.executor.handlers:
                continue
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
        return None
    except Exception as exc:
        LOGGER.warning("LLM da agência indisponível para tenant %s: %s", tenant_id, type(exc).__name__)
        return f"LLM da agência indisponível ({type(exc).__name__})"

def save_cfg(cfg: dict, tenant_id: str = "legacy"):
    path = tenant_cfg_path(tenant_id)
    escrever_json_atomico(path, cfg)
    os.chmod(path, 0o600)


def request_tenant(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.tenant_id if isinstance(principal, Principal) else "legacy"


_PLATFORM_ADMIN_EMAILS = frozenset(
    e.strip().lower() for e in os.environ.get("PLATFORM_ADMIN_EMAILS", "").split(",") if e.strip())


def is_platform_admin(request: Request) -> bool:
    """Platform-admin (dono da PLATAFORMA) ≠ admin de tenant (§9). Em
    multi-tenant, é quem está em PLATFORM_ADMIN_EMAILS. No self-host legado, o
    portador do ACCESS_PASSWORD é o dono."""
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal.email.strip().lower() in _PLATFORM_ADMIN_EMAILS
    return not MULTI_TENANT_ENABLED


def require_platform_admin(request: Request) -> None:
    if not is_platform_admin(request):
        raise HTTPException(403, "requer administrador da PLATAFORMA")


def require_admin(request: Request):
    """Exige papel de ADMINISTRADOR do tenant. No self-host legado (sem
    multi-tenant), quem porta o ACCESS_PASSWORD é o admin único. Viewer,
    analyst, auditor etc. levam 403."""
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        if not principal.can("admin"):
            raise HTTPException(403, "requer papel de administrador")
        return principal
    if MULTI_TENANT_ENABLED:
        raise HTTPException(403, "requer papel de administrador")
    return None


def _principal_actor(request: Request) -> str:
    """Actor para AUDITORIA derivado da sessão provada — NUNCA do corpo. Em
    multi-tenant é o e-mail do Principal; no legado, 'legacy-admin'."""
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal.email
    return "legacy-admin"


# STEP-UP por TOKEN (§1): o segredo NÃO viaja no header. O usuário prova a
# credencial em POST /v1/auth/step-up, recebe um token aleatório escopado,
# curto (<=5min) e single-use, e o apresenta em X-Step-Up-Token nas operações
# sensíveis. Multi-tenant persiste no banco (single-use atômico, entre
# instâncias); o self-host legado usa um cache em processo (instância única).
_STEPUP_PURPOSES = frozenset({"export", "import"})
_LEGACY_STEPUP: dict[str, dict] = {}


def _legacy_stepup_hash(token: str) -> str:
    return hashlib.sha256(("legacy|" + token).encode()).hexdigest()


def _legacy_stepup_create(purpose: str, ttl: int = 300) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    for k in [k for k, v in _LEGACY_STEPUP.items() if v["expires"] < now]:
        _LEGACY_STEPUP.pop(k, None)
    _LEGACY_STEPUP[_legacy_stepup_hash(token)] = {
        "expires": now + ttl, "purpose": purpose, "used": False}
    return token


def _legacy_stepup_consume(token: str, purpose: str) -> bool:
    rec = _LEGACY_STEPUP.get(_legacy_stepup_hash(token))
    if not rec or rec["used"] or rec["expires"] < time.time() or rec["purpose"] != purpose:
        return False
    rec["used"] = True
    return True


def _require_step_up(request: Request, purpose: str) -> None:
    """Exige um token de step-up válido, no escopo `purpose`, e o CONSOME."""
    token = request.headers.get("x-step-up-token", "")
    if not token:
        raise HTTPException(401, {"erro": "step-up necessário",
                                  "como": "POST /v1/auth/step-up"})
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal) and TENANT_STORE:
        ok = TENANT_STORE.consume_step_up(token, principal.tenant_id,
                                          principal.user_id, purpose,
                                          session_id=principal.token_id)
    else:
        ok = _legacy_stepup_consume(token, purpose)
    if not ok:
        raise HTTPException(401, "step-up inválido, expirado, de outra sessão ou já usado")


def upd(jid, tenant_id, **kw):
    JOB_STORE.update(jid, tenant_id, **kw)

_cancel = set()
_lock = threading.Lock()


class _JobCancelled(Exception):
    """Sinal interno de cancelamento cooperativo entre fases do job."""


def _abort_if_cancelled(jid, tenant_id):
    # §7: checkpoint cooperativo — a fonte da verdade é o banco (entre instâncias).
    if JOB_STORE.is_cancelled(jid, tenant_id) or (tenant_id, jid) in _cancel:
        raise _JobCancelled()


def run_job(jid, ticker, date, tenant_id="legacy"):
    with _lock:  # um job por vez: env é global do processo
        runtime = runtime_for_tenant_id(tenant_id)
        # Fonte da verdade do cancelamento é o BANCO (observável entre
        # instâncias); o set em memória é só cache.
        if JOB_STORE.is_cancelled(jid, tenant_id) or (tenant_id, jid) in _cancel:
            upd(jid, tenant_id, status="cancelled")
            _cancel.discard((tenant_id, jid))
            return
        upd(jid, tenant_id, status="running")
        try:
            _abort_if_cancelled(jid, tenant_id)                  # checkpoint 1
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
            _abort_if_cancelled(jid, tenant_id)                  # checkpoint 2 (antes do LLM)
            graph = TradingAgentsGraph(config=conf)
            t_up = ticker.upper()
            asset_type = "crypto" if ("/" in t_up or t_up.endswith(("-USD", "-USDT", "-BTC"))) else "stock"
            # BLOCKED_BY_ARCHITECTURE: graph.propagate() é uma chamada MONOLÍTICA
            # (LangGraph) sem hook de cancelamento cooperativo interno. O cancel é
            # honrado ENTRE fases (checkpoints 1-3), NÃO no meio da chamada do LLM.
            # Cancelar durante a propagação exigiria refatorar o grafo para
            # streaming/checkpoints por nó — fora do escopo deste patch.
            state, decision = graph.propagate(ticker, date, asset_type=asset_type)
            _abort_if_cancelled(jid, tenant_id)                  # checkpoint 3 (antes de gravar)
            reports = {k: v for k, v in (state or {}).items()
                       if isinstance(v, str) and ("report" in k or "decision" in k)}
            # guarda o mark de transporte + identidade: é o que permite EXECUTAR
            # a decisão depois, passando pelo gate de capital honestamente.
            reports["_transport_mark"] = (state or {}).get("transport_security")
            reports["_identity"] = {"company_of_interest": (state or {}).get("company_of_interest"),
                                     "trade_date": (state or {}).get("trade_date")}
            upd(jid, tenant_id, status="completed", decision=str(decision)[:4000],
                result=json.dumps(reports, ensure_ascii=False, default=str)[:200000])
            runtime.agency.set_agent_status("strategy-agent", "PAPER_TRADING")
            runtime.agency.set_agent_status("market-scanner", "WAITING_SETUP")
        except _JobCancelled:
            upd(jid, tenant_id, status="cancelled")
            _cancel.discard((tenant_id, jid))
        except Exception as e:
            runtime.agency.set_agent_status("strategy-agent", "ERROR")
            upd(jid, tenant_id, status="failed",
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


async def _json_body_limited(req: Request, max_bytes: int) -> dict:
    """Como _json_body, mas com TETO de tamanho (§4): corta o corpo pelo
    Content-Length e pelos bytes reais antes de desserializar."""
    declarado = req.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > max_bytes:
        raise HTTPException(413, "corpo grande demais")
    raw = await req.body()
    if len(raw) > max_bytes:
        raise HTTPException(413, "corpo grande demais")
    try:
        data = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(422, "corpo não é JSON válido") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, "corpo deve ser um objeto JSON")
    return data


_EXPORT_BODY_MAX = 4096                  # só a frase-senha
_IMPORT_BODY_MAX = 12 * 1024 * 1024      # envelope cifrado (ciphertext <= 8 MiB)


@app.get("/healthz")
def hz():
    return {"status": "degraded" if HEALTH["avisos"] else "ok",
            "app": "tradingagents-agency/1",
            "armazenamento": HEALTH["armazenamento"],
            "banco": DATABASE_ORIGEM,
            "avisos": HEALTH["avisos"]}


@app.get("/readyz")
def readyz():
    blocked = KILL_SWITCH.state.enabled
    return {"status": "blocked" if blocked else "ready", "paper": PAPER_ADAPTER.healthcheck(), "event_sequence": EVENT_BUS.latest_sequence, "kill_switch": blocked}


@app.get("/metrics")
def metrics(_=Depends(auth)):
    return METRICS.snapshot()

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


@app.get("/setup", include_in_schema=False)
@app.get("/setup/", include_in_schema=False)
def setup_inicial():
    """Bootstrap da PRIMEIRA agência — rota separada do /app público. A API
    `/v1/auth/bootstrap` exige o token e recusa quando já existe tenant (uso
    único); esta página só expõe o formulário nesse cenário."""
    return FileResponse(STATIC / "setup.html")


@app.get("/termos", include_in_schema=False)
def termos():
    return FileResponse(STATIC / "termos.html")


@app.get("/privacidade", include_in_schema=False)
def privacidade():
    return FileResponse(STATIC / "privacidade.html")


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    return FileResponse(STATIC / "robots.txt", media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml():
    return FileResponse(STATIC / "sitemap.xml", media_type="application/xml")


@app.get("/static/marca/{arquivo}", include_in_schema=False)
def marca(arquivo: str):
    """Serve os arquivos da marca (favicon, ícone de atalho, logo).

    Caminho fixo e nome simples: a pasta é pública por natureza, mas aceitar
    nome arbitrário abriria leitura de qualquer arquivo do disco.
    """
    permitidos = {"favicon.ico", "favicon-32.png", "favicon-64.png",
                  "dz23-logo.png", "dz23-logo-512.png", "dz23-logo-192.png"}
    if arquivo not in permitidos:
        raise HTTPException(status_code=404, detail="arquivo não encontrado")
    caminho = STATIC / "marca" / arquivo
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="arquivo não encontrado")
    return FileResponse(caminho, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "marca" / "favicon.ico")

@app.get("/v1/config")
def get_cfg(request: Request, _=Depends(auth)):
    cfg = load_cfg(request_tenant(request))
    out = {}
    for k in ALLOWED_KEYS:
        v = cfg.get(k, "")
        if not v:
            out[k] = {"set": False}
        elif k in SECRETish:
            out[k] = {"set": True, "hint": "•••" + str(v)[-4:]}
        else:
            out[k] = {"set": True, "value": v}
    return {"config": out}

@app.post("/v1/config")
async def set_cfg(req: Request, _=Depends(auth)):
    require_write(req)                    # somente-leitura bloqueia a escrita
    body = await _json_body(req)
    tenant_id = request_tenant(req)
    cfg = load_cfg(tenant_id)
    saved = []
    for k, v in (body.get("vars") or {}).items():
        if k in ALLOWED_KEYS and isinstance(v, str) and v.strip():
            cfg[k] = v.strip()
            saved.append(k)
    if not saved:
        raise HTTPException(400, "nada para salvar")
    save_cfg(cfg, tenant_id)
    # Configuração que muda a mesa (corretora, feed, capital, limites) exige
    # reconstrução — senão o cliente salva e nada acontece até reiniciar.
    if any(k.startswith(("TRADINGAGENTS_DESK", "LIVE_", "EXCHANGE_")) or
           k in ("TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE")
           for k in saved):
        _desk_reset(tenant_id)
    return {"saved": saved}

# §2: símbolo começa com alfanumérico e só admite [A-Z0-9 . _ : / -], até 20 chars.
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,19}$")


@app.post("/v1/jobs")
async def new_job(req: Request, _=Depends(auth)):
    require_write(req)                    # somente-leitura bloqueia a escrita
    b = await _json_body(req)
    t, d = str(b.get("ticker", "")).strip().upper(), str(b.get("trade_date", "")).strip()
    # §2 (XSS): allowlist estrita de SÍMBOLO. Aceita ações e cripto (NVDA,
    # BTC-USD, BTC/USDT, PETR4.SA, ETH:USDT) e RECUSA qualquer coisa com <, >,
    # &, aspas, espaço ou '=' — o vetor de XSS nunca entra no ledger. A
    # camada de render também escapa (defesa em profundidade p/ dados antigos).
    if not t or not _TICKER_RE.match(t):
        raise HTTPException(422, "ticker inválido — use um símbolo como NVDA, BTC-USD ou BTC/USDT")
    if len(d) != 10:
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
    # PROVA de posse ANTES de qualquer efeito: job de outro tenant (ou
    # inexistente) => 404 e ZERO efeito. Nada é escrito no _cancel global antes
    # disso — senão A derrubaria o job de B só sabendo o id.
    job = JOB_STORE.get(jid, tenant_id)
    if job is None:
        raise HTTPException(404, "job não encontrado")
    JOB_STORE.request_cancel(jid, tenant_id)            # DURÁVEL (fonte da verdade)
    _cancel.add((tenant_id, jid))                       # cache em memória (opcional)
    if job["status"] == "queued":
        upd(jid, tenant_id, status="cancelled")         # UPDATE ... WHERE id AND tenant
        return {"ok": True, "cancelado": jid, "estado": "cancelled"}
    if job["status"] == "running":
        # §7: cancelamento cooperativo — sinalizado (durável); o job encerra no
        # próximo checkpoint entre fases.
        return {"ok": True, "cancelado": jid, "estado": "solicitado"}
    return {"ok": True, "cancelado": jid, "estado": job["status"]}


@app.get("/v1/broker/status")
def broker_status(request: Request, _=Depends(auth)):
    """Valida a configuração de corretora SEM enviar ordem nenhuma."""
    cfg = load_cfg(request_tenant(request))
    env = {k: cfg.get(k, "") for k in
           ("TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE",
            "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "LIVE_TRADING_CONFIRM",
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
    if action not in ("engage", "reset"):
        raise HTTPException(422, "action deve ser engage ou reset")
    # Policy por AÇÃO validada no servidor: ENGAGE reduz risco (liberado mesmo
    # em conta somente-leitura); RESET reabilita a execução → exige escrita e é
    # bloqueado em read-only (require_write levanta 402), salvo conta ativa.
    if WP.kill_switch_action_access(action) is not WP.WriteAccess.ALLOW_READONLY:
        require_write(req)
    if action == "engage":
        state = runtime.kill_switch.engage(str(body.get("reason", "bloqueio administrativo")), actor=_principal_actor(req))
    elif action == "reset":
        report = runtime.reconciliation.run_once()
        try:
            state = runtime.kill_switch.reset(actor=_principal_actor(req), reconciliation_ok=report.is_safe)
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
from tradingagents.analytics import ConversionCounter  # noqa: E402
from tradingagents.billing import (  # noqa: E402
    account_state as ACCT,
    catalog as CAT,
    write_policy as WP,
)
from tradingagents.billing.store import (  # noqa: E402
    BillingUnavailable,
    ChargeConflict,
    SubscriptionStore,
)
from tradingagents.billing.woovi import (  # noqa: E402
    BillingError,
    WebhookRejected,
    WooviClient,
    verify_webhook,
)


class _UnavailableStore:
    """Sentinela FAIL-CLOSED: quando o Postgres canônico não sobe no boot, o
    billing fica DESCONHECIDO. Não cai para SQLite (fonte concorrente que
    poderia inventar trials); toda leitura levanta BillingUnavailable, e as
    rotas mutáveis ficam bloqueadas até o banco voltar."""

    def get(self, tenant_id):
        raise BillingUnavailable("billing store não inicializado (Postgres)")

    def start_trial_for(self, tenant_id):
        raise BillingUnavailable("billing store não inicializado (Postgres)")

    def save(self, sub):
        raise BillingUnavailable("billing store não inicializado (Postgres)")

    def record_paid_charge(self, charge_id, tenant_id, plan_code, amount_cents,
                           currency="BRL", **kw):
        raise BillingUnavailable("billing store não inicializado (Postgres)")


def _build_subscription_store():
    """FONTE CANÔNICA ÚNICA. Com DATABASE_URL, é Postgres — indisponível no
    boot vira UNKNOWN (somente-leitura), NUNCA um SQLite paralelo que poderia
    conceder acesso gravável ou reiniciar trials durante um outage."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        try:
            return SubscriptionStore(DATA / "subscriptions.db", dsn)
        except Exception as exc:
            HEALTH["avisos"].append(
                "assinaturas: PostgreSQL indisponível no boot "
                f"({type(exc).__name__}); billing em UNKNOWN (somente-leitura).")
            return _UnavailableStore()
    # Sem DATABASE_URL: SQLite local é a única fonte (dev/Paper).
    return SubscriptionStore(DATA / "subscriptions.db", None)


SUBS = _build_subscription_store()
_enforce_saas_mode()   # §7: aborta o boot se SAAS_MODE ligado sem os requisitos


def estado_da_conta(tenant_id: str):
    """(estado_canônico, sub|None). FAIL-CLOSED: billing indisponível =>
    UNKNOWN_BILLING (somente-leitura). NÃO inicia nem estende trial."""
    try:
        sub = SUBS.get(tenant_id)
    except BillingUnavailable:
        return ACCT.UNKNOWN_BILLING, None
    return ACCT.derive_state(sub), sub
CONV = ConversionCounter()   # conversão anônima (sem PII), em memória


def _woovi_public_key() -> str:
    return (os.environ.get("WOOVI_WEBHOOK_PUBLIC_KEY")
            or load_cfg().get("WOOVI_WEBHOOK_PUBLIC_KEY") or "")


def require_plan(request: Request, feature: str = "mesa"):
    """Alias legado de `require_write`: bloqueia escrita quando a conta está
    somente-leitura (trial expirado, vencida, suspensa, billing desconhecido),
    mas NUNCA o pagamento nem o login."""
    return require_write(request)


@app.get("/v1/billing/planos")
def billing_plans(_=Depends(auth)):
    # Catálogo CANÔNICO (mesma fonte da landing). Não expõe starter/pro legados.
    return {"planos": [{"codigo": p["code"], "nome": p["nome"],
                        "preco": p["preco"], "preco_centavos": p["preco_cents"],
                        "meses": p["meses"], "equiv_mensal": p["equiv_mensal"],
                        "desconto_pct": p["desconto_pct"], "economia": p["economia"],
                        "maior_economia": p["maior_economia"],
                        "recursos": ["produto completo — PAPER e SHADOW"]}
                       for p in CAT.resumo()]}


@app.get("/v1/billing/status")
def billing_status(request: Request, _=Depends(auth)):
    estado, sub = estado_da_conta(request_tenant(request))
    base = {"estado": estado, "gateway_configurado": WooviClient().configured}
    if sub is None:
        return {**base, "plano": None, "status": estado, "ativo": False,
                "expira_em": None}
    # `ativo` = a conta TEM acesso agora (TRIALING ou ACTIVE). `pago` distingue
    # a assinatura paga do teste — antes `ativo` só via ACTIVE mentia no trial.
    return {**base, "plano": sub.plan_code, "nome": sub.plan.name,
            "status": sub.status,
            "ativo": estado in (ACCT.TRIALING, ACCT.ACTIVE),
            "pago": estado == ACCT.ACTIVE,
            "expira_em": sub.expires_at.isoformat()}


def require_write(request: Request):
    """Portão CENTRAL de ESCRITA. Bloqueia quando a conta está somente-leitura
    (TRIAL_EXPIRED / PAST_DUE / SUSPENDED / NOT_STARTED / UNKNOWN_BILLING) —
    leitura, exportação, pagamento e login seguem, e NADA é apagado."""
    # Cobrança/trial só existem no modo multi-tenant (SaaS). Self-host/dev
    # (ACCESS_PASSWORD) não tem assinatura e não é bloqueado.
    if not MULTI_TENANT_ENABLED:
        return ACCT.ACTIVE
    tenant = request_tenant(request)
    estado, _ = estado_da_conta(tenant)
    if ACCT.is_read_only(estado):
        raise HTTPException(402, {
            "erro": "conta somente-leitura",
            "estado": estado,
            "acao": "Assine em Plano e pagamento para voltar a operar. "
                    "Seus dados continuam salvos.",
        })
    return estado


@app.get("/v1/account/state")
def account_state_endpoint(request: Request, _=Depends(auth)):
    """Estado server-side da conta para a UI (sem PII). NÃO inicia trial."""
    estado, sub = estado_da_conta(request_tenant(request))
    return {**ACCT.describe(estado),
            "plan": sub.plan_code if sub else None,
            "expires_at": sub.expires_at.isoformat() if sub else None}


@app.post("/v1/trial/start")
def trial_start(request: Request, principal=Depends(auth)):
    """Inicia o teste de 24h EXPLICITAMENTE (idempotente, exatamente uma vez).
    ÚNICO caminho para começar o trial — nem criar conta, nem login (senha ou
    OAuth), nem GET de estado iniciam. Pré-condições (fail-closed):
      1) sessão autenticada de MEMBRO real de tenant (não self-host/legacy);
      2) papel ADMIN do tenant (onboarding: quem controla a conta é quem inicia);
      3) trial NUNCA usado antes — garantido por `start_trial_for` (INSERT
         condicional): concorrência não cria dois; restart/outage não estende;
         TRIAL_EXPIRED não volta a TRIALING.
    Aprovação por convite é por construção: o cadastro público está FECHADO,
    então todo tenant nasce de bootstrap/admin (convite explícito). Verificação
    de e-mail é infra pendente (§6) e NÃO é reimplementada aqui.

    DECISÃO ASSUMIDA: sem provedor de verificação de e-mail no ambiente, a
    pré-condição 'e-mail verificado' é substituída pela 'aprovação por convite
    por construção' acima; quando o signup público abrir, este gate ganha a
    checagem de e-mail antes de liberar."""
    if not isinstance(principal, Principal):
        raise HTTPException(403, "início de teste exige conta multi-tenant autenticada")
    if not principal.can("admin"):
        raise HTTPException(403, "apenas o administrador do tenant inicia o teste")
    tenant = request_tenant(request)
    # O ESTADO ATUAL decide (§4): só NOT_STARTED pode iniciar. TRIALING é
    # idempotente (devolve o mesmo teste, sem estender). ACTIVE / TRIAL_EXPIRED
    # / PAST_DUE / SUSPENDED NUNCA reiniciam o teste — 409. Billing indisponível
    # é fail-closed (503), nunca inventa trial.
    estado_atual, sub_atual = estado_da_conta(tenant)
    if estado_atual == ACCT.UNKNOWN_BILLING:
        raise HTTPException(503, {"erro": "cobrança indisponível",
                                  "estado": ACCT.UNKNOWN_BILLING})
    if estado_atual == ACCT.TRIALING and sub_atual is not None:
        return {**ACCT.describe(estado_atual), "plan": sub_atual.plan_code,
                "expires_at": sub_atual.expires_at.isoformat(), "idempotent": True}
    if estado_atual != ACCT.NOT_STARTED:
        raise HTTPException(409, {
            "erro": "teste indisponível",
            "estado": estado_atual,
            "motivo": "o teste de 24h começa uma única vez, só a partir de uma "
                      "conta nova; ele não reinicia após expirar/vencer/suspender.",
        })
    try:
        sub = SUBS.start_trial_for(tenant)   # INSERT condicional: uso único real
    except BillingUnavailable:
        raise HTTPException(503, {"erro": "cobrança indisponível",
                                  "estado": ACCT.UNKNOWN_BILLING}) from None
    estado = ACCT.derive_state(sub)
    return {**ACCT.describe(estado), "plan": sub.plan_code,
            "expires_at": sub.expires_at.isoformat()}


_ANALYTICS_HITS: dict = {}
_ANALYTICS_MAX, _ANALYTICS_WINDOW = 120, 60      # 120 eventos/min por IP
_ANALYTICS_BODY_MAX = 2048                        # bytes; evento é minúsculo


def _analytics_rate_limit(ip: str) -> None:
    agora = time.time()
    janela = [t for t in _ANALYTICS_HITS.get(ip, []) if agora - t < _ANALYTICS_WINDOW]
    if len(janela) >= _ANALYTICS_MAX:
        raise HTTPException(429, "muitos eventos deste endereço")
    janela.append(agora)
    _ANALYTICS_HITS[ip] = janela


async def _analytics_body(req: Request) -> dict:
    """Leitura ENDURECIDA do corpo do evento: exige application/json, limita
    o tamanho (Content-Length e bytes reais) e devolve sempre um objeto."""
    ctype = req.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(415, "envie application/json")
    declarado = req.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > _ANALYTICS_BODY_MAX:
        raise HTTPException(413, "corpo grande demais")
    raw = await req.body()
    if len(raw) > _ANALYTICS_BODY_MAX:
        raise HTTPException(413, "corpo grande demais")
    try:
        data = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(422, "corpo não é JSON válido") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, "corpo deve ser um objeto JSON")
    return data


@app.post("/v1/analytics/event", status_code=204)
async def analytics_event(req: Request):
    """Evento de conversão ANÔNIMO (público). Sem PII: só nome+origem da
    allowlist; nada de IP, e-mail ou identificador é guardado. Endurecido:
    content-type, tamanho e rate-limit; responde 204 sem corpo. O agregado é
    best-effort NÃO-durável (memória) — não é fonte contábil."""
    _analytics_rate_limit(_client_ip(req))
    body = await _analytics_body(req)
    # Chaves limitadas: event pela allowlist; source recortado antes do teto.
    CONV.record(str(body.get("event", ""))[:64], str(body.get("source", "none"))[:64])
    return Response(status_code=204)


@app.get("/v1/analytics/summary")
def analytics_summary(_=Depends(auth)):
    """Agregado de conversão (sem PII) — para o time, atrás de login.
    Best-effort e NÃO-durável: reinício zera; várias instâncias não somam."""
    return {**CONV.summary(), "durabilidade": "best-effort (memória, não contábil)"}


@app.post("/v1/billing/checkout")
async def billing_checkout(req: Request, _=Depends(auth)):
    """Gera a cobrança PIX. Não ativa nada: quem ativa é o webhook assinado."""
    body = await _json_body(req)
    plano = str(body.get("plano", "")).strip()
    # Checkout aceita SOMENTE o catálogo canônico ativo.
    if not CAT.is_valid_code(plano):
        raise HTTPException(422, "plano inválido")
    tenant = request_tenant(req)
    correlation = f"dz23-{tenant}-{plano}-{time.strftime('%Y%m')}"
    try:
        resposta = WooviClient().create_charge(tenant, plano, correlation)
    except BillingError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True, "correlation_id": correlation,
            **WooviClient.charge_view(resposta)}


_WEBHOOK_BODY_MAX = 64 * 1024        # webhook é pequeno; corta DoS de corpo grande


@app.post("/v1/webhooks/woovi")
async def woovi_webhook(request: Request):
    """PÚBLICO por necessidade — verificado por assinatura RSA sobre o corpo
    BRUTO. Além da assinatura, valida (§8): tipo do evento, correlação, plano do
    catálogo, MOEDA (BRL), VALOR == preço do plano, tenant existente e um
    identificador de cobrança. Aplica com IDEMPOTÊNCIA DURÁVEL (ledger por
    charge_id): reenvio não credita duas vezes. Se o banco falhar DEPOIS do
    pagamento, responde 5xx e a Woovi reenvia — nada é perdido nem creditado
    pela metade (a aplicação é atômica)."""
    # §3: limita o corpo ANTES de lê-lo (Content-Length) e depois (bytes reais).
    declarado = request.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > _WEBHOOK_BODY_MAX:
        raise HTTPException(413, "corpo do webhook grande demais")
    raw = await request.body()
    if len(raw) > _WEBHOOK_BODY_MAX:
        raise HTTPException(413, "corpo do webhook grande demais")
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

    # Correlação: dz23-<tenant>-<plano>-<AAAAMM>. Parse pela DIREITA para
    # aceitar tenant com hífen (plano e período não têm hífen).
    correlation = str(charge.get("correlationID", ""))
    partes = correlation.split("-")
    if len(partes) < 4 or partes[0] != "dz23":
        return {"ok": True, "ignorado": "cobrança não pertence a este sistema"}
    plano = partes[-2]
    tenant = "-".join(partes[1:-2])

    if not CAT.is_valid_code(plano):
        raise HTTPException(422, "plano fora do catálogo")
    # §5 fail-closed: moeda AUSENTE não vira BRL por default; tem de vir e ser BRL.
    moeda_raw = charge.get("currency")
    if not moeda_raw:
        raise HTTPException(422, "moeda ausente no evento")
    moeda = str(moeda_raw).upper()
    if moeda != "BRL":
        raise HTTPException(422, "moeda não suportada (apenas BRL)")
    esperado = CAT.CATALOGO[plano].preco_cents
    valor = charge.get("value")
    if not isinstance(valor, int) or valor != esperado:       # valor tem de bater
        raise HTTPException(422, "valor pago não confere com o plano")
    charge_id = str(charge.get("identifier") or charge.get("id") or "").strip()
    if not charge_id:                                          # sem id, sem replay-safety
        raise HTTPException(422, "cobrança sem identificador")
    if MULTI_TENANT_ENABLED and TENANT_STORE and tenant != "legacy" \
            and not TENANT_STORE.tenant_exists(tenant):
        raise HTTPException(404, "tenant desconhecido")

    event_id = str(evento.get("id") or charge.get("transactionID") or charge_id)
    payload_hash = hashlib.sha256(raw).hexdigest()            # hash do corpo bruto
    try:
        aplicado, sub = SUBS.record_paid_charge(
            charge_id, tenant, plano, valor, moeda, provider="woovi",
            event_id=event_id, correlation=correlation, payload_hash=payload_hash)
    except ChargeConflict:
        # Mesmo charge_id, payload divergente: conflito DURÁVEL (já registrado no
        # ledger de incidentes), 409, NUNCA 'replay ok' e NENHUMA ativação.
        # Alerta sem segredos: só ids/tenant, nunca corpo/assinatura/chaves.
        logging.getLogger("dz23.webhook").warning(
            "webhook_conflict charge_id=%s tenant=%s event_id=%s", charge_id, tenant, event_id)
        raise HTTPException(409, {"erro": "charge_id já visto com dados "
                                          "diferentes; possível colisão/ataque"}) from None
    except BillingUnavailable:
        # Pagamento recebido, banco fora do ar: NÃO confirma. 503 -> Woovi
        # reenvia; a transação é tudo-ou-nada, então reprocessa limpo.
        raise HTTPException(503, {"erro": "cobrança recebida mas o banco está "
                                          "indisponível; reenvie o webhook"}) from None
    if not aplicado:                                          # replay/idempotente
        return {"ok": True, "replay": True, "tenant": tenant,
                "plano": (sub.plan_code if sub else plano)}
    assert sub is not None
    return {"ok": True, "tenant": tenant, "plano": sub.plan_code,
            "expira_em": sub.expires_at.isoformat()}

# ================================================================== CADASTRO
# Autoatendimento: o cliente cria a própria agência, recebe o período de
# teste e pode assinar. Sem isto não há produto para vender — só instalação
# manual pelo dono.
#
# DECISÃO ASSUMIDA: cadastro FECHADO por padrão (fail-closed). O signup
# público cria tenants, usuários e dados reais; abri-lo é decisão explícita.
# Ausência da variável, valor inválido ou erro de digitação ⇒ DESLIGADO;
# só um valor afirmativo declarado ("true"/"1"/"yes"/"sim"/"on") liga. Os
# portões de verificação de e-mail, rate-limit e convite são pré-requisitos
# desta chave (ver §5) e devem estar prontos antes de ligá-la em produção.
_SIGNUP_SIM = frozenset({"1", "true", "yes", "sim", "on"})
SIGNUP_ENABLED = str(os.environ.get("SIGNUP_ENABLED", "")).strip().lower() in _SIGNUP_SIM
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
            "horas_de_teste": CAT.TRIAL_HORAS,
            "planos": [{"codigo": p["code"], "nome": p["nome"], "preco": p["preco"]}
                       for p in CAT.resumo()]}


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
        # Criação ATÔMICA (tenant + usuário + vínculo). Falha desfaz tudo:
        # sem tenant órfão. Mensagem genérica de propósito — não confirma se
        # o e-mail já existe (anti-enumeração).
        tenant_id, _user_id = TENANT_STORE.create_account(nome, email, senha)
    except AuthError:
        raise HTTPException(409, "não foi possível concluir o cadastro; "
                                 "se já tiver conta, faça login") from None
    except Exception:
        raise HTTPException(500, "não foi possível criar a conta agora") from None
    # §3: criar conta NÃO inicia trial nem concede acesso. A conta nasce
    # NOT_STARTED (somente-leitura). O teste de 24h só começa por AÇÃO
    # explícita do administrador em POST /v1/trial/start.
    token = TENANT_STORE.login(email, senha, tenant_id)
    return {"ok": True, "tenant_id": tenant_id, "access_token": token,
            "estado": ACCT.NOT_STARTED,
            "proximo_passo": "Inicie seu teste de 24h em /v1/trial/start quando "
                             "quiser começar — criar conta não inicia o teste."}


# ============================================================ LOGIN SOCIAL
# OAuth só PROVA identidade; quem concede acesso continua sendo o tenant
# store. Conta nova pelo social nasce NOT_STARTED — o teste de 24h só começa
# por ação explícita em /v1/trial/start (§3), nunca no login/cadastro.
from tradingagents.identity.oauth import (  # noqa: E402
    ACCOUNT_CREATION_PROVIDERS,
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
    vive em /v1/operational-readiness, autenticado e por inquilino, ainda
    não implementado. Responder aqui o que é de lá vazaria estado de cliente
    para quem nem fez login.
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
                  "o estado por inquilino virá autenticado em "
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
        if provider not in ACCOUNT_CREATION_PROVIDERS:
            # §6: só provedores com prova de e-mail verificado criam conta.
            # Microsoft autentica quem JÁ tem conta, mas não cria uma nova.
            raise HTTPException(403, f"criação de conta via {provider} desabilitada "
                                     "(verificação de e-mail não comprovada)")
        try:
            tenant_id, user_id = TENANT_STORE.create_account(   # atômico
                f"Agência de {nome}", email, senha_interna, oauth_only=True)
        except AuthError:
            # Corrida: e-mail passou a existir entre a checagem e a criação.
            tenant_id, user_id = TENANT_STORE.find_membership_by_email(email)
            if tenant_id is None:
                raise HTTPException(409, "não foi possível concluir o cadastro") from None
            token = TENANT_STORE.issue_token_for_user(user_id, tenant_id)
            return {"ok": True, "tenant_id": tenant_id, "access_token": token,
                    "email": email, "conta_nova": False}
        # §3: login social cria a conta, mas NÃO inicia trial. A conta nasce
        # NOT_STARTED; o teste de 24h começa só em POST /v1/trial/start.
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


def _platform_allows_live() -> bool:
    """§1: decisão da PLATAFORMA sobre dinheiro real. AMBAS as flags do serviço
    precisam estar ligadas (LIVE_TRADING e REAL_MONEY_EXECUTION); ausência => NÃO.
    É o bloqueio que a frase de confirmação e as credenciais NÃO contornam."""
    def _on(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")
    return _on("LIVE_TRADING") and _on("REAL_MONEY_EXECUTION")


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
    from tradingagents.desk.storage import ArmazemDeEstado
    # Servidor é descartável; o aprendizado não pode ser. Com banco externo,
    # o histórico dos agentes sobrevive à troca de máquina.
    armazem = ArmazemDeEstado(os.environ.get("DATABASE_URL"),
                              diretorio=DATA / "desks")
    memoria = MemoriaDeDesempenho(DATA / "desks" / tenant_id / "aprendizado.json",
                                  armazem=armazem, tenant_id=tenant_id)
    exchange_cfg = (cfg.get("TRADINGAGENTS_EXCHANGE") or "binance").lower()
    traders = default_desk(universe, memoria=memoria, exchange=exchange_cfg,
                           capital=capital)
    for t in traders:
        for stage in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
            t.spec.move_to(stage)

    risk = RiskEngine(mandate)
    ledger = Ledger(opening_cash=mandate.capital)
    data_dir = DATA / "desks" / tenant_id
    broker, broker_mode = build_desk_broker(
        cfg, final_state={}, journal_path=str(data_dir / "broker.jsonl"),
        paper_price=Decimal("100"), platform_allows_live=_platform_allows_live())
    coord = ExecutionCoordinator(mandate, ledger, risk,
                                 worker_id=f"worker-{tenant_id}", broker=broker)
    # Identidade e destino do relatório de reconciliação.
    coord.tenant_id = tenant_id
    coord.armazem = armazem
    # §4: persistência DURÁVEL do OMS ANTES de cada despacho externo. Diferente do
    # save de fim de ciclo (best-effort), este LEVANTA em falha — e o coordenador
    # BLOQUEIA o envio se não conseguir lembrar da ordem.
    coord.persist_hook = lambda: armazem.salvar(tenant_id, "oms", coord.snapshot_estado())
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
            """Consulta o provedor do INQUILINO.

            A versão anterior escrevia a credencial em os.environ sem trava:
            duas mesas de inquilinos diferentes rodam em threads próprias, e
            a janela entre escrever e restaurar permitia o inquilino A usar a
            chave do B — ou a chave de um sobrar no processo.

            O ambiente do processo deixou de ser o transporte da credencial:
            ela vai explicitamente para o cliente. O lock só permanece para o
            caso em que a biblioteca ainda exija variável de ambiente, e
            nesse caminho a janela fica fechada de ponta a ponta.
            """
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.llm_clients.factory import create_llm_client
            llm_conf, llm_env = resolve_llm(cfg)
            conf = {**DEFAULT_CONFIG, **llm_conf}
            import inspect
            aceita_credencial = "api_key" in inspect.signature(
                create_llm_client).parameters
            if aceita_credencial:
                chave = next((v for v in llm_env.values() if v), "")
                cliente = create_llm_client(conf.get("llm_provider"), conf,
                                            api_key=chave)
                return (cliente.complete(prompt) if hasattr(cliente, "complete")
                        else str(cliente.invoke(prompt)))
            with _lock:                      # janela fechada: um por vez
                anterior = {k: os.environ.get(k) for k in llm_env}
                os.environ.update(llm_env)
                try:
                    cliente = create_llm_client(conf.get("llm_provider"), conf)
                    return (cliente.complete(prompt)
                            if hasattr(cliente, "complete")
                            else str(cliente.invoke(prompt)))
                finally:
                    for k, antes in anterior.items():
                        if antes is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = antes

        analista = AnalistaDeContexto(_consultar)

    orch = DeskOrchestrator(mandate, traders, worker_id=f"worker-{tenant_id}",
                            risk=risk, positions_provider=ledger.open_positions,
                            memoria=memoria, exchange=exchange_cfg,
                            analista=analista)

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

    desk = {"orchestrator": orch, "ledger": ledger, "coordinator": coord,
            "memoria": memoria, "armazem": armazem, "recuperacao": recuperacao,
            "guardian": PositionGuardian(coord), "cycles": [],
            "feed_kind": feed_kind, "broker_mode": broker_mode, "scheduler": None}

    def _persistir_oms():
        try:
            armazem.salvar(tenant_id, "oms", coord.snapshot_estado())
        except Exception:
            HEALTH["avisos"].append(
                "não foi possível persistir o estado financeiro da mesa")

    def _record(out):
        _persistir_oms()
        # A cada ciclo, avalia e envia alertas — é o que acorda o dono sem
        # ele precisar abrir o painel.
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
            if alertas:
                resultado = _notificador(tenant_id).enviar(alertas)
                if resultado.get("falhas"):
                    METRICS.inc("alerts.delivery_failed", len(resultado["falhas"]))
                    LOGGER.warning("alert delivery failed: %s", len(resultado["falhas"]))
        except Exception as exc:
            METRICS.inc("alerts.delivery_callback_failed")
            LOGGER.exception("alert callback failed: %s", type(exc).__name__)
            # O alerta não derruba o ciclo, mas a falha permanece observável.
        desk["cycles"].append({"em": _now().isoformat(), "origem": feed_kind,
                               "resumo": out.get("resumo", ""),
                               "vetos": out.get("vetos", []),
                               "abstencoes": out.get("abstencoes", []),
                               "erros": out.get("erros", []),
                               "descartados": out.get("descartados", []),
                               "ordens": out.get("ordens", [])})
        del desk["cycles"][:-30]

    desk["scheduler"] = DeskScheduler(
        orch, coord, snapshot_provider=snaps_fn, price_provider=prices_fn,
        state_path=data_dir / "state.json", on_cycle=_record,
        armazem=armazem, tenant_id=tenant_id,
        interval_seconds=int(os.environ.get("DESK_INTERVAL_SECONDS", "300")),
        # §8: o laço revalida billing a cada ciclo. Conta somente-leitura (trial
        # expirado/suspenso) ou billing indisponível (UNKNOWN_BILLING => read-only)
        # pausa novo trabalho, sem liquidar posições. Self-host não tem billing.
        entitlement_check=(
            (lambda tid=tenant_id: not ACCT.is_read_only(estado_da_conta(tid)[0]))
            if MULTI_TENANT_ENABLED else None))
    return desk


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
        desk = _desk_boot(tid)
        _DESKS[tid] = desk
    return desk


def _desk_reset(tenant_id: str) -> None:
    """Configuração mudou: parada controlada e reconstrução no próximo uso."""
    desk = _DESKS.pop(tenant_id, None)
    if desk and desk.get("scheduler") and desk["scheduler"].running:
        desk["scheduler"].stop()


@app.get("/v1/desk/status")
def desk_status(request: Request, _=Depends(auth), __=Depends(require_plan)):
    try:
        d = _desk_of(request)
    except HTTPException:
        raise                     # 423 da parada de emergência precisa chegar íntegro
    except Exception as exc:
        raise HTTPException(503, f"mesa indisponível: {type(exc).__name__}: {exc}"[:300]) from exc
    orch, led, coord = d["orchestrator"], d["ledger"], d["coordinator"]
    return {
        "mandate": {"account": orch.mandate.account, "mode": orch.mandate.mode.value,
                    "capital": str(orch.mandate.capital),
                    "instruments": list(orch.mandate.instruments)},
        "agents": [{"id": t.spec.agent_id, "strategy": t.spec.strategy,
                    "version": t.spec.version, "stage": t.spec.stage.value,
                    "universe": list(t.universe)} for t in orch.traders],
        "risk": {"reservado": str(orch.risk.reserved_total),
                 "disponivel": str(orch.risk.available),
                 "instrumentos_abertos": sorted(orch.risk.open_instruments())},
        "orders": [{"id": r.order_id, "instrumento": r.instrumento,
                    "estado": r.state.value, "qtd": str(r.filled_qty),
                    "preco": str(r.avg_price), "nota": r.note}
                   for r in coord.orders.values()],
        "pendencias_reconciliacao": [r.order_id for r in coord.pending_reconciliation()],
        "contabilidade": {"saldos": {k: str(v) for k, v in led.balances().items()},
                          "patrimonio": str(led.equity()),
                          "atribuicao": {k: str(v) for k, v in led.attribution().items()},
                          "balanceado": led.is_balanced()},
        "guardiao": d["guardian"].watchlist(),
        "agendador": d["scheduler"].status(),
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
    out = d["scheduler"].run_once()
    enviados, encerrados = [], []
    for o in out.get("ordens", []):
        enviados.append(o)
    return {"ok": True, "resumo": out.get("resumo", ""), "ordens": enviados,
            "encerradas": encerrados, "descartados": out.get("descartados", [])}


@app.post("/v1/desk/scheduler/{action}")
def desk_scheduler(action: str, request: Request, _=Depends(auth)):
    """Liga/desliga o laço autônomo. Ligado, roda sozinho — fechar o
    navegador não interrompe nada. Start reconcilia ANTES de retomar.

    §8 (ACTION_AWARE): `start` AUMENTA exposição => exige conta com assinatura
    (require_write bloqueia trial expirado/suspenso/billing indisponível).
    `stop` REDUZ risco => sempre acessível, mesmo somente-leitura — nunca deixar
    o dono sem conseguir PARAR a mesa. Parar NÃO liquida posições."""
    d = _desk_of(request)
    sched = d["scheduler"]
    if action == "start":
        require_write(request)               # só o start passa pelo gate de billing
        recuperacao = sched.recover()
        return {"ok": True, "iniciado": sched.start(), "recuperacao": recuperacao,
                "status": sched.status()}
    if action == "stop":
        sched.stop()
        return {"ok": True, "parada": "controlada — posições seguem sob o guardião",
                "a_encerrar": d["orchestrator"].drain(), "status": sched.status()}
    raise HTTPException(422, "ação deve ser start ou stop")


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


_LAST_BACKUP = [0.0]


def _backup_rate_limit(min_interval: int = 30) -> None:
    agora = time.time()
    if agora - _LAST_BACKUP[0] < min_interval:
        raise HTTPException(429, "backup muito frequente; aguarde")
    _LAST_BACKUP[0] = agora


@app.post("/v1/backup")
def backup_now(request: Request, _=Depends(auth)):
    """Backup de recuperação da PLATAFORMA (global, todos os inquilinos).

    §7/§8: em SAAS_MODE o backup GLOBAL é operação INTERNA da plataforma e NÃO
    é exposto por HTTP (403); fora do SaaS exige PLATFORM-ADMIN, é rate-limited,
    e a resposta NUNCA traz caminho interno — só nome e tamanho.
    """
    if SAAS_MODE:
        raise HTTPException(403, {"erro": "backup global é operação interna da "
                                          "plataforma; não disponível por HTTP em SaaS",
                                  "blocked_by": "PLATFORM_INTERNAL_ONLY"})
    require_platform_admin(request)
    _backup_rate_limit()
    from tradingagents.desk.backup import BackupError, create_backup
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destino = DATA / "backups" / f"dz23-backup-{stamp}.tar.gz"
    try:
        out = create_backup(DATA, destino)
    except BackupError as exc:
        raise HTTPException(422, str(exc)) from exc
    tam = out.get("bytes") if isinstance(out, dict) else None
    if tam is None and destino.exists():
        tam = destino.stat().st_size
    return {"ok": True, "arquivo": destino.name, "bytes": tam}   # sem path interno


@app.get("/v1/backups")
def list_backups(request: Request, _=Depends(auth)):
    if SAAS_MODE:
        raise HTTPException(403, {"erro": "listagem de backup global é interna da plataforma",
                                  "blocked_by": "PLATFORM_INTERNAL_ONLY"})
    require_platform_admin(request)          # §8: backup global é da plataforma
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
         "pronto": bool(cfg.get("EXCHANGE_API_KEY") and cfg.get("EXCHANGE_API_SECRET")),
         "acao": "Chave grátis em testnet.binance.vision"},
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
        # justamente o que precisa ser possível enquanto está parado. Se nem
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


def _load_live_halts() -> set[str]:
    try:
        body = json.loads(LIVE_HALT_PATH.read_text(encoding="utf-8"))
        return {str(item) for item in body if isinstance(item, str)}
    except (OSError, TypeError, ValueError):
        return set()


_LIVE_HALTED: set[str] = _load_live_halts()


def _persist_live_halts() -> None:
    escrever_json_atomico(LIVE_HALT_PATH, sorted(_LIVE_HALTED))
    os.chmod(LIVE_HALT_PATH, 0o600)


def _live_halted(tenant_id: str) -> bool:
    _LIVE_HALTED.clear()
    _LIVE_HALTED.update(_load_live_halts())
    return tenant_id in _LIVE_HALTED or _parada(tenant_id).bloqueia


@app.get("/v1/live/status")
def live_status(request: Request, _=Depends(auth)):
    """Diz, sem rodeio, se este inquilino pode operar dinheiro real e o que
    falta para poder."""
    cfg = load_cfg(request_tenant(request))
    d = _DESKS.get(request_tenant(request))
    frase_ok = (cfg.get("LIVE_TRADING_CONFIRM") or "").strip() == LIVE_CONFIRM_PHRASE
    checklist = [
        {"item": "Frase de autorização digitada", "pronto": frase_ok,
         "acao": f"Digite exatamente: {LIVE_CONFIRM_PHRASE}"},
        {"item": "Chaves da corretora configuradas",
         "pronto": bool(cfg.get("EXCHANGE_API_KEY") and cfg.get("EXCHANGE_API_SECRET")),
         "acao": "Cole a chave e o segredo da sua corretora"},
        {"item": "Teto por ordem definido",
         "pronto": bool(cfg.get("LIVE_MAX_ORDER_VALUE")),
         "acao": "Defina LIVE_MAX_ORDER_VALUE (valor máximo de UMA ordem)"},
        {"item": "Perda máxima diária definida",
         "pronto": bool(cfg.get("LIVE_MAX_DAILY_LOSS")),
         "acao": "Defina LIVE_MAX_DAILY_LOSS (quando parar no vermelho)"},
        {"item": "Modo da corretora em LIVE",
         "pronto": (cfg.get("TRADINGAGENTS_BROKER_MODE") or "") == "live",
         "acao": "Troque o modo da corretora para LIVE"},
        {"item": "Já operou em testnet",
         "pronto": bool(d and any(r.state.value == "filled"
                                  for r in d["coordinator"].orders.values())),
         "acao": "Rode a mesa em testnet e veja uma ordem preenchida antes"},
    ]
    faltam = [i for i in checklist if not i["pronto"]]
    return {"pode_operar_real": not faltam, "checklist": checklist,
            "pendencias": len(faltam),
            "aviso": ("Dinheiro real: perdas são suas e irreversíveis. O "
                      "sistema não promete lucro.")}


@app.post("/v1/live/parada-emergencia")
def live_emergency(request: Request, _=Depends(auth)):
    """Botão de pânico: bloqueia envio de ordens reais na hora.

    Esta rota NUNCA pode falhar: se a mesa nem inicia (por exemplo, a
    corretora está mal configurada), parar é trivialmente verdade — e ainda
    assim marcamos a parada, porque o dono clicou para parar."""
    tid = request_tenant(request)
    parada = _parada(tid)
    parada.acionar("acionada pelo painel", por=tid)
    _LIVE_HALTED.add(tid)
    _persist_live_halts()
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
    _LIVE_HALTED.discard(tid)
    _persist_live_halts()
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
    notificador = _notificador(tid)
    alertas = _estado_para_alertas(d)
    return {"ativos": [{"codigo": a.codigo, "severidade": a.severidade,
                        "titulo": a.titulo, "detalhe": a.detalhe, "acao": a.acao}
                       for a in alertas],
            "canal_configurado": notificador.configurado,
            "historico": notificador.historico[-20:],
            "como_configurar": ("Em Configuração: ALERTA_TELEGRAM_TOKEN e "
                                "ALERTA_TELEGRAM_CHAT, ou ALERTA_WEBHOOK_URL")}


@app.post("/v1/alertas/verificar")
def disparar_alertas(request: Request, _=Depends(auth)):
    """Avalia e ENVIA o que houver. O worker chama isto a cada ciclo."""
    tid = request_tenant(request)
    d = _desk_of(request)
    alertas = _estado_para_alertas(d)
    resultado = _notificador(tid).enviar(alertas)
    return {"avaliados": len(alertas), **resultado}


# ======================================================= TRANSPARÊNCIA ⑦
@app.get("/publico/{tenant_id}")
def pagina_publica(tenant_id: str):
    """Página PÚBLICA com o histórico real — inclusive as perdas.

    Só existe se o dono ligar (TRANSPARENCIA_PUBLICA=true). Mostra desempenho
    e comportamento; NUNCA saldo, chave, ordem individual ou dado de conta.
    Num mercado cheio de promessa vazia, mostrar o resultado real — com as
    perdas — é o argumento de venda mais forte que existe.
    """
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
@app.post("/v1/mudanca/exportar")
async def exportar_tudo(request: Request, _=Depends(auth)):
    """Exporta TUDO o que importa do inquilino — CIFRADO (§2/§4).

    É POST (a frase-senha vai no CORPO, não em header/URL, e GET não deve
    consumir token). Inclui SEGREDOS, então:
      - exige papel de ADMIN (viewer/auditor levam 403);
      - exige TOKEN de step-up single-use (a sessão sozinha não basta);
      - frase-senha FORTE (>=12) no corpo;
      - devolve arquivo CIFRADO (AES-256-GCM + AAD do tenant), attachment,
        Cache-Control: no-store — nunca segredos em JSON comum nem em cache.
    """
    require_admin(request)
    _require_step_up(request, "export")           # token single-use, não senha crua
    corpo = await _json_body_limited(request, _EXPORT_BODY_MAX)
    frase = str(corpo.get("passphrase", ""))
    if len(frase) < portable.MIN_PASSPHRASE:
        raise HTTPException(422, {"erro": "frase-senha fraca",
                                  "minimo": portable.MIN_PASSPHRASE})
    tid = request_tenant(request)
    d = _DESKS.get(tid)
    armazem = d.get("armazem") if d else None
    pacote = {
        "formato": "dz23.mudanca/1",
        "tenant_id": tid,
        "exportado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "configuracao": load_cfg(tid),
        "aprendizado": (armazem.carregar(tid, "aprendizado") if armazem else None),
        "mesa": (armazem.carregar(tid, "mesa") if armazem else None),
        # Sem assinatura no pacote: ela não é restaurável pelo cliente (§1).
    }
    aad = {"v": 2, "tenant": tid, "user": _principal_actor(request),
           "purpose": "export", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    envelope = portable.encrypt_package(pacote, frase, aad=aad)
    corpo = json.dumps(envelope).encode()
    return Response(content=corpo, media_type="application/octet-stream",
                    headers={
                        "Content-Disposition":
                            f'attachment; filename="dz23-mudanca-{tid}.enc"',
                        "Cache-Control": "no-store, max-age=0",
                        "Pragma": "no-cache",
                    })


@app.post("/v1/mudanca/importar")
async def importar_tudo(req: Request, _=Depends(auth)):
    """Restaura num servidor novo. Idempotente: importar duas vezes não
    duplica nem corrompe.

    SEGURANÇA (§1): restauração é operação de ADMIN. E o pacote do usuário
    NUNCA carrega assinatura: um trial não pode se auto-ativar importando
    `status=active`. A assinatura só muda por webhook validado ou operação
    administrativa server-side auditada — nunca por dado vindo do cliente."""
    require_admin(req)
    tid = request_tenant(req)
    # §4: corpo com teto. A frase-senha vem no CORPO (nunca em header). Para
    # pacote cifrado, o body é {"pacote": <envelope>, "passphrase": "..."}; um
    # pacote plano pode vir direto no corpo (compat).
    corpo = await _json_body_limited(req, _IMPORT_BODY_MAX)
    frase = str(corpo.get("passphrase", ""))
    pacote = corpo["pacote"] if isinstance(corpo.get("pacote"), dict) else corpo
    # §7: reautenticação (step-up) é exigida para TODO formato aceito — plano
    # (dz23.mudanca/1) ou cifrado. Compatibilidade NÃO é desvio da política de
    # segurança. Token single-use, vinculado à sessão; conta OAuth-only não
    # consegue emitir (STEP_UP_PROVIDER_REAUTH_REQUIRED) e fica bloqueada aqui.
    _require_step_up(req, "import")
    if pacote.get("formato") == portable.MAGIC:      # export cifrado
        if len(frase) < portable.MIN_PASSPHRASE:
            raise HTTPException(422, {"erro": "frase-senha ausente/fraca no corpo"})
        # AAD liga o pacote ao tenant de origem: importar o de OUTRO tenant é 403.
        if portable.envelope_aad(pacote).get("tenant") not in (tid, None):
            raise HTTPException(403, "este pacote pertence a outro tenant")
        try:
            pacote = portable.decrypt_package(pacote, frase)
        except Exception:
            raise HTTPException(400, "pacote cifrado ilegível (frase errada ou adulterado)") from None
    if not isinstance(pacote, dict) or pacote.get("formato") != "dz23.mudanca/1":
        raise HTTPException(422, "formato de pacote desconhecido")
    restaurado = []
    ignorado = []
    if pacote.get("assinatura") is not None:
        # Presente no pacote (export antigo) mas DELIBERADAMENTE ignorado.
        ignorado.append("assinatura (só muda por pagamento/webhook ou admin)")

    # §4/§7: valida o SCHEMA de TODAS as partes ANTES de aplicar qualquer uma.
    # Pacote inválido não pode deixar a configuração PARCIALMENTE alterada.
    cfg = pacote.get("configuracao") or {}
    if not isinstance(cfg, dict):
        raise HTTPException(422, "configuração do pacote inválida")
    aprend = pacote.get("aprendizado")
    mesa = pacote.get("mesa")
    if aprend is not None and not (isinstance(aprend, dict) and isinstance(aprend.get("desfechos", []), list)):
        raise HTTPException(422, "aprendizado do pacote inválido")
    if mesa is not None and not isinstance(mesa, dict):
        raise HTTPException(422, "estado da mesa do pacote inválido")

    # tudo validado => aplica (config primeiro, depois mesa/aprendizado)
    if cfg:
        limpo = {k: v for k, v in cfg.items()
                 if k in ALLOWED_KEYS and isinstance(v, str) and v.strip()}
        if limpo:
            save_cfg({**load_cfg(tid), **limpo}, tid)
            restaurado.append(f"configuração ({len(limpo)} itens)")

    _desk_reset(tid)
    d = _desk_of(req)
    armazem = d.get("armazem")
    if armazem:
        if aprend:
            armazem.salvar(tid, "aprendizado", aprend)
            restaurado.append(f"aprendizado ({len(aprend.get('desfechos', []))} operações)")
        if mesa:
            armazem.salvar(tid, "mesa", mesa)
            restaurado.append("estado da mesa")

    # NÃO restaura assinatura do pacote (§1): ver require_admin/ignorado acima.

    _desk_reset(tid)          # recarrega já com o que foi restaurado
    return {"ok": True, "restaurado": restaurado, "ignorado": ignorado,
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

    def consultar(client_order_id: str, exchange_order_id: str = ""):
        if not hasattr(corretora, "fetch_order_by_client_id"):
            raise RuntimeError("corretora não expõe consulta por ordem")
        return corretora.fetch_order_by_client_id(client_order_id, exchange_order_id)

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

    tem_abertas = hasattr(corretora, "fetch_open_orders")
    tem_consulta = hasattr(corretora, "fetch_order_by_client_id")
    tem_posicoes = hasattr(corretora, "fetch_positions")
    tem_fills = hasattr(corretora, "fetch_order_fills")
    resultado = coord.reconciliar(
        consultar if tem_consulta else None,
        consultar_posicoes=consultar_posicoes if tem_posicoes else None,
        consultar_fills=consultar_fills if tem_fills else None,
        consultar_ordens_abertas=(consultar_ordens_abertas if tem_abertas
                                  else None))
    return {**resultado,
            "acao": ("portão liberado: pode operar" if resultado["seguro"] else
                     "confira as divergências na corretora antes de operar")}
