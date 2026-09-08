"""Shared fail-closed validation for untrusted OHLCV vendor frames."""

from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd


class OhlcvValidationError(ValueError):
    """Raised when a vendor frame cannot be trusted as raw OHLCV."""


def validate_ohlcv_frame(
    frame: pd.DataFrame,
    *,
    column_map: Mapping[str, object] | None = None,
    max_rows: int = 5_000,
) -> pd.DataFrame:
    """Return a numeric copy after enforcing OHLCV shape and invariants.

    No price or volume is imputed.  A partially published or internally
    inconsistent candle is rejected instead of being relabelled as verified.
    """

    if frame.empty:
        raise OhlcvValidationError("OHLCV frame is empty")
    if len(frame) > max_rows:
        raise OhlcvValidationError("OHLCV frame exceeds the row limit")
    lookup = dict(column_map or {name.casefold(): name for name in frame.columns})
    required = ("open", "high", "low", "close", "volume")
    if any(name not in lookup for name in required):
        raise OhlcvValidationError("OHLCV frame has an invalid schema")

    result = frame.copy()
    numeric: dict[str, pd.Series] = {}
    for name in required:
        column = lookup[name]
        values = pd.to_numeric(result[column], errors="coerce")
        if values.isna().any() or not values.map(math.isfinite).all():
            raise OhlcvValidationError(f"OHLCV {name} contains a non-finite value")
        numeric[name] = values.astype(float)
        result[column] = values

    if any((numeric[name] <= 0).any() for name in ("open", "high", "low", "close")):
        raise OhlcvValidationError("OHLC prices must be positive")
    if (numeric["volume"] < 0).any():
        raise OhlcvValidationError("OHLCV volume cannot be negative")
    if (
        (numeric["low"] > numeric["high"]).any()
        or (numeric["open"] < numeric["low"]).any()
        or (numeric["open"] > numeric["high"]).any()
        or (numeric["close"] < numeric["low"]).any()
        or (numeric["close"] > numeric["high"]).any()
    ):
        raise OhlcvValidationError("OHLC price invariants are inconsistent")
    return result
