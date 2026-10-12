"""TradingAgents Operável — serviço completo: API + painel de configuração e
operação embutidos. Uma senha (ACCESS_PASSWORD), um volume (/data), um deploy.

Config vive em /data/config.json (chmod 600), editada PELO PAINEL, aplicada a
cada job — sem redeploy, sem tocar em env no Coolify. Segredos nunca voltam
inteiros ao navegador (exibição mascarada)."""
import asyncio
import contextlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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

DATA = Path(os.environ.get("DATA_DIR", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
CFG_PATH = DATA / "config.json"
DB = DATA / "jobs.db"
LIVE_HALT_PATH = DATA / "live-halts.json"
HEALTH: dict = {"armazenamento": "?", "avisos": []}
LOGGER = logging.getLogger("tradingagents.service")

# Personal trading lab feature gate: disabled by default to keep runtime safe.
PERSONAL_TRADING_LAB_ENABLED = (
    os.environ.get("PERSONAL_TRADING_LAB_ENABLED", "false").lower() == "true"
)

def _require_personal_lab_enabled() -> None:
    """Fail-closed: the personal exchange catalog is never exposed implicitly."""
    if not PERSONAL_TRADING_LAB_ENABLED:
        raise HTTPException(404, "personal trading lab desabilitado")


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


def remote_reconciliation_snapshot() -> dict:
    raise RuntimeError("provider remoto de reconciliação não configurado")


PAPER_RECONCILIATION = ReconciliationMonitor(PAPER_ADAPTER.reconciliation_snapshot, remote_reconciliation_snapshot)
EXECUTION_ROUTER = ExecutionRouter(risk_engine=RiskEngine(), audit_log=AUDIT_LOG, event_bus=EVENT_BUS, kill_switch=KILL_SWITCH, metrics=METRICS, reconciliation_guard=lambda: bool(PAPER_RECONCILIATION.run_once().is_safe))
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
SECRETish = tuple(k for k in ALLOWED_KEYS if k.endswith(("_KEY", "_SECRET")))

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
    }


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


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp

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


# (rest of file continues unchanged; below we only modify the personal endpoints to call guard)


@app.get("/v1/personal/exchanges")
def personal_exchanges(request: Request, _=Depends(auth)):
    """Authenticated read-only list; does not connect, trade, or validate exchange APIs."""
    _require_personal_lab_enabled()
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
def personal_exchange_capabilities(exchange_id: str, request: Request, _=Depends(auth)):
    """Inspect local CCXT advertised features without API credentials or network calls."""
    _require_personal_lab_enabled()
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

