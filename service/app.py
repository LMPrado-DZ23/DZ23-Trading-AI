"""TradingAgents Operável — serviço completo: API + painel de configuração e
operação embutidos. Uma senha (ACCESS_PASSWORD), um volume (/data), um deploy.

Config vive em /data/config.json (chmod 600), editada PELO PAINEL, aplicada a
cada job — sem redeploy, sem tocar em env no Coolify. Segredos nunca voltam
inteiros ao navegador (exibição mascarada)."""
import hmac, json, os, sqlite3, threading, time, traceback, uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

DATA = Path(os.environ.get("DATA_DIR", "/data")); DATA.mkdir(parents=True, exist_ok=True)
CFG_PATH = DATA / "config.json"; DB = DATA / "jobs.db"
PASSWORD = os.environ.get("ACCESS_PASSWORD", "")
STATIC = Path(__file__).parent / "static"

ALLOWED_KEYS = [
    "TRADINGAGENTS_LLM_PROVIDER", "TRADINGAGENTS_DEEP_THINK_LLM",
    "TRADINGAGENTS_QUICK_THINK_LLM", "TRADINGAGENTS_OUTPUT_LANGUAGE",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS", "TRADINGAGENTS_MAX_RISK_ROUNDS",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY", "FRED_API_KEY", "ALPHA_VANTAGE_API_KEY",
    "TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE",
    "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "LIVE_TRADING_CONFIRM",
]
SECRETish = tuple(k for k in ALLOWED_KEYS if k.endswith(("_KEY", "_SECRET")))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
bearer = HTTPBearer(auto_error=False)

def auth(c: HTTPAuthorizationCredentials = Depends(bearer)):
    if not PASSWORD:
        raise HTTPException(500, "defina ACCESS_PASSWORD no ambiente")
    if not c or not hmac.compare_digest(c.credentials, PASSWORD):
        raise HTTPException(401, "não autenticado")
    return True

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
            cfg = load_cfg()
            for k, v in cfg.items():
                if k in ALLOWED_KEYS and v: os.environ[k] = str(v)
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            conf = dict(DEFAULT_CONFIG)
            prov = cfg.get("TRADINGAGENTS_LLM_PROVIDER")
            if prov: conf["llm_provider"] = prov
            if cfg.get("TRADINGAGENTS_DEEP_THINK_LLM"): conf["deep_think_llm"] = cfg["TRADINGAGENTS_DEEP_THINK_LLM"]
            if cfg.get("TRADINGAGENTS_QUICK_THINK_LLM"): conf["quick_think_llm"] = cfg["TRADINGAGENTS_QUICK_THINK_LLM"]
            graph = TradingAgentsGraph(config=conf)
            state, decision = graph.propagate(ticker, date)
            reports = {k: v for k, v in (state or {}).items()
                       if isinstance(v, str) and ("report" in k or "decision" in k)}
            # guarda o mark de transporte + identidade: é o que permite EXECUTAR
            # a decisão depois, passando pelo gate de capital honestamente.
            reports["_transport_mark"] = (state or {}).get("transport_security")
            reports["_identity"] = {"company_of_interest": (state or {}).get("company_of_interest"),
                                     "trade_date": (state or {}).get("trade_date")}
            upd(jid, status="completed", decision=str(decision)[:4000],
                result=json.dumps(reports, ensure_ascii=False, default=str)[:200000])
        except Exception as e:
            upd(jid, status="failed",
                error=f"{type(e).__name__}: {e}"[:2000] + "\n" +
                      traceback.format_exc()[-1500:])

@app.get("/healthz")
def hz(): return {"status": "ok", "app": "tradingagents-operavel/1"}

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
