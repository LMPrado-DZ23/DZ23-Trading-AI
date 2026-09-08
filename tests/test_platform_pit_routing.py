from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.config import use_config
from tradingagents.platform.contracts import RunMode
from tradingagents.platform.evidence_ledger import capture_evidence
from tradingagents.platform.llm_provenance import build_llm_reference_snapshot
from tradingagents.platform.run_context import (
    AnalysisRunContext,
    activate_run_context,
    calendar_schedule_digest,
    decision_cutoff,
)


def _context(mode: RunMode) -> AnalysisRunContext:
    created_at = datetime(2024, 1, 3 if mode is RunMode.BACKTEST else 2, 22, tzinfo=UTC)
    decision_at = decision_cutoff(
        trade_date="2024-01-02",
        asset_type="stock",
        market_calendar="XNYS",
        mode=mode,
        observed_at=created_at,
    )
    connection_id = uuid4()
    provider = build_llm_reference_snapshot(
        provider="openai",
        model="gpt-5",
        connection_id=connection_id,
        credential_version=1,
        base_url=None,
    )
    return AnalysisRunContext(
        run_id=uuid4(),
        job_id=uuid4(),
        tenant_id=uuid4(),
        requested_by_user_id=uuid4(),
        symbol="AAPL",
        asset_type="stock",
        trade_date="2024-01-02",
        decision_at=decision_at,
        market_calendar="XNYS",
        mode=mode,
        provider="openai",
        model="gpt-5",
        llm_connection_id=connection_id,
        llm_credential_version=1,
        selected_analysts=("fundamentals", "news"),
        config_digest=hashlib.sha256(b"config").hexdigest(),
        provider_digest=provider.digest,
        provider_endpoint=provider.endpoint,
        provider_allowed_origins=provider.allowed_origins,
        transport_policy_version=provider.transport_policy_version,
        provider_adapter_digest=provider.adapter_build_digest,
        application_build_digest=provider.application_build_digest,
        calendar_engine_version=provider.calendar_engine_version,
        calendar_schedule_digest=calendar_schedule_digest(
            market_calendar="XNYS",
            trade_date="2024-01-02",
            decision_at=decision_at,
        ),
        token_budget=250_000,
        pricing_catalog_version="44136fa355b3678a",
        max_execution_seconds=900,
        deadline_at=created_at + timedelta(seconds=900),
        created_at=created_at,
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
    assert prediction.startswith("PIT_DATA_EXCLUDED")
    assert fundamentals.startswith("PIT_DATA_EXCLUDED")
    assert news.startswith("PIT_DATA_EXCLUDED")
    assert calls == []


def test_shadow_mode_excludes_sources_without_verified_availability(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_prediction_markets"],
        "polymarket",
        lambda *_args, **_kwargs: called.append("network"),
    )
    with activate_run_context(_context(RunMode.SHADOW)):
        result = interface.route_to_vendor("get_prediction_markets", "Fed", 5)
    assert result.startswith("PIT_DATA_EXCLUDED")
    assert called == []


def test_platform_vendor_failures_never_persist_or_log_exception_secrets(
    monkeypatch, caplog
) -> None:
    secret = "tenant-secret-must-not-survive"

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"remote failed?api_key={secret}")

    monkeypatch.setitem(interface.VENDOR_METHODS["get_stock_data"], "yfinance", fail)
    with (
        use_config({"data_vendors": {"core_stock_apis": "yfinance"}}),
        activate_run_context(_context(RunMode.SHADOW)),
        caplog.at_level("WARNING"),
        pytest.raises(RuntimeError) as captured,
    ):
        interface.route_to_vendor(
            "get_stock_data", "AAPL", "2024-01-01", "2024-01-02"
        )
    assert secret not in str(captured.value)
    assert secret not in caplog.text
    assert "RuntimeError" in str(captured.value)


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


def test_platform_tool_boundary_rejects_unbounded_historical_range(monkeypatch) -> None:
    called = []
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_stock_data"],
        "yfinance",
        lambda *_args, **_kwargs: called.append(True),
    )
    with (
        activate_run_context(_context(RunMode.BACKTEST)),
        pytest.raises(ValueError, match="DATE_RANGE_TOO_LARGE"),
    ):
        interface.route_to_vendor(
            "get_stock_data", "AAPL", "1900-01-01", "2024-01-02"
        )
    assert called == []


def test_verified_snapshot_and_direct_sentiment_sources_reach_evidence_ledger(
    monkeypatch,
) -> None:
    from tradingagents.agents.analysts.sentiment_analyst import (
        _build_evidence_records,
        _record_sentiment_evidence,
    )
    from tradingagents.agents.utils import market_data_validation_tools as snapshot_tool

    context = _context(RunMode.BACKTEST)
    monkeypatch.setattr(
        snapshot_tool,
        "build_verified_market_snapshot",
        lambda *_args, **_kwargs: "raw verified snapshot",
    )
    with activate_run_context(context), capture_evidence() as ledger:
        wrapped = snapshot_tool.get_verified_market_snapshot.func(
            "AAPL", "2024-01-02", 30
        )
        evidences = _build_evidence_records(
            end_date="2024-01-02",
            news_block="PIT_DATA_EXCLUDED",
            stocktwits_block="PIT_DATA_EXCLUDED",
            reddit_block="PIT_DATA_EXCLUDED",
        )
        _record_sentiment_evidence(
            evidences,
            ticker="AAPL",
            start_date="2023-12-26",
            end_date="2024-01-02",
            excluded=True,
        )

    records = ledger.snapshot()
    assert wrapped.startswith("[UNTRUSTED EXTERNAL DATA")
    assert records[0].method == "get_verified_market_snapshot"
    assert records[0].content_digest == hashlib.sha256(
        b"raw verified snapshot"
    ).hexdigest()
    assert records[0].available_at == context.decision_at.isoformat()
    assert context.decision_at == datetime(2024, 1, 2, 21, 15, tzinfo=UTC)
    assert records[0].fetched_at
    assert records[0].availability_basis == "raw_ohlcv_latest_completed_session"
    assert len(records) == 4
    assert all(record.outcome == "excluded" for record in records[1:])
    assert all(record.available_at is None for record in records[1:])
    assert all(record.fetched_at for record in records[1:])
