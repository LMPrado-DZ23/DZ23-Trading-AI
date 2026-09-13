from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from tradingagents.domain.contracts import (
    MarketSnapshot,
    PortfolioSnapshot,
    RiskDecision,
    TradeDecision,
)
from tradingagents.domain.enums import RiskStatus

from .limits import RiskLimits


class RiskEngine:
    """Validação determinística. Nenhum texto de LLM participa da autorização."""

    def __init__(self, limits: RiskLimits | None = None, version: str = "risk-v1"):
        self.limits = limits or RiskLimits()
        self.version = version

    def evaluate(self, decision: TradeDecision, portfolio: PortfolioSnapshot,
                 snapshot: MarketSnapshot, *, now: datetime | None = None) -> RiskDecision:
        reasons: list[str] = []
        checked: list[str] = []
        current = now or datetime.now(timezone.utc)
        age = max(0, int((current - snapshot.observed_at).total_seconds()))
        checked.append("feed_age")
        if age > self.limits.max_feed_age_seconds:
            reasons.append("STALE_FEED")
        if snapshot.ask and snapshot.bid:
            spread_pct = (snapshot.ask - snapshot.bid) / snapshot.last if snapshot.last else Decimal("1")
            checked.append("spread")
            if spread_pct > self.limits.max_spread_pct:
                reasons.append("SPREAD_TOO_WIDE")
        checked.extend(["risk_pct_trade", "daily_loss", "drawdown", "exposure", "positions", "risk_reward"])
        if decision.risk_pct_equity > self.limits.max_risk_pct_trade:
            reasons.append("TRADE_RISK_LIMIT")
        if portfolio.equity <= 0:
            reasons.append("NO_EQUITY")
        elif portfolio.daily_pnl < -(portfolio.equity * self.limits.max_daily_loss_pct):
            reasons.append("DAILY_LOSS_LIMIT")
        if portfolio.drawdown_pct >= self.limits.max_drawdown_pct:
            reasons.append("DRAWDOWN_LIMIT")
        if len(portfolio.positions) >= self.limits.max_positions:
            reasons.append("POSITION_COUNT_LIMIT")
        stop_distance = abs(decision.entry_price_reference - decision.stop_loss)
        target_distance = abs(decision.take_profit - decision.entry_price_reference)
        if stop_distance <= 0 or target_distance / stop_distance < self.limits.min_risk_reward:
            reasons.append("RISK_REWARD_TOO_LOW")
        if portfolio.gross_exposure >= portfolio.equity * self.limits.max_gross_exposure_pct:
            reasons.append("GROSS_EXPOSURE_LIMIT")
        status = RiskStatus.REJECT if reasons else RiskStatus.APPROVE
        quantity = Decimal("0") if reasons else decision.quantity
        amount = Decimal("0") if reasons else decision.risk_amount
        return RiskDecision(
            risk_decision_id=f"risk-{decision.decision_id}",
            decision_id=decision.decision_id,
            status=status,
            approved_quantity=quantity,
            approved_risk_amount=amount,
            reason_codes=tuple(reasons),
            limits_checked=tuple(checked),
            portfolio_snapshot_id=portfolio.snapshot_id,
            engine_version=self.version,
            created_at=current,
        )
