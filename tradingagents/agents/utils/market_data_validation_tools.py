from datetime import datetime, timezone
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.evidence import UntrustedEvidence
from tradingagents.dataflows.interface import assert_platform_tool_request
from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot
from tradingagents.platform.run_context import current_run_context


@tool
def get_verified_market_snapshot(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "the current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[
        int, "number of recent trading rows to include for sanity-checking"
    ] = 30,
) -> str:
    """Deterministic verification snapshot for exact market-data claims.

    Returns the latest OHLCV row on or before curr_date, common technical
    indicators, and recent closes. Call this before making exact claims about
    price levels, Bollinger bands, RSI, MACD, moving averages, support /
    resistance, or historical comparisons, and treat it as the source of truth.
    """
    assert_platform_tool_request(
        "get_verified_market_snapshot", (symbol, curr_date, look_back_days)
    )
    snapshot = build_verified_market_snapshot(symbol, curr_date, look_back_days)
    context = current_run_context()
    if context is None:
        return snapshot

    evidence = UntrustedEvidence(
        source="yfinance:raw_ohlcv_snapshot",
        kind="market_data",
        content=snapshot,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        available_at=context.decision_at.isoformat(),
        as_of=curr_date,
        ref=f"{context.run_id}:verified-market-snapshot",
    )
    from tradingagents.platform.evidence_ledger import record_untrusted_evidence

    record_untrusted_evidence(
        evidence,
        method="get_verified_market_snapshot",
        outcome="used",
        args=(symbol, curr_date, look_back_days),
        availability_basis="raw_ohlcv_latest_completed_session",
    )
    return evidence.to_prompt_block()
