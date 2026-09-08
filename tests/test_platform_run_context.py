from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tradingagents.dataflows.config import get_config, use_config
from tradingagents.platform.contracts import RunMode
from tradingagents.platform.run_context import (
    WORKFLOW_SCHEMA_VERSION,
    AnalysisRunContext,
    activate_run_context,
    current_run_context,
    decision_cutoff,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _context(**overrides) -> AnalysisRunContext:
    values = {
        "run_id": uuid4(),
        "job_id": uuid4(),
        "tenant_id": uuid4(),
        "requested_by_user_id": uuid4(),
        "symbol": "BTC/USDT",
        "asset_type": "crypto",
        "trade_date": "2026-09-08",
        "decision_at": decision_cutoff(
            trade_date="2026-09-08", asset_type="crypto", market_calendar="24/7"
        ),
        "market_calendar": "24/7",
        "mode": RunMode.SHADOW,
        "provider": "openai",
        "model": "gpt-5",
        "llm_connection_id": uuid4(),
        "llm_credential_version": 1,
        "selected_analysts": ("market", "news"),
        "config_digest": _digest("config"),
        "provider_digest": _digest("provider"),
        "created_at": datetime.now(UTC),
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
