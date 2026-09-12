"""Pure position-sizing policy: the LLM never chooses capital or quantity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from tradingagents.agents.schemas import PortfolioRating
from tradingagents.agents.utils.rating import RATING_REVIEW
from tradingagents.runtime import RunContext

from .models import MarketRiskSnapshot, PortfolioConstraints, PortfolioSnapshot

SIZING_SCHEMA_VERSION = "tradingagents/sizing/1"

_RISK_MULTIPLIER = {
    PortfolioRating.BUY: Decimal("1"),
    PortfolioRating.OVERWEIGHT: Decimal("0.65"),
    PortfolioRating.UNDERWEIGHT: Decimal("0.25"),
    PortfolioRating.SELL: Decimal("0"),
}


class SizingRejected(ValueError):
    """The sizing inputs cannot prove a safe point-in-time decision."""


@dataclass(frozen=True, slots=True)
class SizingDecision:
    ticker: str
    rating: str
    action: str
    current_quantity: Decimal
    target_quantity: Decimal
    order_quantity: Decimal
    decision_price: Decimal
    current_weight: Decimal
    target_weight: Decimal
    target_notional: Decimal
    drawdown: Decimal
    constraints_applied: tuple[str, ...]
    blocked_reason: str | None
    decision_at: str
    schema: str = SIZING_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key, item in tuple(value.items()):
            if isinstance(item, Decimal):
                value[key] = str(item)
        value["constraints_applied"] = list(self.constraints_applied)
        return value


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return (units * step).quantize(step)


def validate_sizing_inputs(
    context: RunContext,
    portfolio: PortfolioSnapshot,
    market: MarketRiskSnapshot,
    constraints: PortfolioConstraints,
) -> None:
    if market.ticker != context.ticker:
        raise SizingRejected("market-risk ticker does not match RunContext")
    if portfolio.as_of > context.decision_at:
        raise SizingRejected("portfolio snapshot is newer than the decision cutoff")
    if market.as_of > context.decision_at:
        raise SizingRejected("market-risk snapshot is newer than the decision cutoff")
    max_age = timedelta(days=float(constraints.max_market_data_age_days))
    if context.decision_at - market.as_of > max_age:
        raise SizingRejected("market-risk snapshot is stale")
    if portfolio.as_of != market.as_of:
        raise SizingRejected("portfolio and market-risk snapshots must share one as_of")
    current = portfolio.position(context.ticker)
    if current is not None and current.mark_price != market.price:
        raise SizingRejected("position mark_price does not match the market-risk price")


def size_position(
    signal: str,
    context: RunContext,
    portfolio: PortfolioSnapshot,
    market: MarketRiskSnapshot,
    constraints: PortfolioConstraints | None = None,
) -> SizingDecision:
    """Convert a five-tier rating into a capped long-only target quantity."""
    policy = constraints or PortfolioConstraints()
    validate_sizing_inputs(context, portfolio, market, policy)
    current = portfolio.position(context.ticker)
    current_quantity = current.quantity if current else Decimal("0")
    current_notional = current_quantity * market.price
    current_weight = current_notional / portfolio.equity

    if signal == RATING_REVIEW:
        return SizingDecision(
            ticker=context.ticker,
            rating=signal,
            action="HOLD",
            current_quantity=current_quantity,
            target_quantity=current_quantity,
            order_quantity=Decimal("0"),
            decision_price=market.price,
            current_weight=current_weight,
            target_weight=current_weight,
            target_notional=current_notional,
            drawdown=portfolio.drawdown,
            constraints_applied=(),
            blocked_reason="review_required",
            decision_at=context.decision_at.isoformat(),
        )

    try:
        parsed_rating = PortfolioRating(signal)
    except ValueError as exc:
        raise SizingRejected(f"unsupported portfolio rating {signal!r}") from exc
    rating = parsed_rating.value
    blocked_reason = None
    if parsed_rating is PortfolioRating.HOLD:
        desired_weight = current_weight
    else:
        desired_weight = (
            policy.target_annualized_volatility
            / market.annualized_volatility
            * _RISK_MULTIPLIER[parsed_rating]
        )
        if parsed_rating is PortfolioRating.UNDERWEIGHT:
            desired_weight = min(current_weight, desired_weight)

    constraints_applied: list[str] = []
    if portfolio.drawdown >= policy.max_drawdown:
        desired_weight = Decimal("0")
        blocked_reason = "drawdown_circuit_breaker"
        constraints_applied.append("max_drawdown")

    target_weight = max(Decimal("0"), desired_weight)
    if target_weight > policy.max_position_weight:
        target_weight = policy.max_position_weight
        constraints_applied.append("max_position_weight")

    other_values = [
        position.market_value
        for position in portfolio.positions
        if position.ticker != context.ticker
    ]
    other_gross_weight = sum(other_values, Decimal("0")) / portfolio.equity
    gross_capacity = max(Decimal("0"), policy.max_gross_exposure - other_gross_weight)
    if target_weight > gross_capacity:
        target_weight = gross_capacity
        constraints_applied.append("max_gross_exposure")

    other_net_weight = other_gross_weight  # Long-only v1: net equals gross.
    net_capacity = max(Decimal("0"), policy.max_net_exposure - other_net_weight)
    if target_weight > net_capacity:
        target_weight = net_capacity
        constraints_applied.append("max_net_exposure")

    target_notional = target_weight * portfolio.equity
    target_quantity = _floor_to_step(target_notional / market.price, policy.quantity_step)
    target_notional = target_quantity * market.price
    target_weight = target_notional / portfolio.equity
    order_quantity = target_quantity - current_quantity
    if (
        order_quantity > 0
        and order_quantity * market.price < policy.min_trade_notional
    ):
        target_quantity = current_quantity
        order_quantity = Decimal("0")
        target_notional = current_quantity * market.price
        target_weight = target_notional / portfolio.equity
        constraints_applied.append("min_trade_notional")

    action = "BUY" if order_quantity > 0 else "SELL" if order_quantity < 0 else "HOLD"
    return SizingDecision(
        ticker=context.ticker,
        rating=rating,
        action=action,
        current_quantity=current_quantity,
        target_quantity=target_quantity,
        order_quantity=order_quantity,
        decision_price=market.price,
        current_weight=current_weight,
        target_weight=target_weight,
        target_notional=target_notional,
        drawdown=portfolio.drawdown,
        constraints_applied=tuple(dict.fromkeys(constraints_applied)),
        blocked_reason=blocked_reason,
        decision_at=context.decision_at.isoformat(),
    )
