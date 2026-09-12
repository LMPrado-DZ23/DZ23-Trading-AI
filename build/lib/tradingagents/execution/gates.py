"""Validation from graph evidence to a non-live execution intent."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from tradingagents.agents.utils.rating import RATING_REVIEW, RATINGS_5_TIER
from tradingagents.gates import assert_secure_for_capital
from tradingagents.observability import TELEMETRY_SCHEMA_VERSION
from tradingagents.portfolio.sizing import SIZING_SCHEMA_VERSION
from tradingagents.runtime import WORKFLOW_SCHEMA_VERSION

from .models import ExecutionIntent, _authorized_intent


class ExecutionRejected(ValueError):
    """Execution evidence is missing, inconsistent, stale or unsafe."""


_PLAN_KEYS = {
    "schema", "ticker", "rating", "action", "current_quantity",
    "target_quantity", "order_quantity", "decision_price", "current_weight",
    "target_weight", "target_notional", "drawdown", "constraints_applied",
    "blocked_reason", "decision_at",
}
_CONTEXT_KEYS = {
    "ticker", "trade_date", "asset_type", "decision_at", "market_timezone",
    "workflow_schema_version", "config_sha256", "run_mode", "historical",
}


def _positive_decimal(value: Any, name: str, *, allow_zero: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutionRejected(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        raise ExecutionRejected(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return parsed


def _validate_telemetry(
    telemetry: dict[str, Any],
    final_state: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    if not isinstance(telemetry, dict):
        raise ExecutionRejected("telemetry evidence is required")
    if telemetry.get("schema") != TELEMETRY_SCHEMA_VERSION:
        raise ExecutionRejected("telemetry schema is missing or incompatible")
    if telemetry.get("status") != "succeeded":
        raise ExecutionRejected("only a succeeded graph run may produce an intent")
    context = telemetry.get("run_context")
    if not isinstance(context, dict) or set(context) != _CONTEXT_KEYS:
        raise ExecutionRejected("telemetry lacks run context")
    if context.get("workflow_schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise ExecutionRejected("workflow schema is incompatible")
    if context.get("asset_type") not in {"stock", "crypto", "forex", "index"}:
        raise ExecutionRejected("telemetry asset type is invalid")
    if not isinstance(context.get("historical"), bool):
        raise ExecutionRejected("telemetry historical classification is invalid")
    config_sha = context.get("config_sha256")
    if not isinstance(config_sha, str) or len(config_sha) != 64:
        raise ExecutionRejected("telemetry config digest is invalid")
    if context.get("run_mode") != mode:
        raise ExecutionRejected("telemetry run mode does not match execution mode")
    if context.get("ticker") != final_state.get("company_of_interest"):
        raise ExecutionRejected("telemetry ticker does not match final state")
    if context.get("trade_date") != final_state.get("trade_date"):
        raise ExecutionRejected("telemetry trade date does not match final state")
    run_id = telemetry.get("run_id")
    if (
        not isinstance(run_id, str)
        or len(run_id) != 32
        or any(character not in "0123456789abcdef" for character in run_id.lower())
    ):
        raise ExecutionRejected("telemetry run id is invalid")
    run_key = telemetry.get("run_key")
    if (
        not isinstance(run_key, str)
        or len(run_key) != 64
        or any(character not in "0123456789abcdef" for character in run_key.lower())
    ):
        raise ExecutionRejected("telemetry run key is invalid")
    return context


def prepare_execution(
    final_state: dict[str, Any],
    telemetry: dict[str, Any],
    *,
    mode: Literal["shadow", "paper"],
    now: datetime | None = None,
    max_decision_age: timedelta = timedelta(hours=36),
) -> ExecutionIntent:
    if mode not in {"shadow", "paper"}:
        raise ExecutionRejected("real-capital execution mode is not implemented")
    if max_decision_age <= timedelta(0):
        raise ExecutionRejected("max_decision_age must be positive")
    try:
        assert_secure_for_capital(final_state)
    except RuntimeError as exc:
        raise ExecutionRejected(str(exc)) from exc
    context = _validate_telemetry(telemetry, final_state, mode)
    plan = final_state.get("portfolio_plan")
    if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS:
        raise ExecutionRejected("closed sizing plan is required")
    if plan.get("schema") != SIZING_SCHEMA_VERSION:
        raise ExecutionRejected("sizing schema is missing or incompatible")
    if plan.get("ticker") != context.get("ticker"):
        raise ExecutionRejected("sizing ticker does not match run context")
    try:
        decision_at = datetime.fromisoformat(str(plan.get("decision_at")))
    except ValueError as exc:
        raise ExecutionRejected("sizing decision_at is invalid") from exc
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ExecutionRejected("sizing decision_at must be timezone-aware")
    if decision_at.isoformat() != context.get("decision_at"):
        raise ExecutionRejected("sizing decision time does not match run context")
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ExecutionRejected("execution clock must be timezone-aware")
    age = observed.astimezone(timezone.utc) - decision_at.astimezone(timezone.utc)
    if age < timedelta(minutes=-5) or age > max_decision_age:
        raise ExecutionRejected("decision is outside the permitted execution window")

    action = plan.get("action")
    if action not in {"BUY", "SELL", "HOLD"}:
        raise ExecutionRejected("sizing action is invalid")
    try:
        signed_quantity = Decimal(str(plan.get("order_quantity")))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutionRejected("order quantity must be finite") from exc
    if not signed_quantity.is_finite():
        raise ExecutionRejected("order quantity must be finite")
    current_quantity = _positive_decimal(
        plan.get("current_quantity"), "current_quantity", allow_zero=True
    )
    target_quantity = _positive_decimal(
        plan.get("target_quantity"), "target_quantity", allow_zero=True
    )
    if target_quantity - current_quantity != signed_quantity:
        raise ExecutionRejected("sizing quantities do not reconcile")
    quantity = abs(signed_quantity)
    expected = "BUY" if signed_quantity > 0 else "SELL" if signed_quantity < 0 else "HOLD"
    if action != expected:
        raise ExecutionRejected("sizing action and quantity disagree")
    rating = plan.get("rating")
    if rating not in (*RATINGS_5_TIER, RATING_REVIEW):
        raise ExecutionRejected("sizing rating is invalid")
    blocked_reason = plan.get("blocked_reason")
    if blocked_reason is not None and not isinstance(blocked_reason, str):
        raise ExecutionRejected("blocked reason must be text or null")
    if (blocked_reason is not None or rating == RATING_REVIEW) and action != "HOLD":
        raise ExecutionRejected("a blocked sizing plan cannot trade")
    decision_price = _positive_decimal(plan.get("decision_price"), "decision_price")
    target_notional = _positive_decimal(
        plan.get("target_notional"), "target_notional", allow_zero=True
    )
    if abs(target_notional - target_quantity * decision_price) > Decimal("0.01"):
        raise ExecutionRejected("sizing target notional does not reconcile")
    for name in ("current_weight", "target_weight", "drawdown"):
        value = _positive_decimal(plan.get(name), name, allow_zero=True)
        if value > 1:
            raise ExecutionRejected(f"{name} must not exceed one")
    constraints = plan.get("constraints_applied")
    if not isinstance(constraints, list) or not all(
        isinstance(item, str) for item in constraints
    ):
        raise ExecutionRejected("constraints_applied must be a list of names")

    sizing_canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    sizing_sha = hashlib.sha256(sizing_canonical.encode("utf-8")).hexdigest()
    identity = "|".join((mode, str(telemetry["run_key"])))
    client_order_id = f"ta-{hashlib.sha256(identity.encode()).hexdigest()[:48]}"
    return _authorized_intent(
        mode=mode,
        ticker=str(context["ticker"]),
        asset_type=str(context["asset_type"]),
        action=action,
        quantity=quantity,
        decision_price=decision_price,
        decision_at=decision_at.isoformat(),
        telemetry_run_id=telemetry["run_id"],
        telemetry_run_key=telemetry["run_key"],
        sizing_sha256=sizing_sha,
        client_order_id=client_order_id,
    )
