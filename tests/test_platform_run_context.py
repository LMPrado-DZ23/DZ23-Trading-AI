from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tradingagents.dataflows.config import get_config, use_config
from tradingagents.platform.contracts import RunMode
from tradingagents.platform.llm_provenance import build_llm_reference_snapshot
from tradingagents.platform.run_context import (
    WORKFLOW_SCHEMA_VERSION,
    AnalysisRunContext,
    activate_run_context,
    calendar_schedule_digest,
    current_run_context,
    decision_cutoff,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _context(**overrides) -> AnalysisRunContext:
    created_at = datetime.now(UTC)
    connection_id = uuid4()
    provider = build_llm_reference_snapshot(
        provider="openai",
        model="gpt-5",
        connection_id=connection_id,
        credential_version=1,
        base_url=None,
    )
    values = {
        "run_id": uuid4(),
        "job_id": uuid4(),
        "tenant_id": uuid4(),
        "requested_by_user_id": uuid4(),
        "symbol": "BTC/USDT",
        "asset_type": "crypto",
        "trade_date": "2024-01-02",
        "decision_at": decision_cutoff(
            trade_date="2024-01-02", asset_type="crypto", market_calendar="24/7"
        ),
        "market_calendar": "24/7",
        "mode": RunMode.BACKTEST,
        "provider": "openai",
        "model": "gpt-5",
        "llm_connection_id": connection_id,
        "llm_credential_version": 1,
        "selected_analysts": ("market", "news"),
        "config_digest": _digest("config"),
        "provider_digest": provider.digest,
        "provider_endpoint": provider.endpoint,
        "provider_allowed_origins": provider.allowed_origins,
        "transport_policy_version": provider.transport_policy_version,
        "provider_adapter_digest": provider.adapter_build_digest,
        "application_build_digest": provider.application_build_digest,
        "calendar_engine_version": provider.calendar_engine_version,
        "calendar_schedule_digest": calendar_schedule_digest(
            market_calendar="24/7",
            trade_date="2024-01-02",
            decision_at=decision_cutoff(
                trade_date="2024-01-02",
                asset_type="crypto",
                market_calendar="24/7",
            ),
        ),
        "token_budget": 250_000,
        "pricing_catalog_version": "44136fa355b3678a",
        "max_execution_seconds": 900,
        "deadline_at": created_at + timedelta(seconds=900),
        "created_at": created_at,
    }
    values.update(overrides)
    return AnalysisRunContext(**values)


def test_run_context_is_frozen_versioned_and_digest_is_stable():
    context = _context()
    assert context.workflow_schema_version == WORKFLOW_SCHEMA_VERSION
    assert context.digest == context.digest
    assert context.manifest()["run_context_digest"] == context.digest
    with pytest.raises(ValidationError):
        context.symbol = "ETH/USDT"
    with pytest.raises(ValidationError, match="unsupported workflow"):
        _context(workflow_schema_version=WORKFLOW_SCHEMA_VERSION + 1)


def test_run_and_dataflow_contexts_are_isolated_between_worker_threads():
    contexts = (_context(symbol="BTC/USDT"), _context(symbol="ETH/USDT"))

    def inspect(index: int) -> tuple[str, str]:
        with (
            use_config({"output_language": f"tenant-{index}"}),
            activate_run_context(contexts[index]),
        ):
            current = current_run_context()
            assert current is not None
            return current.symbol, get_config()["output_language"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = tuple(executor.map(inspect, range(2)))
    assert values == (("BTC/USDT", "tenant-0"), ("ETH/USDT", "tenant-1"))
    assert current_run_context() is None


def test_run_context_rejects_duplicate_or_unknown_analysts():
    with pytest.raises(ValidationError, match="unique"):
        _context(selected_analysts=("market", "market"))
    with pytest.raises(ValidationError, match="not supported"):
        _context(selected_analysts=("market", "evil"))


def test_operational_modes_accept_only_latest_completed_daily_session() -> None:
    with pytest.raises(ValueError, match="latest settled"):
        decision_cutoff(
            trade_date="2024-01-02",
            asset_type="stock",
            market_calendar="XNYS",
            mode=RunMode.PAPER,
            observed_at=datetime(2024, 1, 2, 20, 59, 59, tzinfo=UTC),
        )
    close = decision_cutoff(
        trade_date="2024-01-02",
        asset_type="stock",
        market_calendar="XNYS",
        mode=RunMode.PAPER,
        observed_at=datetime(2024, 1, 2, 21, 15, 1, tzinfo=UTC),
    )
    assert close == datetime(2024, 1, 2, 21, 15, tzinfo=UTC)
    with pytest.raises(ValueError, match="latest settled"):
        decision_cutoff(
            trade_date="2024-01-02",
            asset_type="stock",
            market_calendar="XNYS",
            mode=RunMode.SHADOW,
            observed_at=datetime(2024, 1, 3, 22, tzinfo=UTC),
        )
    crypto_close = decision_cutoff(
        trade_date="2024-01-01",
        asset_type="crypto",
        market_calendar="24/7",
        mode=RunMode.SHADOW,
        observed_at=datetime(2024, 1, 2, 0, 15, 1, tzinfo=UTC),
    )
    assert crypto_close == datetime(2024, 1, 2, 0, 15, tzinfo=UTC)
