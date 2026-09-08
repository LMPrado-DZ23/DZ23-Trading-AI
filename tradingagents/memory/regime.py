"""Deterministic market-regime classification from past closes only."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from tradingagents.portfolio.models import aware_datetime, decimal_value


class MarketRegime(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class RegimeSnapshot:
    as_of: datetime
    regime: MarketRegime
    trailing_return: Decimal
    annualized_volatility: Decimal
    observations: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", aware_datetime(self.as_of, "regime as_of"))
        object.__setattr__(
            self, "trailing_return", decimal_value(self.trailing_return, "trailing_return")
        )
        object.__setattr__(
            self,
            "annualized_volatility",
            decimal_value(self.annualized_volatility, "annualized_volatility"),
        )
        if self.annualized_volatility < 0 or self.observations < 2:
            raise ValueError("regime snapshot requires at least two valid observations")


def classify_regime(
    closes: Iterable[Decimal],
    *,
    as_of: datetime,
    annualization_sessions: int = 252,
    bull_threshold: Decimal = Decimal("0.05"),
    bear_threshold: Decimal = Decimal("-0.05"),
    high_volatility_threshold: Decimal = Decimal("0.35"),
    low_volatility_threshold: Decimal = Decimal("0.10"),
) -> RegimeSnapshot:
    values = tuple(decimal_value(value, "close") for value in closes)
    if len(values) < 3 or any(value <= 0 for value in values):
        raise ValueError("regime classification requires at least three positive closes")
    if annualization_sessions <= 0:
        raise ValueError("annualization_sessions must be positive")
    returns = [float(values[index] / values[index - 1] - 1) for index in range(1, len(values))]
    volatility = Decimal(str(statistics.stdev(returns) * math.sqrt(annualization_sessions)))
    trailing_return = values[-1] / values[0] - 1
    if volatility >= high_volatility_threshold:
        regime = MarketRegime.HIGH_VOLATILITY
    elif trailing_return >= bull_threshold:
        regime = MarketRegime.BULL
    elif trailing_return <= bear_threshold:
        regime = MarketRegime.BEAR
    elif volatility <= low_volatility_threshold:
        regime = MarketRegime.LOW_VOLATILITY
    else:
        regime = MarketRegime.NEUTRAL
    return RegimeSnapshot(
        as_of=as_of,
        regime=regime,
        trailing_return=trailing_return,
        annualized_volatility=volatility,
        observations=len(returns),
    )
