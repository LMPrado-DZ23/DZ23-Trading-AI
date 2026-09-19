"""§5/§7: cancelamento cooperativo. Um cancel que chega DURANTE a propagação
faz o job terminar como 'cancelled' — NUNCA 'completed' (o checkpoint pós-
propagate descarta o resultado). Requer o app (roda no CI Docker)."""
from __future__ import annotations

import importlib


def test_cancel_durante_propagate_termina_cancelled(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MULTI_TENANT_ENABLED", raising=False)
    import service.app as m
    importlib.reload(m)
    m.JOB_STORE.insert(job_id="j1", idem="i1", ticker="AAPL",
                       trade_date="2026-01-02", tenant_id="legacy")

    class StubGraph:
        def __init__(self, config=None):
            pass

        def propagate(self, ticker, date, asset_type=None):
            # cancel CHEGA no meio da "propagação" (durante o LLM monolítico)
            m.JOB_STORE.request_cancel("j1", "legacy")
            return {}, "BUY"

    monkeypatch.setattr("tradingagents.graph.trading_graph.TradingAgentsGraph", StubGraph)
    m.run_job("j1", "AAPL", "2026-01-02", "legacy")
    # o checkpoint pós-propagate viu o cancel e descartou o resultado:
    assert m.JOB_STORE.status("j1", "legacy") == "cancelled"


def test_cancel_antes_de_iniciar_nao_roda_o_grafo(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MULTI_TENANT_ENABLED", raising=False)
    import service.app as m
    importlib.reload(m)
    m.JOB_STORE.insert(job_id="j2", idem="i2", ticker="AAPL",
                       trade_date="2026-01-02", tenant_id="legacy")
    m.JOB_STORE.request_cancel("j2", "legacy")

    chamou = {"graph": False}

    class Boom:
        def __init__(self, config=None):
            chamou["graph"] = True

        def propagate(self, *a, **k):
            chamou["graph"] = True
            return {}, "BUY"

    monkeypatch.setattr("tradingagents.graph.trading_graph.TradingAgentsGraph", Boom)
    m.run_job("j2", "AAPL", "2026-01-02", "legacy")
    assert m.JOB_STORE.status("j2", "legacy") == "cancelled"
    assert chamou["graph"] is False          # nem construiu o grafo
