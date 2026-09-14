"""TradingAgents Operável — serviço completo: API + painel de configuração e
operação embutidos. Uma senha (ACCESS_PASSWORD), um volume (/data), um deploy.

Config vive em /data/config.json (chmod 600), editada PELO PAINEL, aplicada a
cada job — sem redeploy, sem tocar em env no Coolify. Segredos nunca voltam
inteiros ao navegador (exibição mascarada)."""
import asyncio
import hmac
import json
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

from tradingagents.agency_runtime import AgencyRuntime
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

DATA = Path(os.environ.get("DATA_DIR", "/data")); DATA.mkdir(parents=True, exist_ok=True)
CFG_PATH = DATA / "config.json"; DB = DATA / "jobs.db"
HEALTH: dict = {"armazenamento": "?", "avisos": []}


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
AGENCY = AgencyRuntime(audit_log=AGENCY_AUDIT, event_bus=AGENCY_EVENT_BUS)
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
    ("strategy-agent", "strategy", "Strategy Researcher"),
    ("risk-advisor", "risk", "Risk Advisor"),
    ("portfolio-manager", "portfolio", "Portfolio Manager"),
    ("execution-planner", "execution", "Execution Planner"),
    ("reconciliation-agent", "monitoring", "Reconciliation"),
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
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else None) or (
        request.client.host if request.client else "?")

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
        return "analysis"
    return "analysis"


def auth(request: Request, c: HTTPAuthorizationCredentials = Depends(bearer)):
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
        if prov: conf["llm_provider"] = prov
        if url: conf["backend_url"] = url
        if deep: conf["deep_think_llm"] = deep
        if quick: conf["quick_think_llm"] = quick
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
        try: cfg.update(json.loads(path.read_text()))
        except ValueError: pass
    return cfg

def save_cfg(cfg: dict, tenant_id: str = "legacy"):
    path = tenant_cfg_path(tenant_id)
    path.write_text(json.dumps(cfg, indent=2)); os.chmod(path, 0o600)


def request_tenant(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.tenant_id if isinstance(principal, Principal) else "legacy"

def upd(jid, **kw):
    JOB_STORE.update(jid, **kw)

_cancel = set(); _lock = threading.Lock()

def run_job(jid, ticker, date, tenant_id="legacy"):
    with _lock:  # um job por vez: env é global do processo
        runtime = runtime_for_tenant_id(tenant_id)
        if jid in _cancel: upd(jid, status="cancelled"); return
        upd(jid, status="running")
        try:
            runtime.agency.set_agent_status("market-scanner", "ANALYZING")
            runtime.agency.set_agent_status("strategy-agent", "ANALYZING")
            cfg = load_cfg(tenant_id)
            for k, v in cfg.items():
                if k in ALLOWED_KEYS and v: os.environ[k] = str(v)
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
def index(): return FileResponse(STATIC / "index.html")

@app.get("/v1/config")
def get_cfg(request: Request, _=Depends(auth)):
    cfg = load_cfg(request_tenant(request))
    out = {}
    for k in ALLOWED_KEYS:
        v = cfg.get(k, "")
        if not v: out[k] = {"set": False}
        elif k in SECRETish: out[k] = {"set": True, "hint": "•••" + str(v)[-4:]}
        else: out[k] = {"set": True, "value": v}
    return {"config": out}

@app.post("/v1/config")
async def set_cfg(req: Request, _=Depends(auth)):
    body = await _json_body(req)
    tenant_id = request_tenant(req)
    cfg = load_cfg(tenant_id); saved = []
    for k, v in (body.get("vars") or {}).items():
        if k in ALLOWED_KEYS and isinstance(v, str) and v.strip():
            cfg[k] = v.strip(); saved.append(k)
    if not saved: raise HTTPException(400, "nada para salvar")
    save_cfg(cfg, tenant_id)
    # Configuração que muda a mesa (corretora, feed, capital, limites) exige
    # reconstrução — senão o cliente salva e nada acontece até reiniciar.
    if any(k.startswith(("TRADINGAGENTS_DESK", "LIVE_", "EXCHANGE_")) or
           k in ("TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE")
           for k in saved):
        _desk_reset(tenant_id)
    return {"saved": saved}

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
    if not result: raise HTTPException(404, "job não encontrado")
    return result

@app.post("/v1/jobs/{jid}/cancel")
def cancel(jid: str, request: Request, _=Depends(auth)):
    tenant_id = request_tenant(request)
    _cancel.add(jid)
    if JOB_STORE.status(jid, tenant_id) == "queued": upd(jid, status="cancelled")
    return {"ok": True}


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
    return {"runtime": "READY", "event_sequence": runtime.agency.event_bus.latest_sequence, "agents": len(runtime.agency.agents), "meetings": len(runtime.agency.meetings), "audit_log": str(DATA / "agency-events.jsonl")}


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
        self.agency = AgencyRuntime(audit_log=self.agency_audit, event_bus=EventBus(self.agency_audit))
        self.paper = PaperAdapter(state_path=root / "paper-state.json")
        self.shadow = ShadowAdapter(state_path=root / "shadow-state.json")
        self.kill_switch = KillSwitch(root / "kill-switch.json")
        self.metrics = MetricsRegistry()
        self.reconciliation = ReconciliationMonitor(self.paper.reconciliation_snapshot, lambda: (_ for _ in ()).throw(RuntimeError("provider remoto de reconciliação não configurado")))
        self.router = ExecutionRouter(risk_engine=RiskEngine(), audit_log=audit, event_bus=bus, kill_switch=self.kill_switch, metrics=self.metrics, reconciliation_guard=lambda: bool(self.reconciliation.last_report and self.reconciliation.last_report.is_safe))
        for agent_id, desk, role in (("market-scanner", "research", "Market Scanner"), ("technical-analyst", "research", "Technical Analyst"), ("strategy-agent", "strategy", "Strategy Researcher"), ("risk-advisor", "risk", "Risk Advisor"), ("portfolio-manager", "portfolio", "Portfolio Manager"), ("execution-planner", "execution", "Execution Planner"), ("reconciliation-agent", "monitoring", "Reconciliation")):
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
    tenant = request_tenant(request)
    sub = SUBS.ensure(tenant)
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
    memoria = MemoriaDeDesempenho(DATA / "desks" / tenant_id / "aprendizado.json")
    traders = default_desk(universe, memoria=memoria)
    for t in traders:
        for stage in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
            t.spec.move_to(stage)

    risk = RiskEngine(mandate)
    ledger = Ledger(opening_cash=mandate.capital)
    data_dir = DATA / "desks" / tenant_id
    broker, broker_mode = build_desk_broker(
        cfg, final_state={}, journal_path=str(data_dir / "broker.jsonl"),
        paper_price=Decimal("100"))
    coord = ExecutionCoordinator(mandate, ledger, risk, worker_id=f"worker-{tenant_id}",
                                 broker=broker)
    orch = DeskOrchestrator(mandate, traders, worker_id=f"worker-{tenant_id}",
                            risk=risk, positions_provider=ledger.open_positions,
                            memoria=memoria)

    feed_kind = (cfg.get("TRADINGAGENTS_DESK_FEED") or "fixture").strip().lower()
    if feed_kind == "real":
        from tradingagents.desk.market import MarketFeedProviders, PublicMarketFeed
        prov = MarketFeedProviders(
            PublicMarketFeed(cfg.get("TRADINGAGENTS_EXCHANGE") or "binance"),
            universe, timeframe="1h", limit=60)
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

    desk = {"orchestrator": orch, "ledger": ledger, "coordinator": coord,
            "memoria": memoria,
            "guardian": PositionGuardian(coord), "cycles": [],
            "feed_kind": feed_kind, "broker_mode": broker_mode, "scheduler": None}

    def _record(out):
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
        interval_seconds=int(os.environ.get("DESK_INTERVAL_SECONDS", "300")))
    return desk


def _desk_of(request: Request) -> dict:
    tid = request_tenant(request)
    if tid in _LIVE_HALTED:
        raise HTTPException(
            423, "parada de emergência acionada: libere em Plano e risco "
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
        "orders": [{"id": r.order_id, "instrumento": r.intent.reservation.instrument,
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
def desk_scheduler(action: str, request: Request, _=Depends(auth), __=Depends(require_plan)):
    """Liga/desliga o laço autônomo. Ligado, roda sozinho — fechar o
    navegador não interrompe nada. Start reconcilia ANTES de retomar."""
    d = _desk_of(request)
    sched = d["scheduler"]
    if action == "start":
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
from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE  # noqa: E402

# Inquilinos com parada de emergência acionada — trava independente da mesa.
_LIVE_HALTED: set = set()


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
    _LIVE_HALTED.add(tid)
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
    _LIVE_HALTED.discard(tid)
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
