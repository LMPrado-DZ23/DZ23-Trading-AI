"""Immutable inputs for deterministic, long-only portfolio sizing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_value(value: Any, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return parsed


def aware_datetime(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class Position:
    ticker: str
    quantity: Decimal
    mark_price: Decimal

    def __post_init__(self) -> None:
        ticker = str(self.ticker).strip()
        quantity = decimal_value(self.quantity, "quantity")
        mark_price = decimal_value(self.mark_price, "mark_price")
        if not ticker:
            raise ValueError("position ticker must not be empty")
        if quantity < 0:
            raise ValueError("long-only position quantity must be non-negative")
        if mark_price <= 0:
            raise ValueError("mark_price must be positive")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "mark_price", mark_price)

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.mark_price


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    as_of: datetime
    cash: Decimal
    equity: Decimal
    peak_equity: Decimal
    positions: tuple[Position, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", aware_datetime(self.as_of, "portfolio as_of"))
        cash = decimal_value(self.cash, "cash")
        equity = decimal_value(self.equity, "equity")
        peak = decimal_value(self.peak_equity, "peak_equity")
        positions = tuple(self.positions)
        if cash < 0:
            raise ValueError("cash must be non-negative for a long-only portfolio")
        if equity <= 0:
            raise ValueError("equity must be positive")
        if peak <= 0 or peak < equity:
            raise ValueError("peak_equity must be positive and at least current equity")
        tickers = [position.ticker for position in positions]
        if len(tickers) != len(set(tickers)):
            raise ValueError("portfolio positions must have unique tickers")
        marked_equity = cash + sum(
            (position.market_value for position in positions), Decimal("0")
        )
        if abs(marked_equity - equity) > Decimal("0.01"):
            raise ValueError("equity must equal cash plus marked long positions")
        object.__setattr__(self, "cash", cash)
        object.__setattr__(self, "equity", equity)
        object.__setattr__(self, "peak_equity", peak)
        object.__setattr__(self, "positions", positions)

    def position(self, ticker: str) -> Position | None:
        return next((item for item in self.positions if item.ticker == ticker), None)

    @property
    def drawdown(self) -> Decimal:
        return max(Decimal("0"), Decimal("1") - self.equity / self.peak_equity)


@dataclass(frozen=True, slots=True)
class MarketRiskSnapshot:
    ticker: str
    as_of: datetime
    price: Decimal
    annualized_volatility: Decimal

    def __post_init__(self) -> None:
        ticker = str(self.ticker).strip()
        price = decimal_value(self.price, "price")
        volatility = decimal_value(self.annualized_volatility, "annualized_volatility")
        if not ticker:
            raise ValueError("market-risk ticker must not be empty")
        if price <= 0:
            raise ValueError("price must be positive")
        if volatility <= 0:
            raise ValueError("annualized_volatility must be positive")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "as_of", aware_datetime(self.as_of, "market-risk as_of"))
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "annualized_volatility", volatility)


@dataclass(frozen=True, slots=True)
class PortfolioConstraints:
    target_annualized_volatility: Decimal = Decimal("0.10")
    max_position_weight: Decimal = Decimal("0.20")
    max_gross_exposure: Decimal = Decimal("1.00")
    max_net_exposure: Decimal = Decimal("1.00")
    max_drawdown: Decimal = Decimal("0.15")
    max_market_data_age_days: Decimal = Decimal("5")
    quantity_step: Decimal = Decimal("0.000001")
    min_trade_notional: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        names = (
            "target_annualized_volatility",
            "max_position_weight",
            "max_gross_exposure",
            "max_net_exposure",
            "max_drawdown",
            "max_market_data_age_days",
            "quantity_step",
            "min_trade_notional",
        )
        for name in names:
            object.__setattr__(self, name, decimal_value(getattr(self, name), name))
        if not Decimal("0") < self.target_annualized_volatility <= Decimal("1"):
            raise ValueError("target_annualized_volatility must be in (0, 1]")
        if not Decimal("0") < self.max_position_weight <= Decimal("1"):
            raise ValueError("max_position_weight must be in (0, 1]")
        if not Decimal("0") < self.max_net_exposure <= self.max_gross_exposure <= Decimal("1"):
            raise ValueError("exposure limits must satisfy 0 < net <= gross <= 1")
        if not Decimal("0") < self.max_drawdown < Decimal("1"):
            raise ValueError("max_drawdown must be in (0, 1)")
        if not Decimal("0") <= self.max_market_data_age_days <= Decimal("365"):
            raise ValueError("max_market_data_age_days must be between 0 and 365")
        if self.quantity_step <= 0:
            raise ValueError("quantity_step must be positive")
        if self.min_trade_notional < 0:
            raise ValueError("min_trade_notional must be non-negative")

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> PortfolioConstraints:
        return cls(**(value or {}))
