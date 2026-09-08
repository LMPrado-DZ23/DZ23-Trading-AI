"""Gate B: immutable runtime, point-in-time data and reproducible identity."""

from __future__ import annotations

import json
from contextvars import copy_context
from datetime import datetime, timezone

import pytest

from tradingagents.agents.utils.agent_utils import get_prediction_markets
from tradingagents.dataflows import (
    alpha_vantage_fundamentals as avf,
    interface,
    polymarket,
    y_finance,
)
from tradingagents.dataflows.config import get_config, set_config
from tradingagents.dataflows.date_window import in_window
from tradingagents.dataflows.evidence import UntrustedEvidence, is_backtest_admissible
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.manifest import build_run_manifest
from tradingagents.runtime import (
    WORKFLOW_SCHEMA_VERSION,
    RunContext,
    build_runtime_config,
    freeze_config,
)


def _context(
    trade_date="2024-05-01", *, asset_type="stock", now=None, config=None
) -> RunContext:
    return RunContext.create(
        "AAPL", trade_date, asset_type, config or build_runtime_config(),
        now=now or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.unit
def test_config_snapshot_is_recursive_and_immutable():
    source = build_runtime_config({"data_vendors": {"news_data": "alpha_vantage"}})
    frozen = freeze_config(source)
    source["data_vendors"]["news_data"] = "yfinance"
    assert frozen["data_vendors"]["news_data"] == "alpha_vantage"
    with pytest.raises(TypeError):
        frozen["data_vendors"]["news_data"] = "polymarket"


@pytest.mark.unit
def test_legacy_direct_config_is_context_local_not_process_global():
    set_config({"output_language": "Portuguese"})
    child = copy_context()
    child.run(set_config, {"output_language": "French"})
    assert child.run(get_config)["output_language"] == "French"
    assert get_config()["output_language"] == "Portuguese"


@pytest.mark.unit
def test_run_context_uses_logical_session_close_boundary():
    cfg = build_runtime_config()
    before = RunContext.create(
        "AAPL", "2026-01-05", "stock", cfg,
        now=datetime.fromisoformat("2026-01-05T15:59:59-05:00"),
    )
    after = RunContext.create(
        "AAPL", "2026-01-05", "stock", cfg,
        now=datetime.fromisoformat("2026-01-05T16:00:01-05:00"),
    )
    assert before.decision_at.isoformat() == "2026-01-05T16:00:00-05:00"
    assert before.historical is False
    assert after.historical is True


@pytest.mark.unit
def test_backtest_mode_is_historical_even_for_a_future_date():
    context = RunContext.create(
        "AAPL", "2030-01-02", "stock", build_runtime_config(),
        now=datetime(2026, 1, 1, tzinfo=timezone.utc), run_mode="backtest",
    )
    assert context.historical is True
    assert context.run_mode == "backtest"


@pytest.mark.unit
def test_run_context_rejects_naive_or_wrong_date_decision_time():
    cfg = build_runtime_config()
    with pytest.raises(ValueError, match="timezone-aware"):
        RunContext.create(
            "AAPL", "2024-05-01", "stock", cfg,
            decision_at=datetime(2024, 5, 1, 16),
        )
    with pytest.raises(ValueError, match="trade_date"):
        RunContext.create(
            "AAPL", "2024-05-01", "stock", cfg,
            decision_at=datetime.fromisoformat("2024-05-02T00:01:00-04:00"),
        )


@pytest.mark.unit
def test_tool_boundary_rejects_other_ticker_and_future_date(monkeypatch):
    ctx = _context()
    cfg = build_runtime_config({"data_vendors": {"core_stock_apis": "yfinance"}})
    monkeypatch.setitem(interface.VENDOR_METHODS["get_stock_data"], "yfinance", lambda *a: "x")
    with pytest.raises(ValueError, match="does not match run ticker"):
        interface.route_to_vendor(
            "get_stock_data", "MSFT", "2024-04-01", "2024-05-01",
            config=cfg, run_context=ctx,
        )
    with pytest.raises(ValueError, match="exceeds immutable cutoff"):
        interface.route_to_vendor(
            "get_stock_data", "AAPL", "2024-04-01", "2024-05-02",
            config=cfg, run_context=ctx,
        )


@pytest.mark.unit
def test_polymarket_historical_is_excluded_before_network(monkeypatch):
    monkeypatch.setattr(polymarket, "_request", lambda *a, **k: pytest.fail("network called"))
    out = polymarket.get_prediction_markets("fed", run_context=_context())
    assert out.startswith("PIT_DATA_EXCLUDED:")
    assert "historical snapshot" in out


@pytest.mark.unit
def test_publication_one_second_after_close_is_excluded():
    start = datetime.fromisoformat("2024-05-01T00:00:00-04:00")
    end = datetime.fromisoformat("2024-05-01T00:00:00-04:00")
    close = datetime.fromisoformat("2024-05-01T16:00:00-04:00")
    assert in_window(
        datetime.fromisoformat("2024-05-01T15:59:59-04:00"), start, end, close
    )
    assert not in_window(
        datetime.fromisoformat("2024-05-01T16:00:01-04:00"), start, end, close
    )


@pytest.mark.unit
def test_polymarket_tool_runtime_fields_are_not_llm_arguments():
    schema = get_prediction_markets.args_schema.model_json_schema()
    assert set(schema["properties"]) == {"topic", "limit"}
    assert "config" not in schema["properties"]


@pytest.mark.unit
def test_alpha_reports_filter_on_publication_not_fiscal_date(monkeypatch):
    payload = json.dumps({
        "quarterlyReports": [
            {"fiscalDateEnding": "2023-12-31", "reportedDate": "2024-04-20", "v": 1},
            {"fiscalDateEnding": "2023-09-30", "reportedDate": "2024-05-02", "v": 2},
            {"fiscalDateEnding": "2022-12-31", "v": 3},
        ]
    })
    monkeypatch.setattr(avf, "_make_api_request", lambda *a, **k: payload)
    out = json.loads(avf.get_balance_sheet("AAPL", curr_date="2024-05-01"))
    assert [row["v"] for row in out["quarterlyReports"]] == [1]
    assert out["_point_in_time"]["policy"] == "verified-publication-timestamp-only"


@pytest.mark.unit
def test_yfinance_historical_fundamentals_excluded_before_network(monkeypatch):
    monkeypatch.setattr(y_finance.yf, "Ticker", lambda *a: pytest.fail("network called"))
    out = y_finance.get_fundamentals("AAPL", "2024-05-01", run_context=_context())
    assert out.startswith("PIT_DATA_EXCLUDED:")


@pytest.mark.unit
def test_historical_insider_snapshot_is_excluded_before_network(monkeypatch):
    monkeypatch.setattr(y_finance.yf, "Ticker", lambda *a: pytest.fail("network called"))
    out = y_finance.get_insider_transactions("AAPL", run_context=_context())
    assert out.startswith("PIT_DATA_EXCLUDED:")


@pytest.mark.unit
def test_evidence_admissibility_requires_parseable_available_at_before_cutoff():
    base = {
        "source": "test", "kind": "news", "content": "x",
        "fetched_at": "2026-01-01T00:00:00Z",
    }
    unknown = UntrustedEvidence(**base)
    before = UntrustedEvidence(**base, available_at="2024-05-01T19:59:59Z")
    after = UntrustedEvidence(**base, available_at="2024-05-01T20:00:01Z")
    invalid = UntrustedEvidence(**base, available_at="not-a-time")
    cutoff = "2024-05-01T16:00:00-04:00"
    assert not is_backtest_admissible(unknown, cutoff)
    assert is_backtest_admissible(before, cutoff)
    assert not is_backtest_admissible(after, cutoff)
    assert not is_backtest_admissible(invalid, cutoff)


@pytest.mark.unit
def test_checkpoint_signature_binds_schema_and_config():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.ticker = "AAPL"
    graph.selected_analysts = ("market",)
    graph.config = freeze_config(build_runtime_config())
    ctx = _context(config=graph.config)
    signature = graph._run_signature("stock", ctx)
    assert f"schema={WORKFLOW_SCHEMA_VERSION}" in signature
    assert f"config={ctx.config_sha256}" in signature


@pytest.mark.unit
def test_initial_state_carries_canonical_run_context():
    ctx = _context()
    state = Propagator().create_initial_state(
        "AAPL", "2024-05-01", config=build_runtime_config(), run_context=ctx,
        output_language="Portuguese",
    )
    assert state["run_context"] == ctx.to_dict()
    assert state["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION
    assert state["output_language"] == "Portuguese"


@pytest.mark.unit
def test_manifest_identity_is_deterministic_and_dependency_bound(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pandas==2.3.0\n", encoding="utf-8")
    ctx = _context()
    first = build_run_manifest(tmp_path, ctx, ["market", "news"])
    second = build_run_manifest(tmp_path, ctx, ["market", "news"])
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["artifacts"]["requirements.txt"]
    assert first["run_context"]["config_sha256"] == ctx.config_sha256
