from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_risk_pct_trade: Decimal = Decimal("0.005")
    max_daily_loss_pct: Decimal = Decimal("0.02")
    max_drawdown_pct: Decimal = Decimal("0.10")
    max_gross_exposure_pct: Decimal = Decimal("1.0")
    max_positions: int = 10
    min_risk_reward: Decimal = Decimal("1.2")
    max_feed_age_seconds: int = 30
    max_spread_pct: Decimal = Decimal("0.01")
