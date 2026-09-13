"""Immutable scope contracts for multi-account capital execution.

This module contains no persistence, credential material, broker calls, or
authorization side effects.  The contracts make the complete capital scope a
required part of an execution intent so downstream authorities cannot infer it
from ambient configuration.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.platform.contracts import (
    MandateStatus,
    OrderSide,
    OrderType,
    RiskProfile,
    RunMode,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Venue = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")]
Symbol = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9./_=-]*$"),
]
Currency = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]{2,11}$")]

_DIGEST_PREFIX = b"tradingagents/execution-contract/"
_DECIMAL_CONTEXT = Context(prec=76)
_MAX_PROPOSAL_LIFETIME_SECONDS = 3_600
_MAX_INTENT_LIFETIME_SECONDS = 900


class ExecutionEnvironment(StrEnum):
    """Broker environment bound to a logical portfolio account."""

    TESTNET = "testnet"
    LIVE = "live"


class ExecutionContract(BaseModel):
    """Strict, deeply immutable input contract."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    @field_validator("*", mode="before")
    @classmethod
    def reject_nil_uuid(cls, value: Any) -> Any:
        if type(value) is UUID and value.int == 0:
            raise ValueError("authority identifiers must not use the nil UUID")
        return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must use UTC")
    return value.astimezone(UTC)


def _reject_binary_float(value: Any) -> Any:
    if isinstance(value, (bool, float)):
        raise ValueError("decimal values must not be supplied as binary floats or booleans")
    if type(value) is not Decimal:
        raise ValueError("financial values must be exact Decimal instances")
    return value


def _bounded_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("decimal value must be finite")
    _sign, digits, exponent = value.as_tuple()
    if len(digits) > 38 or value.adjusted() >= 38:
        raise ValueError("decimal value exceeds 38 significant integer digits")
    if exponent < -18:
        raise ValueError("decimal value exceeds 18 fractional digits")
    return value


def _decimal_text(value: Decimal) -> str:
    _bounded_decimal(value)
    if value == 0:
        return "0"
    sign, raw_digits, exponent = value.as_tuple()
    digits = list(raw_digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if not digits:
        return "0"
    rendered_digits = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        rendered = rendered_digits + ("0" * exponent)
    else:
        point = len(rendered_digits) + exponent
        rendered = (
            f"{rendered_digits[:point]}.{rendered_digits[point:]}"
            if point > 0
            else f"0.{('0' * -point)}{rendered_digits}"
        )
    return f"-{rendered}" if sign else rendered


def _canonical_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        raise ValueError("canonical payload exceeds the nesting limit")
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (bool, float)):
        raise ValueError("canonical payload cannot contain booleans or binary floats")
    if isinstance(value, int):
        if value.bit_length() > 256:
            raise ValueError("canonical integer exceeds the resource limit")
        return value
    if isinstance(value, str):
        if len(value) > 4_096:
            raise ValueError("canonical string exceeds the resource limit")
        return value
    if isinstance(value, dict):
        if len(value) > 1_000 or any(type(key) is not str for key in value):
            raise ValueError("canonical mappings require bounded string keys")
        return {
            key: _canonical_value(item, depth=depth + 1) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if len(value) > 1_000:
            raise ValueError("canonical sequence exceeds the resource limit")
        return [_canonical_value(item, depth=depth + 1) for item in value]
    if value is None:
        return None
    raise ValueError("canonical payload contains an unsupported scalar type")


def _domain_digest(domain: str, value: BaseModel | dict[str, Any]) -> str:
    payload = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
    canonical = json.dumps(
        _canonical_value(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(_DIGEST_PREFIX)
    digest.update(domain.encode("ascii"))
    digest.update(b"\x00")
    digest.update(canonical)
    return digest.hexdigest()


def _validated_contract_digest(
    domain: str,
    value: ExecutionContract,
    expected_type: type[ExecutionContract],
) -> str:
    """Deeply revalidate an instance before it can become authority bytes.

    Pydantic's ``model_copy(update=...)`` deliberately skips validation.  That
    is useful for internal transformations but must never let a forged frozen
    instance acquire an authoritative digest.
    """

    if type(value) is not expected_type:
        raise ValueError("execution contract type is not exact")
    fields = {
        name: getattr(value, name)
        for name in expected_type.model_fields
    }
    checked = expected_type.model_validate(fields)
    return _domain_digest(domain, checked)


class PortfolioAccountScopeV2(ExecutionContract):
    """Continuous logical account identity, independent of credential rotation."""

    schema_version: Literal[2] = 2
    tenant_id: UUID
    portfolio_id: UUID
    portfolio_account_id: UUID
    external_account_id: UUID
    venue_slug: Venue
    environment: ExecutionEnvironment
    market_scope: Literal["spot"] = "spot"

    @property
    def digest(self) -> str:
        return _validated_contract_digest(
            "portfolio-account-scope/v2", self, PortfolioAccountScopeV2
        )


class CapitalExecutionScopeV2(ExecutionContract):
    """Point-in-time authority scope for one account and credential generation."""

    schema_version: Literal[2] = 2
    account: PortfolioAccountScopeV2
    broker_connection_id: UUID
    venue_capability_digest: Digest
    credential_version: int = Field(ge=1, le=2_147_483_647)
    credential_key_fingerprint: Digest
    mandate_id: UUID
    mandate_version: int = Field(ge=1, le=2_147_483_647)
    mandate_digest: Digest
    risk_policy_digest: Digest
    account_snapshot_digest: Digest

    @field_validator("account", mode="before")
    @classmethod
    def account_is_exact(cls, value: Any) -> PortfolioAccountScopeV2:
        if type(value) is not PortfolioAccountScopeV2:
            raise ValueError("account must be an exact PortfolioAccountScopeV2 instance")
        return value

    @property
    def digest(self) -> str:
        return _validated_contract_digest(
            "capital-execution-scope/v2", self, CapitalExecutionScopeV2
        )


class MandateBindingV2(ExecutionContract):
    """Versioned mandate identity bound to exactly one portfolio."""

    schema_version: Literal[2] = 2
    mandate_id: UUID
    tenant_id: UUID
    portfolio_id: UUID
    version: int = Field(ge=1, le=2_147_483_647)
    profile: RiskProfile
    mode: RunMode
    status: MandateStatus
    policy_digest: Digest

    @property
    def digest(self) -> str:
        return _validated_contract_digest(
            "mandate-binding/v2", self, MandateBindingV2
        )


class TargetAllocationV2(ExecutionContract):
    """One deterministic portfolio target used by a rebalance proposal."""

    portfolio_account_id: UUID
    symbol: Symbol
    quote_currency: Currency
    quote_to_base_rate: Decimal
    target_weight: Decimal
    target_notional_base: Decimal
    max_trade_notional_quote: Decimal
    price_snapshot_digest: Digest

    _decimal_input_is_exact = field_validator(
        "quote_to_base_rate",
        "target_weight",
        "target_notional_base",
        "max_trade_notional_quote",
        mode="before",
    )(_reject_binary_float)

    @field_validator("target_weight")
    @classmethod
    def validate_weight(cls, value: Decimal) -> Decimal:
        _bounded_decimal(value)
        if value < 0 or value > 1:
            raise ValueError("target weight must be between zero and one")
        return value

    @field_validator("target_notional_base", "max_trade_notional_quote")
    @classmethod
    def validate_notional(cls, value: Decimal) -> Decimal:
        _bounded_decimal(value)
        if value < 0:
            raise ValueError("target notional must be non-negative")
        return value

    @model_validator(mode="after")
    def validate_target_shape(self) -> TargetAllocationV2:
        _bounded_decimal(self.quote_to_base_rate)
        if self.quote_to_base_rate <= 0:
            raise ValueError("quote-to-base FX rate must be positive")
        if (self.target_weight == 0) is not (self.target_notional_base == 0):
            raise ValueError("zero target weight and notional must agree")
        if self.max_trade_notional_quote <= 0:
            raise ValueError("maximum trade notional must be positive")
        return self


class RebalanceProposalV2(ExecutionContract):
    """Portfolio-wide proposal that cannot contain cross-portfolio accounts."""

    schema_version: Literal[2] = 2
    proposal_id: UUID
    tenant_id: UUID
    portfolio_id: UUID
    analysis_run_id: UUID
    mandate: MandateBindingV2
    base_currency: Currency
    portfolio_nav_base: Decimal
    maximum_turnover: Decimal
    account_scopes: tuple[PortfolioAccountScopeV2, ...] = Field(min_length=1, max_length=64)
    allocations: tuple[TargetAllocationV2, ...] = Field(min_length=1, max_length=1_000)
    risk_digest: Digest
    snapshot_digest: Digest
    fx_snapshot_digest: Digest
    created_at: datetime
    expires_at: datetime

    _timestamps_are_utc = field_validator("created_at", "expires_at")(_utc)
    _financial_inputs_are_exact = field_validator(
        "portfolio_nav_base", "maximum_turnover", mode="before"
    )(_reject_binary_float)

    @field_validator("portfolio_nav_base")
    @classmethod
    def validate_nav(cls, value: Decimal) -> Decimal:
        _bounded_decimal(value)
        if value <= 0:
            raise ValueError("portfolio NAV must be positive")
        return value

    @field_validator("maximum_turnover")
    @classmethod
    def validate_turnover(cls, value: Decimal) -> Decimal:
        _bounded_decimal(value)
        if value <= 0 or value > 2:
            raise ValueError("maximum turnover must be greater than zero and at most two")
        return value

    @field_validator("mandate", mode="before")
    @classmethod
    def mandate_is_exact(cls, value: Any) -> MandateBindingV2:
        if type(value) is not MandateBindingV2:
            raise ValueError("mandate must be an exact MandateBindingV2 instance")
        return value

    @field_validator("account_scopes", mode="before")
    @classmethod
    def account_scopes_are_exact(cls, value: Any) -> tuple[PortfolioAccountScopeV2, ...]:
        if type(value) is not tuple or any(type(item) is not PortfolioAccountScopeV2 for item in value):
            raise ValueError("account scopes must be exact PortfolioAccountScopeV2 instances")
        return value

    @field_validator("allocations", mode="before")
    @classmethod
    def allocations_are_exact(cls, value: Any) -> tuple[TargetAllocationV2, ...]:
        if type(value) is not tuple or any(type(item) is not TargetAllocationV2 for item in value):
            raise ValueError("allocations must be exact TargetAllocationV2 instances")
        return value

    @model_validator(mode="after")
    def validate_bindings(self) -> RebalanceProposalV2:
        if self.expires_at <= self.created_at:
            raise ValueError("proposal expiry must be after creation")
        if (self.expires_at - self.created_at).total_seconds() > _MAX_PROPOSAL_LIFETIME_SECONDS:
            raise ValueError("proposal lifetime exceeds one hour")
        if (
            self.mandate.tenant_id != self.tenant_id
            or self.mandate.portfolio_id != self.portfolio_id
        ):
            raise ValueError("mandate must belong to the proposal tenant and portfolio")
        for account in self.account_scopes:
            if account.tenant_id != self.tenant_id or account.portfolio_id != self.portfolio_id:
                raise ValueError("account scope must belong to the proposal tenant and portfolio")
        account_ids = tuple(account.portfolio_account_id for account in self.account_scopes)
        if len(set(account_ids)) != len(account_ids):
            raise ValueError("proposal account scopes must be unique")
        if account_ids != tuple(sorted(account_ids, key=str)):
            raise ValueError("proposal account scopes must be in canonical account order")
        external_account_ids = tuple(
            account.external_account_id for account in self.account_scopes
        )
        if len(set(external_account_ids)) != len(external_account_ids):
            raise ValueError("proposal external accounts must be unique")
        authorized_accounts = set(account_ids)
        allocation_keys = tuple(
            (item.portfolio_account_id, item.symbol) for item in self.allocations
        )
        if any(account_id not in authorized_accounts for account_id, _ in allocation_keys):
            raise ValueError("allocation account is not authorized by the proposal")
        if len(set(allocation_keys)) != len(allocation_keys):
            raise ValueError("proposal allocations must have unique account-symbol pairs")
        if allocation_keys != tuple(sorted(allocation_keys, key=lambda item: (str(item[0]), item[1]))):
            raise ValueError("proposal allocations must be in canonical account-symbol order")
        with localcontext(_DECIMAL_CONTEXT):
            total_weight = sum(
                (item.target_weight for item in self.allocations), Decimal("0")
            )
            authorized_turnover_base = sum(
                (
                    item.max_trade_notional_quote * item.quote_to_base_rate
                    for item in self.allocations
                ),
                Decimal("0"),
            )
        if total_weight > 1:
            raise ValueError("target weights cannot exceed one")
        for item in self.allocations:
            with localcontext(_DECIMAL_CONTEXT):
                expected_target = self.portfolio_nav_base * item.target_weight
            if item.target_notional_base != expected_target:
                raise ValueError("target notional must equal NAV times target weight")
        with localcontext(_DECIMAL_CONTEXT):
            turnover_limit = self.portfolio_nav_base * self.maximum_turnover
        if authorized_turnover_base > turnover_limit:
            raise ValueError("authorized trade notional exceeds portfolio turnover")
        return self

    @property
    def digest(self) -> str:
        return _validated_contract_digest(
            "rebalance-proposal/v2", self, RebalanceProposalV2
        )


class ExecutionIntentV2(ExecutionContract):
    """One child order fully bound to proposal, account, credential, and risk state."""

    schema_version: Literal[2] = 2
    intent_id: UUID
    child_order_id: UUID
    tranche_index: int = Field(strict=True, ge=1, le=1_000)
    proposal: RebalanceProposalV2
    scope: CapitalExecutionScopeV2
    symbol: Symbol
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal
    max_slippage_bps: int = Field(ge=0, le=500)
    sizing_decision_digest: Digest
    risk_reservation_id: UUID
    risk_reservation_digest: Digest
    created_at: datetime
    expires_at: datetime

    _timestamps_are_utc = field_validator("created_at", "expires_at")(_utc)
    _decimal_input_is_exact = field_validator(
        "quantity", "limit_price", mode="before"
    )(_reject_binary_float)

    @field_validator("proposal", mode="before")
    @classmethod
    def proposal_is_exact(cls, value: Any) -> RebalanceProposalV2:
        if type(value) is not RebalanceProposalV2:
            raise ValueError("proposal must be an exact RebalanceProposalV2 instance")
        return value

    @field_validator("scope", mode="before")
    @classmethod
    def scope_is_exact(cls, value: Any) -> CapitalExecutionScopeV2:
        if type(value) is not CapitalExecutionScopeV2:
            raise ValueError("scope must be an exact CapitalExecutionScopeV2 instance")
        return value

    @field_validator("quantity", "limit_price")
    @classmethod
    def validate_positive_value(cls, value: Decimal) -> Decimal:
        _bounded_decimal(value)
        if value <= 0:
            raise ValueError("order values must be positive")
        return value

    @model_validator(mode="after")
    def validate_bindings(self) -> ExecutionIntentV2:
        if self.expires_at <= self.created_at:
            raise ValueError("intent expiry must be after creation")
        if (self.expires_at - self.created_at).total_seconds() > _MAX_INTENT_LIFETIME_SECONDS:
            raise ValueError("intent lifetime exceeds fifteen minutes")
        if self.created_at < self.proposal.created_at or self.expires_at > self.proposal.expires_at:
            raise ValueError("intent lifetime must be contained by proposal lifetime")
        if self.proposal.mandate.status is not MandateStatus.ACTIVE:
            raise ValueError("execution intent requires an active mandate")
        account = self.scope.account
        if (
            account.tenant_id != self.proposal.tenant_id
            or account.portfolio_id != self.proposal.portfolio_id
        ):
            raise ValueError("capital scope must belong to the proposal tenant and portfolio")
        if account not in self.proposal.account_scopes:
            raise ValueError("capital scope account is not authorized by the proposal")
        if (
            self.scope.mandate_id != self.proposal.mandate.mandate_id
            or self.scope.mandate_version != self.proposal.mandate.version
            or self.scope.mandate_digest != self.proposal.mandate.digest
        ):
            raise ValueError("capital scope mandate digest does not match the proposal")
        if self.scope.risk_policy_digest != self.proposal.risk_digest:
            raise ValueError("capital scope risk digest does not match the proposal")
        if self.scope.account_snapshot_digest != self.proposal.snapshot_digest:
            raise ValueError("capital scope snapshot digest does not match the proposal")
        if (
            account.environment is ExecutionEnvironment.TESTNET
            and self.proposal.mandate.mode is not RunMode.PAPER
        ) or (
            account.environment is ExecutionEnvironment.LIVE
            and self.proposal.mandate.mode
            not in {RunMode.LIVE_SUPERVISED, RunMode.LIVE_AUTOMATED}
        ):
            raise ValueError("broker environment does not match the mandate execution mode")
        allocations = tuple(
            allocation
            for allocation in self.proposal.allocations
            if allocation.portfolio_account_id == account.portfolio_account_id
            and allocation.symbol == self.symbol
        )
        if len(allocations) != 1:
            raise ValueError("intent account and symbol are not present exactly once")
        allocation = allocations[0]
        with localcontext(_DECIMAL_CONTEXT):
            slippage_multiplier = Decimal(1) + (
                Decimal(self.max_slippage_bps) / Decimal(10_000)
            )
            worst_case_notional = self.quantity * self.limit_price * slippage_multiplier
        _bounded_decimal(worst_case_notional)
        if worst_case_notional > allocation.max_trade_notional_quote:
            raise ValueError("intent exceeds the authorized trade notional")
        return self

    @property
    def digest(self) -> str:
        return _validated_contract_digest(
            "execution-intent/v2", self, ExecutionIntentV2
        )

    @property
    def client_order_seed(self) -> str:
        checked = ExecutionIntentV2.model_validate(
            {name: getattr(self, name) for name in type(self).model_fields}
        )
        return _domain_digest(
            "client-order-seed/v2",
            {
                "schema_version": checked.schema_version,
                "tenant_id": checked.scope.account.tenant_id,
                "portfolio_id": checked.scope.account.portfolio_id,
                "portfolio_account_id": checked.scope.account.portfolio_account_id,
                "external_account_id": checked.scope.account.external_account_id,
                "proposal_id": checked.proposal.proposal_id,
                "child_order_id": checked.child_order_id,
                "tranche_index": checked.tranche_index,
                "symbol": checked.symbol,
                "side": checked.side,
                "order_type": checked.order_type,
                "quantity": checked.quantity,
                "limit_price": checked.limit_price,
                "max_slippage_bps": checked.max_slippage_bps,
                "sizing_decision_digest": checked.sizing_decision_digest,
                "risk_reservation_id": checked.risk_reservation_id,
                "risk_reservation_digest": checked.risk_reservation_digest,
            },
        )


__all__ = [
    "CapitalExecutionScopeV2",
    "ExecutionEnvironment",
    "ExecutionIntentV2",
    "MandateBindingV2",
    "PortfolioAccountScopeV2",
    "RebalanceProposalV2",
    "TargetAllocationV2",
]
