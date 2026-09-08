import csv
import io
from datetime import datetime

from tradingagents.runtime import RunContext

from .alpha_vantage_common import _filter_csv_by_date_range, _make_api_request


def get_stock(
    symbol: str,
    start_date: str,
    end_date: str,
    run_context: RunContext | None = None,
) -> str:
    """
    Returns daily market data filtered to the specified date range. Historical
    runs retain only raw OHLCV and remove adjusted/corporate-action fields.

    Args:
        symbol: The name of the equity. For example: symbol=IBM
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        CSV string containing the daily adjusted time series data filtered to the date range.
    """
    # Parse dates to determine the range
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    today = datetime.now()

    # Choose outputsize based on whether the requested range is within the latest 100 days
    # Compact returns latest 100 data points, so check if start_date is recent enough
    days_from_today_to_start = (today - start_dt).days
    outputsize = "compact" if days_from_today_to_start < 100 else "full"

    params = {
        "symbol": symbol,
        "outputsize": outputsize,
        "datatype": "csv",
    }

    response = _make_api_request("TIME_SERIES_DAILY_ADJUSTED", params)

    filtered = _filter_csv_by_date_range(response, start_date, end_date)
    if run_context is None or not run_context.historical:
        return filtered

    # The adjusted close and corporate-action columns are recalculated using
    # facts known after the historical decision. Preserve only raw OHLCV.
    reader = csv.DictReader(io.StringIO(filtered))
    safe_fields = [
        field for field in (reader.fieldnames or [])
        if field.strip().lower() in {"timestamp", "time", "open", "high", "low", "close", "volume"}
    ]
    if not safe_fields:
        return "PIT_DATA_EXCLUDED: Alpha Vantage OHLCV response had no raw OHLCV columns."
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=safe_fields, lineterminator="\n")
    writer.writeheader()
    for row in reader:
        writer.writerow({field: row.get(field, "") for field in safe_fields})
    return output.getvalue()
