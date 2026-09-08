"""Immutable contracts at the analysis-to-capital boundary.

These models deliberately contain no broker calls.  They define the data that
later application services must persist and bind before an execution adapter is
allowed to see an order.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RiskProfile(StrEnum):
    PRESERVATION = "preservation"
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    GROWTH = "growth"
    AGGRESSIVE = "aggressive"
    CUSTOM = "custom"


class RunMode(StrEnum):
    BACKTEST = "backtest"
    SHADOW = "shadow"
    PAPER = "paper"
    LIVE_SUPERVISED = "live_supervised"
    LIVE_AUTOMATED = "live_automated"


class SuitabilityStatus(StrEnum):
    DRAFT = "draft"
    VALID = "valid"
    EXPIRED = "expired"
    REVOKED = "revoked"


class MandateStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    CIRCUIT_BROKEN = "circuit_broken"
    CLOSED = "closed"


class SignalAction(StrEnum):
    STRONG_SELL = "strong_sell"
    SELL = "sell"
    HOLD = "hold"
    BUY = "buy"
    STRONG_BUY = "strong_buy"
    REVIEW = "review"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"


class OrderState(StrEnum):
    CREATED = "created"
    APPROVED = "approved"
    SUBMITTING = "submitting"
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


class TenantActor(FrozenContract):
    tenant_id: UUID
    user_id: UUID


class SuitabilityAssessment(FrozenContract):
    assessment_id: UUID
    tenant_id: UUID
    user_id: UUID
    requested_profile: RiskProfile
    maximum_profile: RiskProfile
    status: SuitabilityStatus
    assessed_at: datetime
    expires_at: datetime
    policy_version: int = Field(ge=1)
    digest: Digest

    _timestamps_are_aware = field_validator("assessed_at", "expires_at")(_aware)

    @model_validator(mode="after")
    def validate_window(self):
        if self.expires_at <= self.assessed_at:
            raise ValueError("suitability expiry must be after assessment")
        return self


class MandateBinding(FrozenContract):
    mandate_id: UUID
    tenant_id: UUID
    version: int = Field(ge=1)
    profile: RiskProfile
    mode: RunMode
    status: MandateStatus
    digest: Digest


class Signal(FrozenContract):
    signal_id: UUID
    tenant_id: UUID
    analysis_run_id: UUID
    symbol: str = Field(min_length=1, max_length=64)
    action: SignalAction
    conviction: Decimal
    as_of: datetime
    evidence_digest: Digest
    digest: Digest

    _as_of_is_aware = field_validator("as_of")(_aware)

    @field_validator("conviction")
    @classmethod
    def validate_conviction(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0 or value > 1:
            raise ValueError("conviction must be finite and between zero and one")
        return value


class PortfolioSnapshotRef(FrozenContract):
    snapshot_id: UUID
    tenant_id: UUID
    portfolio_id: UUID
    captured_at: datetime
    digest: Digest

    _captured_at_is_aware = field_validator("captured_at")(_aware)


class TargetAllocation(FrozenContract):
    symbol: str = Field(min_length=1, max_length=64)
    target_weight: Decimal
    target_notional: Decimal

    @field_validator("target_weight")
    @classmethod
    def validate_weight(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0 or value > 1:
            raise ValueError("target weight must be finite and between zero and one")
        return value

    @field_validator("target_notional")
    @classmethod
    def validate_notional(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("target notional must be finite and non-negative")
        return value


class RebalanceProposal(FrozenContract):
    proposal_id: UUID
    tenant_id: UUID
    mandate: MandateBinding
    snapshot: PortfolioSnapshotRef
    allocations: tuple[TargetAllocation, ...] = Field(min_length=1)
    created_at: datetime
    expires_at: datetime
    digest: Digest

    _proposal_timestamps_are_aware = field_validator("created_at", "expires_at")(_aware)

    @model_validator(mode="after")
    def validate_binding_and_window(self):
        if self.mandate.tenant_id != self.tenant_id or self.snapshot.tenant_id != self.tenant_id:
            raise ValueError("proposal references must belong to the same tenant")
        if self.expires_at <= self.created_at:
            raise ValueError("proposal expiry must be after creation")
        if sum((item.target_weight for item in self.allocations), Decimal(0)) > 1:
            raise ValueError("target weights cannot exceed one")
        return self


class Approval(FrozenContract):
    approval_id: UUID
    tenant_id: UUID
    approver_user_id: UUID
    intent_digest: Digest
    mfa_verified: bool
    approved_at: datetime
    expires_at: datetime

    _approval_timestamps_are_aware = field_validator("approved_at", "expires_at")(_aware)

    @model_validator(mode="after")
    def validate_window(self):
        if self.expires_at <= self.approved_at:
            raise ValueError("approval expiry must be after approval")
        return self


class ExecutionIntent(FrozenContract):
    intent_id: UUID
    tenant_id: UUID
    proposal_id: UUID
    analysis_run_id: UUID
    mandate_id: UUID
    mandate_version: int = Field(ge=1)
    mandate_digest: Digest
    policy_digest: Digest
    signal_digest: Digest
    snapshot_digest: Digest
    connection_id: UUID
    credential_version: int = Field(ge=1)
    exchange: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")
    mode: RunMode
    symbol: str = Field(min_length=1, max_length=64)
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal
    max_slippage_bps: int = Field(ge=0, le=500)
    created_at: datetime
    expires_at: datetime
    idempotency_key: str = Field(min_length=16, max_length=200)
    digest: Digest | None = None

    _intent_timestamps_are_aware = field_validator("created_at", "expires_at")(_aware)

    @field_validator("quantity", "limit_price")
    @classmethod
    def validate_positive_money(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("financial value must be finite and positive")
        return value

    @model_validator(mode="after")
    def validate_and_bind(self):
        if self.expires_at <= self.created_at:
            raise ValueError("intent expiry must be after creation")
        computed = self._computed_digest()
        if self.digest is None:
            object.__setattr__(self, "digest", computed)
        elif self.digest != computed:
            raise ValueError("execution intent digest mismatch")
        return self

    @classmethod
    def create(cls, **data) -> ExecutionIntent:
        data.pop("digest", None)
        return cls(**data)

    def _computed_digest(self) -> str:
        payload = self.model_dump(mode="json", exclude={"digest"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def verify_digest(self) -> bool:
        return self.digest is not None and self.digest == self._computed_digest()

    def is_approved_by(self, approval: Approval, *, at: datetime) -> bool:
        _aware(at)
        if self.digest is None or at >= self.expires_at or at >= approval.expires_at:
            return False
        if approval.approved_at > at:
            return False
        if approval.tenant_id != self.tenant_id or approval.intent_digest != self.digest:
            return False
        if self.mode in {RunMode.LIVE_SUPERVISED, RunMode.LIVE_AUTOMATED}:
            return approval.mfa_verified
        return True
