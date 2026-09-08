"""Raw, unadjusted daily price inputs for the backtest engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import yfinance as yf

from tradingagents.dataflows.symbol_utils import normalize_symbol
from tradingagents.portfolio.models import decimal_value


@dataclass(frozen=True, slots=True)
class PriceBar:
    session_date: date
    raw_open: Decimal
    raw_close: Decimal
    cash_dividend: Decimal = Decimal("0")
    split_ratio: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not isinstance(self.session_date, date):
            raise ValueError("session_date must be a date")
        raw_open = decimal_value(self.raw_open, "raw_open")
        raw_close = decimal_value(self.raw_close, "raw_close")
        dividend = decimal_value(self.cash_dividend, "cash_dividend")
        split_ratio = decimal_value(self.split_ratio, "split_ratio")
        if raw_open <= 0 or raw_close <= 0:
            raise ValueError("raw prices must be positive")
        if dividend < 0:
            raise ValueError("cash_dividend must be non-negative")
        if split_ratio <= 0:
            raise ValueError("split_ratio must be positive")
        object.__setattr__(self, "raw_open", raw_open)
        object.__setattr__(self, "raw_close", raw_close)
        object.__setattr__(self, "cash_dividend", dividend)
        object.__setattr__(self, "split_ratio", split_ratio)


@dataclass(frozen=True, slots=True)
class PriceSeries:
    ticker: str
    bars: tuple[PriceBar, ...]

    def __post_init__(self) -> None:
        ticker = str(self.ticker).strip()
        bars = tuple(self.bars)
        if not ticker:
            raise ValueError("price-series ticker must not be empty")
        if len(bars) < 2:
            raise ValueError("price series requires at least two sessions")
        dates = [bar.session_date for bar in bars]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError("price bars must be unique and strictly ascending")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "bars", bars)

    @classmethod
    def from_dataframe(cls, ticker: str, frame: pd.DataFrame) -> PriceSeries:
        """Build from explicit raw Open/Close columns; adjusted-only data is rejected."""
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise ValueError("price dataframe must not be empty")
        columns = {str(column).lower(): column for column in frame.columns}
        if "open" not in columns or "close" not in columns:
            raise ValueError("price dataframe must contain raw Open and Close columns")
        dividend_column = columns.get("dividends")
        split_column = columns.get("stock splits")
        bars = []
        for index, row in frame.sort_index().iterrows():
            timestamp = pd.Timestamp(index)
            dividend = row[dividend_column] if dividend_column else 0
            split = row[split_column] if split_column else 0
            dividend = 0 if pd.isna(dividend) else dividend
            split = 0 if pd.isna(split) else split
            bars.append(PriceBar(
                session_date=timestamp.date(),
                raw_open=decimal_value(row[columns["open"]], "raw_open"),
                raw_close=decimal_value(row[columns["close"]], "raw_close"),
                cash_dividend=dividend,
                split_ratio=(split if split else 1),
            ))
        return cls(ticker=ticker, bars=tuple(bars))


def load_yfinance_price_series(
    ticker: str,
    start: str | date,
    end: str | date,
    *,
    history_client: Any = None,
) -> PriceSeries:
    """Fetch unadjusted daily bars. ``end`` follows yfinance's exclusive convention."""
    client = history_client or yf.Ticker(normalize_symbol(ticker))
    frame = client.history(
        start=str(start),
        end=str(end),
        auto_adjust=False,
        actions=True,
    )
    if "Adj Close" in frame.columns:
        frame = frame.drop(columns=["Adj Close"])
    return PriceSeries.from_dataframe(ticker, frame)
