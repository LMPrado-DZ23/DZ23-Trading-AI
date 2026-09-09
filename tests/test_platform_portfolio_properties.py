from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings, strategies as st

from tradingagents.platform.contracts import RiskProfile
from tradingagents.platform.portfolio import (
    SizingInput,
    cap_target_weights,
    policy_for,
    size_position,
)

finite_positive = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("1000000000"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)
unit_interval = st.decimals(
    min_value=Decimal(0),
    max_value=Decimal(1),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)


@settings(max_examples=150, deadline=None, derandomize=True)
@given(
    capital=finite_positive,
    cash=finite_positive,
    price=finite_positive,
    conviction=unit_interval,
    volatility=st.decimals(
        min_value=Decimal("0.0001"),
        max_value=Decimal("5"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    ),
    adv=finite_positive,
    current_weight=unit_interval,
    cost_bps=st.decimals(
        min_value=Decimal(0),
        max_value=Decimal(1000),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_sizing_never_exceeds_policy_cash_or_liquidity(
    capital,
    cash,
    price,
    conviction,
    volatility,
    adv,
    current_weight,
    cost_bps,
) -> None:
    policy = policy_for(RiskProfile.MODERATE)
    result = size_position(
        policy,
        SizingInput(
            capital=capital,
            available_cash=cash,
            price=price,
            conviction=conviction,
            realized_annual_volatility=volatility,
            average_daily_notional=adv,
            current_weight=current_weight,
            trading_cost_bps=cost_bps,
        ),
    )
    assert Decimal(0) <= result.target_weight <= policy.max_position_weight
    assert Decimal(0) <= result.order_notional <= cash
    assert result.order_notional <= capital * policy.max_order_notional_weight
    assert result.order_notional <= adv * policy.max_adv_participation
    assert result.quantity >= 0
    assert result.quantity * price <= result.order_notional


@settings(max_examples=100, deadline=None, derandomize=True)
@given(
    values=st.lists(unit_interval, min_size=3, max_size=8),
    profile=st.sampled_from(
        [
            RiskProfile.PRESERVATION,
            RiskProfile.CONSERVATIVE,
            RiskProfile.MODERATE,
            RiskProfile.GROWTH,
            RiskProfile.AGGRESSIVE,
        ]
    ),
)
def test_correlated_allocation_never_breaks_cluster_or_gross_caps(values, profile) -> None:
    policy = policy_for(profile)
    weights = {f"ASSET-{index}": value for index, value in enumerate(values)}
    correlations = {
        (f"ASSET-{index}", f"ASSET-{index + 1}"): Decimal("0.90")
        for index in range(len(values) - 1)
    }
    capped = cap_target_weights(
        policy,
        weights,
        correlations,
        asset_classes=dict.fromkeys(weights, "crypto"),
    )
    assert sum(capped.values()) <= policy.max_correlated_bucket_weight
    assert sum(capped.values()) <= Decimal(1) - policy.minimum_cash_weight
    assert all(Decimal(0) <= value <= policy.max_position_weight for value in capped.values())
