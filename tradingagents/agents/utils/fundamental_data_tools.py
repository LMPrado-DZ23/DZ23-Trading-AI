from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.runtime import runtime_from_runnable


@tool
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
    config: RunnableConfig,
) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing comprehensive fundamental data
    """
    runtime_config, run_context = runtime_from_runnable(config)
    return route_to_vendor(
        "get_fundamentals", ticker, curr_date,
        config=runtime_config, run_context=run_context,
    )


@tool
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
    *,
    config: RunnableConfig,
) -> str:
    """
    Retrieve balance sheet data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing balance sheet data
    """
    runtime_config, run_context = runtime_from_runnable(config)
    return route_to_vendor(
        "get_balance_sheet", ticker, freq, curr_date,
        config=runtime_config, run_context=run_context,
    )


@tool
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
    *,
    config: RunnableConfig,
) -> str:
    """
    Retrieve cash flow statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing cash flow statement data
    """
    runtime_config, run_context = runtime_from_runnable(config)
    return route_to_vendor(
        "get_cashflow", ticker, freq, curr_date,
        config=runtime_config, run_context=run_context,
    )


@tool
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
    *,
    config: RunnableConfig,
) -> str:
    """
    Retrieve income statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing income statement data
    """
    runtime_config, run_context = runtime_from_runnable(config)
    return route_to_vendor(
        "get_income_statement", ticker, freq, curr_date,
        config=runtime_config, run_context=run_context,
    )
