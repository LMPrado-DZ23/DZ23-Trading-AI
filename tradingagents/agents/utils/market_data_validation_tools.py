from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot
from tradingagents.runtime import runtime_from_runnable


@tool
def get_verified_market_snapshot(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "the current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[
        int, "number of recent trading rows to include for sanity-checking"
    ] = 30,
    *,
    config: RunnableConfig,
) -> str:
    """Deterministic verification snapshot for exact market-data claims.

    Returns the latest OHLCV row on or before curr_date, common technical
    indicators, and recent closes. Call this before making exact claims about
    price levels, Bollinger bands, RSI, MACD, moving averages, support /
    resistance, or historical comparisons, and treat it as the source of truth.
    """
    runtime_config, _ = runtime_from_runnable(config)
    return build_verified_market_snapshot(
        symbol, curr_date, look_back_days, runtime_config=runtime_config
    )
