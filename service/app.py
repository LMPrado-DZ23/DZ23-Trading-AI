"""TradingAgents Operável — serviço completo: API + painel de configuração e
operação embutidos. Uma senha (ACCESS_PASSWORD), um volume (/data), um deploy.

Config vive em /data/config.json (chmod 600), editada PELO PAINEL, aplicada a
cada job — sem redeploy, sem tocar em env no Coolify. Segredos nunca voltam
inteiros ao navegador (exibição mascarada)."""
import asyncio
import hmac
import json
import os
import sqlite3
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
from tradingagents.risk import RiskEngine
from tradingagents.telemetry import AuditLog, EventBus, MetricsRegistry

DATA = Path(os.environ.get("DATA_DIR", "/data")); DATA.mkdir(parents=True, exist_ok=True)
CFG_PATH = DATA / "config.json"; DB = DATA / "jobs.db"
PASSWORD = os.environ.get("ACCESS_PASSWORD", "")
STATIC = Path(__file__).parent / "static"
AGENCY_AUDIT = AuditLog(DATA / "agency-events.jsonl")
AGENCY_EVENT_BUS = EventBus(AGENCY_AUDIT)
AGENCY = AgencyRuntime(audit_log=AGENCY_AUDIT, event_bus=AGENCY_EVENT_BUS)
AUDIT_LOG = AuditLog(DATA / "execution-events.jsonl")
EVENT_BUS = EventBus(AUDIT_LOG)
PAPER_ADAPTER = PaperAdapter(state_path=DATA / "paper-state.json")
SHADOW_ADAPTER = ShadowAdapter(state_path=DATA / "shadow-state.json")
KILL_SWITCH = KillSwitch()
METRICS = MetricsRegistry()
EXECUTION_ROUTER = ExecutionRouter(risk_engine=RiskEngine(), audit_log=AUDIT_LOG, event_bus=EVENT_BUS, kill_switch=KILL_SWITCH, metrics=METRICS)
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
    "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "LIVE_TRADING_CONFIRM",
]
SECRETish = tuple(k for k in ALLOWED_KEYS if k.endswith(("_KEY", "_SECRET")))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
bearer = HTTPBearer(auto_error=False)

# Trava de força-bruta: com o código público, qualquer um sabe que UMA senha
# guarda tudo — então errar custa caro. 5 erros por IP => 15 min de espera.
_FAILS: dict = {}
MAX_FAILS, LOCK_SECONDS = 5, 900

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else None) or (
        request.client.host if request.client else "?")

def auth(request: Request, c: HTTPAuthorizationCredentials = Depends(bearer)):
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


def load_cfg() -> dict:
    if CFG_PATH.exists():
        try: return json.loads(CFG_PATH.read_text())
        except ValueError: return {}
    return {}

def save_cfg(cfg: dict):
    CFG_PATH.write_text(json.dumps(cfg, indent=2)); os.chmod(CFG_PATH, 0o600)

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS jobs(
        id TEXT PRIMARY KEY, idem TEXT UNIQUE, ticker TEXT, trade_date TEXT,
        status TEXT, error TEXT, decision TEXT, result TEXT,
        created REAL, updated REAL)"""); return c

def upd(jid, **kw):
    kw["updated"] = time.time()
    with db() as c:
        c.execute(f"UPDATE jobs SET {','.join(k+'=?' for k in kw)} WHERE id=?",
                  [*kw.values(), jid])

_cancel = set(); _lock = threading.Lock()

def run_job(jid, ticker, date):
    with _lock:  # um job por vez: env é global do processo
        if jid in _cancel: upd(jid, status="cancelled"); return
        upd(jid, status="running")
        try:
            AGENCY.set_agent_status("market-scanner", "ANALYZING")
            AGENCY.set_agent_status("strategy-agent", "ANALYZING")
            cfg = load_cfg()
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
            AGENCY.set_agent_status("strategy-agent", "PAPER_TRADING")
            AGENCY.set_agent_status("market-scanner", "WAITING_SETUP")
        except Exception as e:
            AGENCY.set_agent_status("strategy-agent", "ERROR")
            upd(jid, status="failed",
                error=f"{type(e).__name__}: {e}"[:2000] + "\n" +
                      traceback.format_exc()[-1500:])

@app.get("/healthz")
def hz(): return {"status": "ok", "app": "tradingagents-operavel/3"}


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
def get_cfg(_=Depends(auth)):
    cfg = load_cfg()
    out = {}
    for k in ALLOWED_KEYS:
        v = cfg.get(k, "")
        if not v: out[k] = {"set": False}
        elif k in SECRETish: out[k] = {"set": True, "hint": "•••" + str(v)[-4:]}
        else: out[k] = {"set": True, "value": v}
    return {"config": out}

@app.post("/v1/config")
async def set_cfg(req: Request, _=Depends(auth)):
    body = await req.json()
    cfg = load_cfg(); saved = []
    for k, v in (body.get("vars") or {}).items():
        if k in ALLOWED_KEYS and isinstance(v, str) and v.strip():
            cfg[k] = v.strip(); saved.append(k)
    if not saved: raise HTTPException(400, "nada para salvar")
    save_cfg(cfg); return {"saved": saved}

@app.post("/v1/jobs")
async def new_job(req: Request, _=Depends(auth)):
    b = await req.json()
    t, d = str(b.get("ticker", "")).strip().upper(), str(b.get("trade_date", "")).strip()
    if not t or len(d) != 10: raise HTTPException(422, "ticker e trade_date (AAAA-MM-DD) obrigatórios")
    idem = req.headers.get("Idempotency-Key") or uuid.uuid4().hex
    with db() as c:
        row = c.execute("SELECT id FROM jobs WHERE idem=?", (idem,)).fetchone()
        if row: return {"id": row["id"], "idempotent": True}
        jid = uuid.uuid4().hex[:16]
        c.execute("INSERT INTO jobs(id,idem,ticker,trade_date,status,created,updated)"
                  " VALUES(?,?,?,?,?,?,?)", (jid, idem, t, d, "queued", time.time(), time.time()))
    threading.Thread(target=run_job, args=(jid, t, d), daemon=True).start()
    return {"id": jid}

@app.get("/v1/jobs")
def list_jobs(_=Depends(auth)):
    with db() as c:
        rows = c.execute("SELECT id,ticker,trade_date,status,decision,created "
                         "FROM jobs ORDER BY created DESC LIMIT 50").fetchall()
    return {"jobs": [dict(r) for r in rows]}


@app.get("/v1/jobs/{jid}")
def get_job(jid: str, _=Depends(auth)):
    with db() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r: raise HTTPException(404, "job não encontrado")
    o = dict(r); o["result"] = json.loads(o["result"]) if o.get("result") else None
    return o

@app.post("/v1/jobs/{jid}/cancel")
def cancel(jid: str, _=Depends(auth)):
    _cancel.add(jid)
    with db() as c:
        r = c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    if r and r["status"] == "queued": upd(jid, status="cancelled")
    return {"ok": True}


@app.get("/v1/broker/status")
def broker_status(_=Depends(auth)):
    """Valida a configuração de corretora SEM enviar ordem nenhuma."""
    cfg = load_cfg()
    env = {k: cfg.get(k, "") for k in
           ("TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE",
            "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET",
            "LIVE_TRADING_CONFIRM")}
    try:
        from tradingagents.brokers.ccxt_bridge import CCXTBridge
        b = CCXTBridge(env=env, journal_path=str(DATA / "broker-journal.jsonl"))
        return {"ready": True, **b.status(),
                "nota": ("ordens serão REAIS" if b.mode=="live" else "ordens de mentira (testnet)")}
    except Exception as e:
        return {"ready": False, "erro": f"{type(e).__name__}: {e}"[:400]}


@app.post("/v1/broker/order")
async def broker_order(req: Request, _=Depends(auth)):
    """EXECUTA uma ordem (testnet ou live conforme configuração) amarrada a
    um job concluído — a decisão analisada é o que se opera."""
    if os.environ.get("LEGACY_BROKER_ORDER_ENABLED", "false").lower() != "true":
        raise HTTPException(410, "endpoint legado bloqueado: use /v1/execution/paper ou o ExecutionRouter governado")
    b = await req.json()
    jid = str(b.get("job_id", "")).strip()
    symbol = str(b.get("symbol", "")).strip().upper()
    side = str(b.get("side", "")).strip().lower()
    try:
        amount = float(b.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0
    with db() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r or r["status"] != "completed":
        raise HTTPException(422, "job_id inexistente ou não concluído — executa-se a decisão de uma análise")
    stored = json.loads(r["result"] or "{}")
    ident = stored.get("_identity") or {}
    final_state = {**ident, "transport_security": stored.get("_transport_mark")}
    cfg = load_cfg()
    env = {k: cfg.get(k, "") for k in ("TRADINGAGENTS_EXCHANGE",
           "TRADINGAGENTS_BROKER_MODE", "EXCHANGE_API_KEY",
           "EXCHANGE_API_SECRET", "LIVE_TRADING_CONFIRM")}
    try:
        from tradingagents.brokers.ccxt_bridge import CCXTBridge
        bridge = CCXTBridge(env=env, journal_path=str(DATA / "broker-journal.jsonl"))
        out = bridge.submit_order(final_state, job_id=jid,
                                  symbol=symbol or ident.get("company_of_interest", ""),
                                  side=side, amount=amount)
        return {"ok": True, "mode": bridge.mode,
                "client_order_id": out["client_order_id"],
                "order_id": (out.get("order") or {}).get("id")}
    except Exception as e:
        raise HTTPException(422, f"{type(e).__name__}: {e}"[:400])


@app.get("/floor")
def floor():
    """Trading Floor visual; os dados continuam protegidos pela API autenticada."""
    return FileResponse(STATIC / "floor.html")


@app.get("/v1/agency/snapshot")
def agency_snapshot(_=Depends(auth)):
    snapshot = AGENCY.snapshot()
    agents_by_id = AGENCY.agents
    for desk in snapshot["desks"]:
        desk["agents_data"] = [agents_by_id[agent_id] for agent_id in desk["agents"]]
    return snapshot


@app.get("/v1/agency/events")
def agency_events(after: int = 0, _=Depends(auth)):
    if after < 0:
        raise HTTPException(422, "after deve ser não-negativo")
    return {"events": [{"sequence": item.sequence, "event": item.event.as_dict()} for item in AGENCY.event_bus.replay(after)]}


@app.get("/v1/agency/health")
def agency_health(_=Depends(auth)):
    return {"runtime": "READY", "event_sequence": AGENCY.event_bus.latest_sequence, "agents": len(AGENCY.agents), "meetings": len(AGENCY.meetings), "audit_log": str(DATA / "agency-events.jsonl")}


@app.post("/v1/agency/agents/{agent_id}/status")
async def agency_agent_status(agent_id: str, req: Request, _=Depends(auth)):
    body = await req.json()
    status = str(body.get("status", "")).strip().upper()
    if not status:
        raise HTTPException(422, "status obrigatório")
    try:
        event = AGENCY.set_agent_status(agent_id, status)
    except KeyError:
        raise HTTPException(404, "agente não encontrado") from None
    return {"ok": True, "event_id": event.event_id}


@app.post("/v1/agency/meetings")
async def agency_meeting(req: Request, _=Depends(auth)):
    body = await req.json()
    try:
        meeting = AGENCY.start_meeting(str(body["meeting_id"]), str(body["meeting_type"]), str(body["desk_id"]), tuple(body["participants"]), int(body["quorum"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"reunião inválida: {exc}") from exc
    return {"meeting": {"meeting_id": meeting.meeting_id, "status": meeting.status, "quorum": meeting.quorum}}


@app.post("/v1/agency/meetings/{meeting_id}/votes")
async def agency_vote(meeting_id: str, req: Request, _=Depends(auth)):
    body = await req.json()
    meeting = AGENCY.meetings.get(meeting_id)
    if not meeting:
        raise HTTPException(404, "reunião não encontrada")
    try:
        meeting.record_vote(str(body["agent_id"]), str(body["action"]), float(body["confidence"]), list(body.get("evidence_ids", [])))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"voto inválido: {exc}") from exc
    return {"meeting_id": meeting_id, "status": meeting.status, "votes": meeting.votes}


@app.get("/v1/agency/stream")
async def agency_stream(after: int = 0, _=Depends(auth)):
    async def events():
        cursor = after
        for _ in range(120):
            items = AGENCY.event_bus.replay(cursor)
            for item in items:
                cursor = item.sequence
                yield f"id: {cursor}\nevent: {item.event.event_type}\ndata: {json.dumps(item.event.as_dict(), ensure_ascii=False)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/v1/execution/paper")
async def paper_execution(req: Request, _=Depends(auth)):
    """Executa apenas Paper através de Decision -> RiskEngine -> Adapter."""
    body = await req.json()
    try:
        now = datetime.now(timezone.utc)
        decision = TradeDecision(
            str(body["decision_id"]), str(body.get("run_id", uuid.uuid4().hex)), str(body["strategy_id"]), str(body.get("desk_id", "strategy")), str(body["instrument_id"]), "paper", PAPER_ADAPTER.account_id,
            Side(str(body["side"]).upper()), str(body.get("order_type", "MARKET")), Decimal(str(body["entry_price"])), Decimal(str(body["stop_loss"])), Decimal(str(body["take_profit"])), Decimal(str(body["risk_amount"])), Decimal(str(body["risk_pct_equity"])), Decimal(str(body["quantity"])), str(body.get("time_in_force", "GTC")), now + timedelta(seconds=int(body.get("signal_ttl_seconds", 60))), Decimal(str(body["confidence"])), str(body.get("regime", "UNKNOWN")), tuple(body.get("evidence_ids", [])), tuple(body.get("agent_votes", [])), now,
        )
        observed = datetime.fromisoformat(str(body.get("observed_at", now.isoformat())))
        snapshot = MarketSnapshot(str(body.get("snapshot_id", uuid.uuid4().hex)), decision.instrument_id, "paper", Decimal(str(body["bid"])), Decimal(str(body["ask"])), Decimal(str(body.get("last", body["entry_price"]))), Decimal(str(body.get("volume", "0"))), Decimal(str(body.get("spread", Decimal(str(body["ask"])) - Decimal(str(body["bid"]))))), observed, now, "paper-api", int(body.get("sequence", 0)))
        portfolio = PAPER_ADAPTER.get_portfolio(PAPER_ADAPTER.account_id)
        request_order = OrderRequest(str(body.get("client_order_id", f"paper-{decision.decision_id}")), decision.decision_id, decision.run_id, decision.strategy_id, decision.account_id, decision.instrument_id, "paper", decision.side, decision.order_type, decision.quantity, decision.entry_price_reference, decision.stop_loss, decision.take_profit)
        result = EXECUTION_ROUTER.submit(decision, portfolio, snapshot, PAPER_ADAPTER, request=request_order)
        return {"risk": {"status": result.risk.status.value, "reason_codes": result.risk.reason_codes, "risk_decision_id": result.risk.risk_decision_id}, "order": result.order.__dict__ if result.order and hasattr(result.order, "__dict__") else ({"order_id": result.order.order_id, "client_order_id": result.order.client_order_id, "status": result.order.status.value, "filled_quantity": str(result.order.filled_quantity), "average_price": str(result.order.average_price) if result.order.average_price is not None else None} if result.order else None)}
    except PermissionError as exc:
        raise HTTPException(423, str(exc)) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"paper execution inválida: {exc}") from exc


@app.get("/v1/execution/shadow")
def shadow_summary(_=Depends(auth)):
    return SHADOW_ADAPTER.summary()


@app.post("/v1/execution/shadow")
async def shadow_execution(req: Request, _=Depends(auth)):
    body = await req.json()
    try:
        now = datetime.now(timezone.utc)
        request = OrderRequest(str(body["client_order_id"]), str(body.get("decision_id", "shadow-decision")), str(body.get("run_id", "shadow-run")), str(body.get("strategy_id", "shadow-strategy")), "shadow-account", str(body["instrument_id"]), "shadow", Side(str(body["side"]).upper()), str(body.get("order_type", "MARKET")), Decimal(str(body["quantity"])), Decimal(str(body.get("price", body["last"]))), Decimal(str(body.get("stop_loss", "0.0001"))), Decimal(str(body.get("take_profit", "0.0002"))))
        snapshot = MarketSnapshot(str(body.get("snapshot_id", uuid.uuid4().hex)), request.instrument_id, "shadow", Decimal(str(body["bid"])), Decimal(str(body["ask"])), Decimal(str(body["last"])), Decimal(str(body.get("volume", "0"))), Decimal(str(body.get("spread", "0"))), now, now, "shadow-api", int(body.get("sequence", 0)))
        trade = SHADOW_ADAPTER.record(request, snapshot)
        return {"shadow_id": trade.shadow_id, "client_order_id": trade.client_order_id, "status": trade.status, "reference_price": str(trade.reference_price), "orders_sent": 0}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"shadow execution inválida: {exc}") from exc


@app.get("/v1/execution/health")
def execution_health(_=Depends(auth)):
    return {"paper": PAPER_ADAPTER.healthcheck(), "kill_switch": {"enabled": KILL_SWITCH.state.enabled, "reason": KILL_SWITCH.state.reason, "changed_at": KILL_SWITCH.state.changed_at.isoformat(), "actor": KILL_SWITCH.state.actor}, "event_sequence": EVENT_BUS.latest_sequence}


@app.post("/v1/execution/kill-switch")
async def execution_kill_switch(req: Request, _=Depends(auth)):
    body = await req.json()
    action = str(body.get("action", "engage")).lower()
    if action == "engage":
        state = KILL_SWITCH.engage(str(body.get("reason", "bloqueio administrativo")), actor=str(body.get("actor", "api")))
    elif action == "reset":
        state = KILL_SWITCH.reset(actor=str(body.get("actor", "api")), reconciliation_ok=bool(body.get("reconciliation_ok", False)))
    else:
        raise HTTPException(422, "action deve ser engage ou reset")
    return {"enabled": state.enabled, "reason": state.reason, "changed_at": state.changed_at.isoformat(), "actor": state.actor}
