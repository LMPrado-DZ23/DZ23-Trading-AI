"""TradingAgents Operável — serviço completo: API + painel de configuração e
operação embutidos. Uma senha (ACCESS_PASSWORD), um volume (/data), um deploy.

Config vive em /data/config.json (chmod 600), editada PELO PAINEL, aplicada a
cada job — sem redeploy, sem tocar em env no Coolify. Segredos nunca voltam
inteiros ao navegador (exibição mascarada)."""
import hmac
import json
import os
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

DATA = Path(os.environ.get("DATA_DIR", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
CFG_PATH = DATA / "config.json"
PASSWORD = os.environ.get("ACCESS_PASSWORD", "")
STATIC = Path(__file__).parent / "static"

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
    "TRADINGAGENTS_DESK_FEED", "TRADINGAGENTS_DESK_BROKER",
    "TRADINGAGENTS_DESK_INSTRUMENTS", "TRADINGAGENTS_DESK_CAPITAL",
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
        if prov:
            conf["llm_provider"] = prov
        if url:
            conf["backend_url"] = url
        if deep:
            conf["deep_think_llm"] = deep
        if quick:
            conf["quick_think_llm"] = quick
    return conf, env


def load_cfg() -> dict:
    if CFG_PATH.exists():
        try:
            return json.loads(CFG_PATH.read_text())
        except ValueError:
            return {}
    return {}

def save_cfg(cfg: dict):
    CFG_PATH.write_text(json.dumps(cfg, indent=2))
    os.chmod(CFG_PATH, 0o600)

# Estado de saúde exposto no /healthz e no painel — o serviço prefere
# funcionar DEGRADADO a morrer no arranque (um banco fora do ar não pode
# derrubar a página de configuração, senão ninguém consegue nem consertar).
HEALTH: dict = {"armazenamento": "?", "avisos": []}


def build_store():
    """Armazenamento ÚNICO de jobs (TA-006). Postgres quando DATABASE_URL
    existir; SQLite durável do mesmo contrato caso contrário. Se o Postgres
    falhar, cai para SQLite com aviso visível em vez de derrubar o processo."""
    from tradingagents.service.store import SQLiteJobStore
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if dsn:
        try:
            from tradingagents.service.store import PostgresJobStore
            st = PostgresJobStore(dsn)
            st.initialize()
            HEALTH["armazenamento"] = "postgres"
            return st
        except Exception as exc:
            HEALTH["avisos"].append(
                "DATABASE_URL configurada mas o PostgreSQL não respondeu "
                f"({type(exc).__name__}); usando o banco local do volume /data. "
                "Corrija a URL ou remova a variável.")
    try:
        st = SQLiteJobStore(DATA / "jobs.db")
        st.initialize()
        HEALTH["armazenamento"] = "sqlite" if not dsn else "sqlite (fallback)"
        return st
    except Exception as exc:
        HEALTH["armazenamento"] = "indisponível"
        HEALTH["avisos"].append(
            f"não consegui gravar em {DATA} ({type(exc).__name__}): confira o "
            "volume /data e suas permissões. Análises ficarão indisponíveis.")
        return None


STORE = build_store()


async def _json_body(req: Request) -> dict:
    """Corpo JSON validado. Requisição malformada é ERRO DO CLIENTE (422) com
    mensagem legível — 500 seria o servidor confessando bug que não é dele."""
    try:
        body = await req.json()
    except Exception as exc:
        raise HTTPException(422, "corpo da requisição não é JSON válido") from exc
    if not isinstance(body, dict):
        raise HTTPException(422, "corpo da requisição deve ser um objeto JSON")
    return body


def _store_or_502():
    if STORE is None:
        raise HTTPException(503, HEALTH["avisos"][-1] if HEALTH["avisos"]
                            else "armazenamento indisponível")
    return STORE


def _panel_view(rec) -> dict:
    """Traduz o JobRecord do store para o formato que o painel já consome."""
    res = rec.result or {}
    payload = rec.payload or {}
    status_map = {"queued": "queued", "running": "running", "succeeded": "completed",
                  "failed": "failed", "cancelled": "cancelled"}
    err = None
    if rec.error_message:
        err = f"{rec.error_code or 'erro'}: {rec.error_message}"
    return {
        "id": rec.job_id,
        "ticker": payload.get("ticker", ""),
        "trade_date": payload.get("trade_date", ""),
        "status": status_map.get(rec.status.value, rec.status.value),
        "decision": res.get("_decision"),
        "error": err,
        "result": {k: v for k, v in res.items() if k != "_decision"} or None,
        "created": rec.created_at.timestamp(),
        "updated": rec.updated_at.timestamp(),
    }


_lock = threading.Lock()

def run_job(jid, ticker, date):
    with _lock:  # um job pesado por vez neste processo
        rec = STORE.get(jid)
        if not rec or rec.status.value != "queued":
            return
        claimed = STORE.claim(f"panel-{os.getpid()}", lease_seconds=1800)
        if not claimed or claimed.job_id != jid:
            return  # outro worker levou: posse é do store, não do painel
        token = claimed.lease_token
        try:
            cfg = load_cfg()
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
            reports["_decision"] = str(decision)[:4000]
            reports["_transport_mark"] = (state or {}).get("transport_security")
            reports["_identity"] = {
                "company_of_interest": (state or {}).get("company_of_interest"),
                "trade_date": (state or {}).get("trade_date")}
            STORE.complete(jid, token, json.loads(json.dumps(reports, default=str)))
        except Exception as e:
            STORE.fail(jid, token, type(e).__name__,
                       f"{e}"[:800] + " | " + traceback.format_exc()[-600:])


@app.get("/healthz")
def hz():
    degradado = STORE is None or bool(HEALTH["avisos"])
    return {"status": "degraded" if degradado else "ok",
            "app": "tradingagents-operavel/9",
            "armazenamento": HEALTH["armazenamento"],
            "avisos": HEALTH["avisos"]}

@app.get("/")
def index(): return FileResponse(STATIC / "index.html")

@app.get("/v1/primeiros-passos")
def onboarding(_=Depends(auth)):
    """Checklist do usuário final: o que já está pronto e o que falta,
    com a ação concreta de cada item."""
    cfg = load_cfg()
    prov = (cfg.get("TRADINGAGENTS_LLM_PROVIDER") or "").strip().lower()
    chave_prov = {"groq": "GROQ_API_KEY", "google": "GOOGLE_API_KEY",
                  "mistral": "MISTRAL_API_KEY", "openrouter": "OPENROUTER_API_KEY",
                  "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                  "deepseek": "DEEPSEEK_API_KEY"}.get(prov)
    tem_ia = bool(prov and (prov == "ollama" or (chave_prov and cfg.get(chave_prov))))
    itens = [
        {"passo": "Escolher a IA e colar a chave",
         "pronto": tem_ia,
         "acao": "Aba ⚙️: escolha Groq (grátis) e cole a chave de console.groq.com/keys"},
        {"passo": "Dados de mercado (opcional para ações)",
         "pronto": bool(cfg.get("FRED_API_KEY") or cfg.get("ALPHA_VANTAGE_API_KEY")),
         "acao": "Aba ⚙️: chaves gratuitas do FRED e Alpha Vantage enriquecem a análise"},
        {"passo": "Fazer a primeira análise",
         "pronto": bool(STORE and STORE.list_recent(1)),
         "acao": "Aba 📈: ticker (ex.: BTC/USDT) + data e ▶ Analisar agora"},
        {"passo": "Corretora de treino (para operar sem risco)",
         "pronto": bool(cfg.get("EXCHANGE_API_KEY") and cfg.get("EXCHANGE_API_SECRET")),
         "acao": "Crie chave grátis em testnet.binance.vision e cole na aba ⚙️"},
        {"passo": "Ligar a mesa de agentes",
         "pronto": bool(_DESK.get("scheduler") and _DESK["scheduler"].running),
         "acao": "Aba 🧑‍💼: 🤖 Ligar mesa autônoma (ela roda sozinha depois disso)"},
        {"passo": "Guardar um backup",
         "pronto": (DATA / "backups").exists() and any((DATA / "backups").glob("*.tar.gz")),
         "acao": "Aba 🧑‍💼: 💾 Gerar backup"},
    ]
    feitos = sum(1 for i in itens if i["pronto"])
    return {"itens": itens, "concluidos": feitos, "total": len(itens),
            "saude": {"status": "degraded" if (STORE is None or HEALTH["avisos"]) else "ok",
                      "avisos": HEALTH["avisos"]}}


@app.get("/v1/config")
def get_cfg(_=Depends(auth)):
    cfg = load_cfg()
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
    body = await _json_body(req)
    cfg = load_cfg()
    saved = []
    for k, v in (body.get("vars") or {}).items():
        if k in ALLOWED_KEYS and isinstance(v, str) and v.strip():
            cfg[k] = v.strip()
            saved.append(k)
    if not saved:
        raise HTTPException(400, "nada para salvar")
    save_cfg(cfg)
    if any(k.startswith("TRADINGAGENTS_DESK") or k in
           ("TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE") for k in saved):
        sched = _DESK.get("scheduler")
        if sched and sched.running:
            sched.stop()                    # parada controlada antes de trocar
        _DESK["orchestrator"] = None        # próxima chamada reconstrói a mesa
    return {"saved": saved}

@app.post("/v1/jobs")
async def new_job(req: Request, _=Depends(auth)):
    b = await _json_body(req)
    t = str(b.get("ticker", "")).strip().upper()
    d = str(b.get("trade_date", "")).strip()
    if not t or len(d) != 10:
        raise HTTPException(422, "ticker e trade_date (AAAA-MM-DD) obrigatórios")
    idem = req.headers.get("Idempotency-Key") or uuid.uuid4().hex
    idem = (idem + "0" * 16)[:64] if len(idem) < 16 else idem[:128]
    try:
        rec, created = _store_or_502().enqueue({"ticker": t, "trade_date": d}, idem)
    except Exception as e:  # conflito de idempotência = payload diferente
        raise HTTPException(409, f"{type(e).__name__}: {e}"[:200]) from e
    if created:
        threading.Thread(target=run_job, args=(rec.job_id, t, d), daemon=True).start()
    return {"id": rec.job_id, "idempotent": not created}


@app.get("/v1/jobs")
def list_jobs(_=Depends(auth)):
    return {"jobs": [_panel_view(r) for r in _store_or_502().list_recent(50)]}


@app.get("/v1/jobs/{jid}")
def get_job(jid: str, _=Depends(auth)):
    rec = _store_or_502().get(jid)
    if not rec:
        raise HTTPException(404, "job não encontrado")
    return _panel_view(rec)


@app.post("/v1/jobs/{jid}/cancel")
def cancel(jid: str, _=Depends(auth)):
    STORE.cancel(jid)
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
    b = await _json_body(req)
    jid = str(b.get("job_id", "")).strip()
    symbol = str(b.get("symbol", "")).strip().upper()
    side = str(b.get("side", "")).strip().lower()
    try:
        amount = float(b.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0
    rec = STORE.get(jid)
    if not rec or rec.status.value != "succeeded":
        raise HTTPException(422, "job_id inexistente ou não concluído — executa-se a decisão de uma análise")
    stored = rec.result or {}
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
        raise HTTPException(422, f"{type(e).__name__}: {e}"[:400]) from e


# ---------------------------------------------------------------- Mesa (MA-08)
# Estado da mesa vive no processo do serviço; o ciclo é chamado por evento
# (botão do painel agora, worker agendado na fatia seguinte). Modo PAPER
# fixo: esta superfície NÃO liga capital real.
_DESK = {"orchestrator": None, "ledger": None, "coordinator": None,
         "guardian": None, "cycles": [], "scheduler": None}


def _desk_boot(capital: str | None = None):
    from decimal import Decimal
    cfg = load_cfg()
    capital = capital or cfg.get("TRADINGAGENTS_DESK_CAPITAL") or "1000"
    from tradingagents.desk.execution import ExecutionCoordinator, PositionGuardian
    from tradingagents.desk.ledger import Ledger
    from tradingagents.desk.models import AgentStage, DeskMandate, Mode
    from tradingagents.desk.orchestrator import DeskOrchestrator
    from tradingagents.desk.risk import RiskEngine
    from tradingagents.desk.traders import default_desk

    raw = (cfg.get("TRADINGAGENTS_DESK_INSTRUMENTS") or "BTC/USDT,ETH/USDT")
    universe = tuple(i.strip().upper() for i in raw.split(",") if i.strip())
    mandate = DeskMandate(mandate_id="desk-1", owner="prado", account="paper-1",
                          mode=Mode.PAPER, instruments=universe,
                          capital=Decimal(capital))
    traders = default_desk(universe)
    for t in traders:  # estágios autorizados, um a um
        t.spec.move_to(AgentStage.VALIDATED)
        t.spec.move_to(AgentStage.SHADOW)
        t.spec.move_to(AgentStage.PAPER_ENABLED)
    risk = RiskEngine(mandate)
    ledger = Ledger(opening_cash=mandate.capital)
    from tradingagents.desk.brokers import build_desk_broker
    _price_holder = {}
    broker, broker_mode = build_desk_broker(
        cfg, final_state={}, journal_path=str(DATA / "desk-broker.jsonl"),
        paper_price=Decimal("100"))
    if hasattr(broker, "price_provider"):
        broker.price_provider = lambda: _price_holder.get("fn", lambda: {})()
    coord = ExecutionCoordinator(mandate, ledger, risk, worker_id="desk-worker-1",
                                 broker=broker)
    _DESK["broker_mode"] = broker_mode
    orch = DeskOrchestrator(mandate, traders, worker_id="desk-worker-1", risk=risk,
                            positions_provider=ledger.open_positions)
    from tradingagents.desk.models import MarketSnapshot, utcnow as _now
    from tradingagents.desk.scheduler import DeskScheduler

    feed_kind = (cfg.get("TRADINGAGENTS_DESK_FEED") or "fixture").strip().lower()
    if feed_kind == "real":
        # Mercado de verdade: velas públicas, cutoff = fechamento da vela.
        from tradingagents.desk.market import MarketFeedProviders, PublicMarketFeed
        feed = PublicMarketFeed(cfg.get("TRADINGAGENTS_EXCHANGE") or "binance")
        prov = MarketFeedProviders(feed, universe, timeframe="1h", limit=60)
        snaps_fn, prices_fn = prov.snapshots, prov.prices
    else:
        def snaps_fn():
            """Fixture ROTULADA — prova a coordenação, não o mercado de hoje."""
            alta = tuple(Decimal(str(100 + i * 1.5)) for i in range(40))
            queda = tuple([Decimal("100")] * 24 + [Decimal("70")])
            shapes = {0: alta, 1: queda}
            return [MarketSnapshot(inst, "1h", _now(),
                                   shapes.get(n, alta), source="fixture")
                    for n, inst in enumerate(universe)]

        def prices_fn():
            # Preço de execução vem do MESMO dado que gerou a decisão —
            # números inventados distorceriam a contabilidade do treino.
            return {s.instrument: s.last for s in snaps_fn() if s.closes}

    _price_holder["fn"] = prices_fn          # paper preenche ao preço do feed
    _DESK["feed_kind"] = feed_kind
    def _record(out):
        _DESK["cycles"].append({
            "em": _now().isoformat(), "origem": _DESK.get("feed_kind", "fixture"),
            "resumo": out.get("resumo", ""), "vetos": out.get("vetos", []),
            "abstencoes": out.get("abstencoes", []), "erros": out.get("erros", []),
            "descartados": out.get("descartados", []), "ordens": out.get("ordens", [])})
        del _DESK["cycles"][:-30]

    sched = DeskScheduler(
        orch, coord, snapshot_provider=snaps_fn, price_provider=prices_fn,
        state_path=DATA / "desk-state.json", on_cycle=_record,
        interval_seconds=int(os.environ.get("DESK_INTERVAL_SECONDS", "300")))
    _DESK.update(orchestrator=orch, ledger=ledger, coordinator=coord,
                 guardian=PositionGuardian(coord), cycles=[], scheduler=sched)
    return _DESK


@app.get("/v1/desk/status")
def desk_status(_=Depends(auth)):
    try:
        d = _DESK if _DESK["orchestrator"] else _desk_boot()
    except Exception as exc:
        raise HTTPException(503,
                            f"mesa indisponível: {type(exc).__name__}: {exc}"[:300]) from exc
    orch, led, coord = d["orchestrator"], d["ledger"], d["coordinator"]
    return {
        "mandate": {"account": orch.mandate.account, "mode": orch.mandate.mode.value,
                    "capital": str(orch.mandate.capital),
                    "instruments": list(orch.mandate.instruments),
                    "revoked": orch.mandate.revoked_at is not None},
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
        "fonte": {"dados": d.get("feed_kind", "fixture"),
                  "corretora": d.get("broker_mode", "paper"),
                  "aviso": ("dados de FIXTURE — provam a mesa, não o mercado"
                            if d.get("feed_kind") != "real" else
                            "mercado real (leitura pública, cutoff na vela fechada)")},
        "ciclos": d["cycles"][-10:],
    }


@app.post("/v1/desk/cycle")
def desk_cycle(_=Depends(auth)):
    """Roda um ciclo com dados de FIXTURE rotulados — prova a coordenação da
    mesa sem inventar mercado. O ciclo de produção virá do worker agendado."""
    from decimal import Decimal

    from tradingagents.desk.models import MarketSnapshot, utcnow
    d = _DESK if _DESK["orchestrator"] else _desk_boot()
    alta = [Decimal(str(100 + i * 1.5)) for i in range(40)]
    queda = [Decimal("100")] * 24 + [Decimal("70")]
    snaps = [MarketSnapshot("BTC/USDT", "1h", utcnow(), tuple(alta), source="fixture"),
             MarketSnapshot("ETH/USDT", "1h", utcnow(), tuple(queda), source="fixture")]
    prices = d["scheduler"].price_provider()
    report = d["orchestrator"].run_cycle(snaps, prices)
    enviados, encerrados = [], []
    # saídas primeiro: liberam capital e realizam resultado
    for _agente, instrument, motivo in report.exits:
        pos = d["orchestrator"].book.get(instrument)
        if not pos:
            continue
        preco = d["orchestrator"].risk and prices.get(instrument) or pos.entry_price
        out = d["coordinator"].close_position(
            instrument=instrument, quantity=pos.quantity, price=preco,
            cost_basis=pos.cost_basis, agent_id=pos.agent_id)
        if out.get("estado") == "encerrada":
            d["orchestrator"].close_position(instrument)
        encerrados.append({"instrumento": instrument, "motivo": motivo, **out})
    for intent in report.intents:
        rec = d["coordinator"].submit(intent)
        enviados.append({"ordem": rec.order_id, "estado": rec.state.value,
                         "instrumento": rec.intent.reservation.instrument})
        if rec.state.value == "filled":
            d["orchestrator"].register_fill(
                rec.intent.reservation.instrument, rec.filled_qty, rec.avg_price,
                rec.intent.reservation.agent_id,
                rec.filled_qty * rec.avg_price)
    d["cycles"].append({"em": report.started_at.isoformat(),
                        "origem": d.get("feed_kind", "fixture"),
                        "resumo": report.summary,
                        "vetos": [f"{x}: {w}" for x, w in report.vetoed][:6],
                        "abstencoes": [f"{ag} · {i}: {r}"
                                       for ag, i, r in report.abstentions][:6],
                        "erros": report.errors[:4], "ordens": enviados,
                        "encerradas": encerrados})
    return {"ok": True, "resumo": report.summary, "ordens": enviados,
            "encerradas": encerrados}


@app.post("/v1/desk/pause")
def desk_pause(_=Depends(auth)):
    d = _DESK if _DESK["orchestrator"] else _desk_boot()
    d["orchestrator"].pause_entries()
    return {"ok": True, "entradas": "pausadas",
            "a_encerrar": d["orchestrator"].drain(),
            "aviso": "saída solicitada não é saída concluída — o guardião acompanha"}


@app.get("/v1/desk/avaliacao")
def desk_evaluation(_=Depends(auth)):
    """Avaliação da equipe a partir do que a mesa REALMENTE fez (ciclos e
    livro-razão). Sem operações suficientes, diz isso — não inventa nota."""
    from decimal import Decimal as D
    d = _DESK if _DESK["orchestrator"] else _desk_boot()
    led, coord = d["ledger"], d["coordinator"]
    atrib = led.attribution()
    fechadas = [r for r in coord.orders.values() if r.state.value == "filled"]
    por_agente: dict[str, dict] = {}
    for r in fechadas:
        ag = r.intent.reservation.agent_id or "desconhecido"
        e = por_agente.setdefault(ag, {"ordens": 0, "custos": D("0")})
        e["ordens"] += 1
        e["custos"] += r.fees
    linhas = []
    for ag, e in sorted(por_agente.items()):
        linhas.append({"agente": ag, "ordens": e["ordens"],
                       "custos": str(e["custos"]),
                       "resultado_realizado": str(atrib.get(ag, D("0")))})
    ressalvas = []
    if len(fechadas) < 20:
        ressalvas.append(f"amostra pequena: {len(fechadas)} operações (< 20)")
    if d.get("feed_kind") != "real":
        ressalvas.append("dados de fixture — não mede desempenho de mercado")
    if d.get("broker_mode", "paper") == "paper":
        ressalvas.append("execução simulada: sem fila real, slippage real nem latência")
    if not any(D(linha["resultado_realizado"]) != 0 for linha in linhas):
        ressalvas.append("nenhuma posição foi FECHADA: resultado ainda não realizado")
    return {"por_agente": linhas,
            "conclusivo": not ressalvas,
            "ressalvas": ressalvas,
            "aviso": "retrospectivo e em ambiente simulado; não é promessa de lucro"}


@app.post("/v1/backup")
def backup_now(_=Depends(auth)):
    """Gera o pacote de recuperação em /data/backups. O download é do dono,
    pelo volume — o serviço não expõe segredos por HTTP."""
    from tradingagents.desk.backup import BackupError, create_backup
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        out = create_backup(DATA, DATA / "backups" / f"dz23-backup-{stamp}.tar.gz")
    except BackupError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, **out,
            "como_restaurar": "copie o arquivo do volume /data/backups e use "
                              "restore_backup() na máquina nova (veja RUNBOOK.md)"}


@app.get("/v1/backups")
def list_backups(_=Depends(auth)):
    d = DATA / "backups"
    if not d.exists():
        return {"backups": []}
    return {"backups": sorted(
        ({"arquivo": p.name, "bytes": p.stat().st_size,
          "em": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))}
         for p in d.glob("*.tar.gz")), key=lambda x: x["arquivo"], reverse=True)}


@app.get("/v1/desk/feed")
def desk_feed(_=Depends(auth)):
    """Diagnóstico honesto do feed: quantas velas, até quando, e a próxima
    ação concreta quando algo está errado."""
    d = _DESK if _DESK["orchestrator"] else _desk_boot()
    cfg = load_cfg()
    kind = d.get("feed_kind", "fixture")
    if kind != "real":
        return {"fonte": "fixture", "estado": "TREINO",
                "proxima_acao": "Em ⚙️ Configurações, Fonte de dados = Mercado real"}
    from tradingagents.desk.market import MarketFeedProviders, PublicMarketFeed
    feed = PublicMarketFeed(cfg.get("TRADINGAGENTS_EXCHANGE") or "binance")
    universe = tuple(d["orchestrator"].mandate.instruments)
    out = []
    for snap in MarketFeedProviders(feed, universe).snapshots():
        ok = snap.quality == "ok"
        out.append({"instrumento": snap.instrument, "velas": len(snap.closes),
                    "ate": snap.cutoff.isoformat() if snap.closes else None,
                    "qualidade": snap.quality, "fonte": snap.source,
                    "pronto": ok,
                    "proxima_acao": "" if ok else (
                        "servidor sem acesso à exchange (firewall/saída bloqueada) "
                        "ou exchange fora do ar — teste outra exchange em ⚙️")})
    return {"fonte": "real", "exchange": cfg.get("TRADINGAGENTS_EXCHANGE") or "binance",
            "instrumentos": out,
            "estado": "PRONTO" if all(i["pronto"] for i in out) else "SEM DADOS"}


@app.post("/v1/desk/scheduler/{action}")
def desk_scheduler(action: str, _=Depends(auth)):
    """Liga/desliga o laço autônomo da mesa. Ligado, o ciclo roda sozinho —
    fechar o navegador não interrompe nada."""
    d = _DESK if _DESK["orchestrator"] else _desk_boot()
    sched = d["scheduler"]
    if action == "start":
        recovered = sched.recover()      # reconcilia ANTES de retomar
        started = sched.start()
        return {"ok": True, "iniciado": started, "recuperacao": recovered,
                "status": sched.status()}
    if action == "stop":
        sched.stop()
        return {"ok": True, "parada": "controlada — posições seguem sob o guardião",
                "a_encerrar": d["orchestrator"].drain(), "status": sched.status()}
    raise HTTPException(422, "ação deve ser start ou stop")
