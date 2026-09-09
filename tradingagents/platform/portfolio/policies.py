"""Pure deterministic portfolio policies; no LLM or broker dependency is allowed here."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN, Decimal

from tradingagents.platform.contracts import RiskProfile

POLICY_VERSION = 1
_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class PortfolioPolicy:
    profile: RiskProfile
    target_annual_volatility: Decimal
    max_position_weight: Decimal
    max_crypto_weight: Decimal
    max_correlated_bucket_weight: Decimal
    max_daily_drawdown: Decimal
    max_total_drawdown: Decimal
    max_order_notional_weight: Decimal
    max_adv_participation: Decimal
    rebalance_band: Decimal
    minimum_cash_weight: Decimal

    @property
    def digest(self) -> str:
        payload = {key: str(value) for key, value in asdict(self).items()}
        payload["profile"] = self.profile.value
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_POLICIES = {
    RiskProfile.PRESERVATION: PortfolioPolicy(
        RiskProfile.PRESERVATION,
        Decimal("0.04"),
        Decimal("0.05"),
        Decimal("0.10"),
        Decimal("0.12"),
        Decimal("0.01"),
        Decimal("0.05"),
        Decimal("0.02"),
        Decimal("0.005"),
        Decimal("0.015"),
        Decimal("0.60"),
    ),
    RiskProfile.CONSERVATIVE: PortfolioPolicy(
        RiskProfile.CONSERVATIVE,
        Decimal("0.07"),
        Decimal("0.08"),
        Decimal("0.20"),
        Decimal("0.20"),
        Decimal("0.015"),
        Decimal("0.08"),
        Decimal("0.03"),
        Decimal("0.0075"),
        Decimal("0.02"),
        Decimal("0.40"),
    ),
    RiskProfile.MODERATE: PortfolioPolicy(
        RiskProfile.MODERATE,
        Decimal("0.10"),
        Decimal("0.12"),
        Decimal("0.35"),
        Decimal("0.30"),
        Decimal("0.02"),
        Decimal("0.12"),
        Decimal("0.05"),
        Decimal("0.01"),
        Decimal("0.025"),
        Decimal("0.25"),
    ),
    RiskProfile.GROWTH: PortfolioPolicy(
        RiskProfile.GROWTH,
        Decimal("0.15"),
        Decimal("0.18"),
        Decimal("0.55"),
        Decimal("0.40"),
        Decimal("0.03"),
        Decimal("0.18"),
        Decimal("0.08"),
        Decimal("0.015"),
        Decimal("0.03"),
        Decimal("0.15"),
    ),
    RiskProfile.AGGRESSIVE: PortfolioPolicy(
        RiskProfile.AGGRESSIVE,
        Decimal("0.22"),
        Decimal("0.25"),
        Decimal("0.75"),
        Decimal("0.55"),
        Decimal("0.05"),
        Decimal("0.25"),
        Decimal("0.12"),
        Decimal("0.02"),
        Decimal("0.04"),
        Decimal("0.08"),
    ),
}


def policy_for(profile: RiskProfile | str) -> PortfolioPolicy:
    parsed = RiskProfile(profile)
    if parsed is RiskProfile.CUSTOM:
        raise ValueError("custom profile requires a separately approved policy version")
    return _POLICIES[parsed]


@dataclass(frozen=True, slots=True)
class SizingInput:
    capital: Decimal
    available_cash: Decimal
    price: Decimal
    conviction: Decimal
    realized_annual_volatility: Decimal
    average_daily_notional: Decimal
    current_weight: Decimal = _ZERO
    trading_cost_bps: Decimal = _ZERO
    asset_class: str = "crypto"


@dataclass(frozen=True, slots=True)
class SizingResult:
    target_weight: Decimal
    target_notional: Decimal
    order_notional: Decimal
    quantity: Decimal
    limited_by: tuple[str, ...]


def cap_target_weights(
    policy: PortfolioPolicy,
    raw_weights: dict[str, Decimal],
    correlations: dict[tuple[str, str], Decimal],
    *,
    asset_classes: dict[str, str] | None = None,
    correlated_threshold: Decimal = Decimal("0.80"),
) -> dict[str, Decimal]:
    """Apply position, asset-class, correlation, gross and cash-floor caps.

    ``asset_classes`` is mandatory for non-empty allocations.  Inferring a
    class from a symbol would make the crypto limit bypassable through aliases.
    """

    if not raw_weights:
        return {}
    symbols = tuple(sorted(raw_weights))
    if asset_classes is None or set(asset_classes) != set(symbols):
        raise ValueError("asset classes must cover every allocation symbol exactly")
    if any(asset_classes[symbol] not in {"crypto", "stock"} for symbol in symbols):
        raise ValueError("unsupported allocation asset class")
    if (
        not isinstance(correlated_threshold, Decimal)
        or not correlated_threshold.is_finite()
        or correlated_threshold < Decimal("-1")
        or correlated_threshold > _ONE
    ):
        raise ValueError("correlation threshold is outside the allowed range")
    parent = {symbol: symbol for symbol in symbols}

    def find(symbol: str) -> str:
        while parent[symbol] != symbol:
            parent[symbol] = parent[parent[symbol]]
            symbol = parent[symbol]
        return symbol

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    capped: dict[str, Decimal] = {}
    for symbol in symbols:
        if not symbol or len(symbol) > 64:
            raise ValueError("invalid allocation symbol")
        value = raw_weights[symbol]
        _finite_nonnegative("target_weight", value)
        capped[symbol] = min(value, policy.max_position_weight)
    for pair, correlation in correlations.items():
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or pair[0] not in capped
            or pair[1] not in capped
        ):
            raise ValueError("correlation references an unknown symbol")
        if not isinstance(correlation, Decimal) or not correlation.is_finite():
            raise ValueError("correlation must be a finite Decimal")
        if correlation < Decimal("-1") or correlation > _ONE:
            raise ValueError("correlation is outside the allowed range")
        if correlation >= correlated_threshold:
            union(pair[0], pair[1])
    clusters: dict[str, list[str]] = {}
    for symbol in symbols:
        clusters.setdefault(find(symbol), []).append(symbol)
    for members in clusters.values():
        total = sum((capped[symbol] for symbol in members), _ZERO)
        if total > policy.max_correlated_bucket_weight:
            scale = policy.max_correlated_bucket_weight / total
            for symbol in members:
                capped[symbol] *= scale
    crypto_symbols = [symbol for symbol in symbols if asset_classes[symbol] == "crypto"]
    crypto_total = sum((capped[symbol] for symbol in crypto_symbols), _ZERO)
    if crypto_total > policy.max_crypto_weight:
        scale = policy.max_crypto_weight / crypto_total
        for symbol in crypto_symbols:
            capped[symbol] *= scale
    gross = sum(capped.values(), _ZERO)
    gross_cap = _ONE - policy.minimum_cash_weight
    if gross > gross_cap:
        scale = gross_cap / gross
        capped = {symbol: value * scale for symbol, value in capped.items()}
    return {
        symbol: value.quantize(Decimal("0.000000000001"), ROUND_DOWN)
        for symbol, value in capped.items()
    }


def _finite_nonnegative(name: str, value: Decimal, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < 0 or (positive and value <= 0):
        raise ValueError(f"{name} is outside the allowed range")


def size_position(policy: PortfolioPolicy, data: SizingInput) -> SizingResult:
    """Turn conviction and market risk into a capped spot quantity.

    The function accepts no order quantity from the caller. Every executable
    number is derived from capital, price, volatility, liquidity and policy.
    """

    for name in ("capital", "available_cash", "price", "realized_annual_volatility"):
        _finite_nonnegative(name, getattr(data, name), positive=True)
    for name in ("conviction", "average_daily_notional", "current_weight", "trading_cost_bps"):
        _finite_nonnegative(name, getattr(data, name))
    if data.conviction > _ONE or data.current_weight > _ONE:
        raise ValueError("weight and conviction cannot exceed one")
    if data.asset_class not in {"crypto", "stock", "cash"}:
        raise ValueError("unsupported asset class")

    constraints: list[tuple[str, Decimal]] = []
    volatility_weight = (
        policy.target_annual_volatility / data.realized_annual_volatility * data.conviction
    )
    constraints.append(("volatility", volatility_weight))
    constraints.append(("position_cap", policy.max_position_weight))
    if data.asset_class == "crypto":
        constraints.append(("crypto_cap", policy.max_crypto_weight))
    liquidity_weight = data.average_daily_notional * policy.max_adv_participation / data.capital
    constraints.append(("liquidity", liquidity_weight))
    constraints.append(("cash_floor", _ONE - policy.minimum_cash_weight))
    target_weight = max(_ZERO, min(value for _, value in constraints))
    limited_by = tuple(name for name, value in constraints if value == target_weight)
    target_notional = (data.capital * target_weight).quantize(Decimal("0.00000001"), ROUND_DOWN)
    current_notional = data.capital * data.current_weight
    delta = max(_ZERO, target_notional - current_notional)
    max_order = min(
        data.capital * policy.max_order_notional_weight,
        data.available_cash,
        data.average_daily_notional * policy.max_adv_participation,
    )
    if delta <= data.capital * policy.rebalance_band:
        order_notional = _ZERO
        limited_by += ("rebalance_band",)
    else:
        order_notional = min(delta, max_order)
        if order_notional < delta:
            limited_by += ("order_cap",)
    if data.trading_cost_bps > 0:
        order_notional = order_notional * (
            _ONE - min(data.trading_cost_bps / Decimal(10_000), Decimal("0.10"))
        )
    order_notional = order_notional.quantize(Decimal("0.00000001"), ROUND_DOWN)
    quantity = (order_notional / data.price).quantize(Decimal("0.00000001"), ROUND_DOWN)
    return SizingResult(target_weight, target_notional, order_notional, quantity, limited_by)
