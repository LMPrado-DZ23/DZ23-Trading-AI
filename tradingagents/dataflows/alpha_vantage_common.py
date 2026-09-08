import json
import os
from datetime import datetime
from io import StringIO

import pandas as pd
import requests

from .errors import VendorNotConfiguredError, VendorRateLimitError
from .http_limits import read_bounded_response
from .ohlcv_validation import OhlcvValidationError, validate_ohlcv_frame

API_BASE_URL = "https://www.alphavantage.co/query"

# Network timeout (seconds) so a stalled Alpha Vantage request can't hang the
# CLI/agents indefinitely (#990).
REQUEST_TIMEOUT = 30
MAX_RESPONSE_BYTES = 5_000_000
MAX_CSV_ROWS = 5_000


class AlphaVantageNotConfiguredError(VendorNotConfiguredError):
    """Raised when Alpha Vantage is selected but no API key is configured.

    A VendorNotConfiguredError (and thus still a ValueError), so the routing
    layer's "vendor unavailable" handling and existing ValueError callers both
    keep working.
    """
    pass


def get_api_key() -> str:
    """Retrieve the API key for Alpha Vantage from environment variables."""
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise AlphaVantageNotConfiguredError(
            "ALPHA_VANTAGE_API_KEY environment variable is not set."
        )
    return api_key

def format_datetime_for_api(date_input) -> str:
    """Convert various date formats to YYYYMMDDTHHMM format required by Alpha Vantage API."""
    if isinstance(date_input, str):
        # If already in correct format, return as-is
        if len(date_input) == 13 and 'T' in date_input:
            return date_input
        # Try to parse common date formats
        try:
            dt = datetime.strptime(date_input, "%Y-%m-%d")
            return dt.strftime("%Y%m%dT0000")
        except ValueError:
            try:
                dt = datetime.strptime(date_input, "%Y-%m-%d %H:%M")
                return dt.strftime("%Y%m%dT%H%M")
            except ValueError:
                raise ValueError(f"Unsupported date format: {date_input}") from None
    elif isinstance(date_input, datetime):
        return date_input.strftime("%Y%m%dT%H%M")
    else:
        raise ValueError(f"Date must be string or datetime object, got {type(date_input)}")

class AlphaVantageRateLimitError(VendorRateLimitError):
    """Raised when the Alpha Vantage API rate limit is exceeded."""
    pass

def _make_api_request(function_name: str, params: dict) -> dict | str:
    """Helper function to make API requests and handle responses.

    Raises:
        AlphaVantageRateLimitError: When API rate limit is exceeded
    """
    # Create a copy of params to avoid modifying the original
    api_params = params.copy()
    api_params.update({
        "function": function_name,
        "apikey": get_api_key(),
        "source": "trading_agents",
    })

    # Handle entitlement parameter if present in params or global variable
    current_entitlement = globals().get('_current_entitlement')
    entitlement = api_params.get("entitlement") or current_entitlement

    if entitlement:
        api_params["entitlement"] = entitlement
    elif "entitlement" in api_params:
        # Remove entitlement if it's None or empty
        api_params.pop("entitlement", None)

    response = None
    try:
        response = requests.get(
            API_BASE_URL,
            params=api_params,
            timeout=REQUEST_TIMEOUT,
            stream=True,
        )
        response_text = read_bounded_response(
            response,
            max_bytes=MAX_RESPONSE_BYTES,
            vendor="Alpha Vantage",
        ).decode("utf-8")
    except requests.RequestException as exc:
        raise ConnectionError(
            f"Alpha Vantage request failed: {type(exc).__name__}"
        ) from None
    finally:
        if response is not None:
            getattr(response, "close", lambda: None)()
    if getattr(response, "status_code", 200) >= 400:
        raise ValueError(
            f"Alpha Vantage request failed: HTTP {response.status_code}"
        )

    # Error responses are JSON; data responses are usually CSV (or data-keyed
    # JSON). A non-JSON body is normal data.
    try:
        response_json = json.loads(response_text)
    except json.JSONDecodeError:
        return response_text

    # Alpha Vantage reports problems via "Information" / "Note". Classify so a
    # genuine rate limit and an invalid/missing key aren't conflated (#991):
    # rate-limit phrasing is checked first because those notices also mention
    # "API key" ("your API key ... 25 requests per day").
    notice = response_json.get("Information") or response_json.get("Note")
    if notice:
        low = notice.lower()
        if any(m in low for m in ("rate limit", "requests per day", "call frequency", "premium")):
            raise AlphaVantageRateLimitError(f"Alpha Vantage rate limit exceeded: {notice}")
        if "api key" in low or "apikey" in low:
            # Reuse the existing "not configured" error so a bad key surfaces as
            # a real, actionable failure rather than a mislabeled rate limit (#991).
            raise AlphaVantageNotConfiguredError(f"Alpha Vantage API key invalid or missing: {notice}")

    return response_text



def _filter_csv_by_date_range(csv_data: str, start_date: str, end_date: str) -> str:
    """
    Filter CSV data to include only rows within the specified date range.

    Args:
        csv_data: CSV string from Alpha Vantage API
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        Filtered CSV string
    """
    if not csv_data or csv_data.strip() == "":
        return csv_data

    try:
        start_dt = pd.Timestamp(start_date, tz="UTC")
        end_dt = pd.Timestamp(end_date, tz="UTC")
    except (TypeError, ValueError):
        raise ValueError("Alpha Vantage date range is invalid") from None
    if start_dt > end_dt:
        raise ValueError("Alpha Vantage date range is reversed")
    try:
        frame = pd.read_csv(StringIO(csv_data), nrows=MAX_CSV_ROWS + 1)
    except Exception:
        raise ValueError("Alpha Vantage returned malformed CSV") from None
    if frame.empty or not len(frame.columns):
        raise ValueError("Alpha Vantage returned no rows")
    if len(frame) > MAX_CSV_ROWS:
        raise ValueError("Alpha Vantage returned too many OHLCV rows")
    date_col = frame.columns[0]
    normalized_columns = {str(column).strip().casefold() for column in frame.columns}
    if str(date_col).strip().casefold() not in {"timestamp", "date", "datetime"} or not {
        "open",
        "high",
        "low",
        "close",
        "volume",
    }.issubset(normalized_columns):
        raise ValueError("Alpha Vantage returned an unexpected OHLCV schema")
    parsed_dates = pd.to_datetime(frame[date_col], errors="coerce", utc=True)
    if parsed_dates.isna().any():
        raise ValueError("Alpha Vantage returned an invalid OHLCV timestamp")
    if parsed_dates.duplicated().any():
        raise ValueError("Alpha Vantage returned duplicate OHLCV timestamps")
    normalized_lookup = {
        str(column).strip().casefold(): column for column in frame.columns
    }
    try:
        frame = validate_ohlcv_frame(
            frame,
            column_map=normalized_lookup,
            max_rows=MAX_CSV_ROWS,
        )
    except OhlcvValidationError as exc:
        raise ValueError(f"Alpha Vantage returned invalid OHLCV: {exc}") from None
    valid = parsed_dates.notna() & (parsed_dates >= start_dt) & (parsed_dates <= end_dt)
    filtered = frame.loc[valid].copy()
    if filtered.empty:
        raise ValueError("Alpha Vantage returned no valid rows in the requested range")
    filtered[date_col] = parsed_dates.loc[valid].dt.strftime("%Y-%m-%d")
    filtered = filtered.sort_values(date_col, kind="stable")
    return filtered.to_csv(index=False)
