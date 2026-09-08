from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.config import use_config
from tradingagents.platform.contracts import RunMode
from tradingagents.platform.run_context import (
    AnalysisRunContext,
    activate_run_context,
    decision_cutoff,
)


def _context(mode: RunMode) -> AnalysisRunContext:
    return AnalysisRunContext(
        run_id=uuid4(),
        job_id=uuid4(),
        tenant_id=uuid4(),
        requested_by_user_id=uuid4(),
        symbol="AAPL",
        asset_type="stock",
        trade_date="2024-01-02",
        decision_at=decision_cutoff(
            trade_date="2024-01-02", asset_type="stock", market_calendar="XNYS"
        ),
        market_calendar="XNYS",
        mode=mode,
        provider="openai",
        model="gpt-5",
        llm_connection_id=uuid4(),
        llm_credential_version=1,
        selected_analysts=("fundamentals", "news"),
        config_digest=hashlib.sha256(b"config").hexdigest(),
        provider_digest=hashlib.sha256(b"provider").hexdigest(),
        created_at=datetime.now(UTC),
    )


def test_backtest_excludes_live_polymarket_and_current_fundamentals_before_network(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def forbidden(*_args, **_kwargs):
        calls.append("network")
        raise AssertionError("live vendor was called during backtest")

    monkeypatch.setitem(interface.VENDOR_METHODS["get_prediction_markets"], "polymarket", forbidden)
    monkeypatch.setitem(interface.VENDOR_METHODS["get_fundamentals"], "yfinance", forbidden)
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"], "yfinance", forbidden)
    with activate_run_context(_context(RunMode.BACKTEST)):
        prediction = interface.route_to_vendor("get_prediction_markets", "Fed", 5)
        fundamentals = interface.route_to_vendor("get_fundamentals", "AAPL", "2024-01-02")
        news = interface.route_to_vendor("get_news", "AAPL", "2023-12-20", "2024-01-02")
    assert prediction.startswith("BACKTEST_DATA_EXCLUDED")
    assert fundamentals.startswith("BACKTEST_DATA_EXCLUDED")
    assert news.startswith("BACKTEST_DATA_EXCLUDED")
    assert calls == []


def test_shadow_mode_does_not_apply_historical_exclusion(monkeypatch) -> None:
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_prediction_markets"],
        "polymarket",
        lambda *_args, **_kwargs: "LIVE-SNAPSHOT",
    )
    with activate_run_context(_context(RunMode.SHADOW)):
        assert interface.route_to_vendor("get_prediction_markets", "Fed", 5) == "LIVE-SNAPSHOT"


def test_platform_vendor_failures_never_persist_or_log_exception_secrets(
    monkeypatch, caplog
) -> None:
    secret = "tenant-secret-must-not-survive"

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"remote failed?api_key={secret}")

    monkeypatch.setitem(interface.VENDOR_METHODS["get_macro_indicators"], "fred", fail)
    with (
        use_config({"data_vendors": {"macro_data": "fred"}}),
        activate_run_context(_context(RunMode.SHADOW)),
        caplog.at_level("WARNING"),
    ):
        result = interface.route_to_vendor("get_macro_indicators", "cpi", "2024-01-02")
    assert secret not in result
    assert secret not in caplog.text
    assert "RuntimeError" in result


def test_platform_tool_boundary_rejects_future_dates_and_symbol_switch(monkeypatch) -> None:
    called = []
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_stock_data"],
        "yfinance",
        lambda *_args, **_kwargs: called.append(True),
    )
    with activate_run_context(_context(RunMode.BACKTEST)):
        with pytest.raises(ValueError, match="LOOKAHEAD"):
            interface.route_to_vendor("get_stock_data", "AAPL", "2024-01-01", "2026-01-01")
        with pytest.raises(ValueError, match="SYMBOL_MISMATCH"):
            interface.route_to_vendor("get_stock_data", "MSFT", "2024-01-01", "2024-01-02")
    assert called == []
