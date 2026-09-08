
from tradingagents.runtime import RunContext

from .alpha_vantage_common import _make_api_request
from .fundamentals_pit import (
    filter_reports_by_publication,
    historical_cutoff,
)


def _filter_reports_by_date(
    result, curr_date: str, run_context: RunContext | None = None
):
    """Filter by real publication time; fiscalDateEnding is never a proxy."""
    cutoff = historical_cutoff(curr_date, run_context)
    if cutoff is None:
        return result
    return filter_reports_by_publication(result, cutoff)


def get_fundamentals(
    ticker: str, curr_date: str = None, run_context: RunContext | None = None
) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol using Alpha Vantage.

    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd (not used for Alpha Vantage)

    Returns:
        str: Company overview data including financial ratios and key metrics
    """
    params = {
        "symbol": ticker,
    }

    cutoff = historical_cutoff(curr_date, run_context)
    result = _make_api_request("OVERVIEW", params)
    if cutoff is not None:
        return filter_reports_by_publication(result, cutoff)
    return result


def get_balance_sheet(
    ticker: str, freq: str = "quarterly", curr_date: str = None,
    run_context: RunContext | None = None,
):
    """Retrieve balance sheet data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("BALANCE_SHEET", {"symbol": ticker})
    return _filter_reports_by_date(result, curr_date, run_context)


def get_cashflow(
    ticker: str, freq: str = "quarterly", curr_date: str = None,
    run_context: RunContext | None = None,
):
    """Retrieve cash flow statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("CASH_FLOW", {"symbol": ticker})
    return _filter_reports_by_date(result, curr_date, run_context)


def get_income_statement(
    ticker: str, freq: str = "quarterly", curr_date: str = None,
    run_context: RunContext | None = None,
):
    """Retrieve income statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("INCOME_STATEMENT", {"symbol": ticker})
    return _filter_reports_by_date(result, curr_date, run_context)
