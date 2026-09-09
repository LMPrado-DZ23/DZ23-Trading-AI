"""Transactional application service for deterministic portfolio risk controls."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import struct
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.platform.contracts import RiskProfile, RunMode
from tradingagents.platform.identity import IdentityPrincipal
from tradingagents.platform.portfolio.models import (
    CashReservation,
    CircuitBreaker,
    LedgerPosting,
    LedgerTransaction,
    Portfolio,
    PortfolioMandate,
    PortfolioPosition,
    PortfolioSnapshot,
    SuitabilityRecord,
)
from tradingagents.platform.portfolio.policies import (
    POLICY_VERSION,
    SizingInput,
    SizingResult,
    cap_incremental_target_weights,
    policy_for,
    size_position,
)

_PROFILE_ORDER = {
    RiskProfile.PRESERVATION: 0,
    RiskProfile.CONSERVATIVE: 1,
    RiskProfile.MODERATE: 2,
    RiskProfile.GROWTH: 3,
    RiskProfile.AGGRESSIVE: 4,
}
_ZERO = Decimal("0")
_CAPABILITY_KID = "portfolio-capability-v1"
_CAPABILITY_TTL_SECONDS = 20


class PortfolioError(RuntimeError):
    """Stable, non-sensitive portfolio error code."""


class PortfolioRequiredAction(StrEnum):
    COMPLETE_SUITABILITY_AND_CREATE_MANDATE = "complete_suitability_and_create_mandate"
    RESUME_MANDATE = "resume_mandate"
    CREATE_COMPATIBLE_MANDATE = "create_compatible_mandate"
    CREATE_NEW_PORTFOLIO = "create_new_portfolio"
    REVIEW_RISK_HOLD = "review_risk_hold"


@dataclass(frozen=True, slots=True)
class PortfolioOverview:
    id: UUID
    name: str
    base_currency: str
    status: str
    mandate_profile: str | None
    suitability_effective_profile: str | None
    mode: str | None
    cash: Decimal
    reserved_cash: Decimal
    available_cash: Decimal
    current_mandate_id: UUID | None
    mandate_status: str | None
    pause_reason: str | None
    required_action: PortfolioRequiredAction | None
    circuit_breaker_open: bool


@dataclass(frozen=True, slots=True)
class PausedPortfolioNotice:
    portfolio_id: UUID
    name: str
    mandate_id: UUID
    profile: str
    mode: str
    pause_reason: str
    required_action: PortfolioRequiredAction


@dataclass(frozen=True, slots=True)
class SuitabilityAssessmentResult:
    id: UUID
    version: int
    requested_profile: str
    maximum_profile: str
    effective_profile: str
    status: str
    policy_version: int
    digest: str
    assessed_at: datetime
    expires_at: datetime
    paused_portfolios: tuple[PausedPortfolioNotice, ...]


@dataclass(frozen=True, slots=True)
class PortfolioSnapshotOverview:
    portfolio_id: UUID
    sequence: int
    cash: Decimal
    reserved_cash: Decimal
    market_value: Decimal
    equity: Decimal
    nav_per_unit: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    drawdown: Decimal
    daily_drawdown: Decimal
    unit_count: Decimal
    high_water_nav: Decimal
    valuation_source: str
    valuation_observed_at: datetime
    valuation_available_at: datetime
    captured_at: datetime
    freshness: str


@dataclass(frozen=True, slots=True)
class TrustedValuation:
    """Internal valuation observation with point-in-time provenance.

    Public API clients cannot create snapshots.  A market-data or broker
    reconciliation adapter must construct this value after validating the
    upstream observation and recording its evidence digest.
    """

    prices: Mapping[str, Decimal]
    source: str
    observed_at: datetime
    available_at: datetime
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class TrustedAssetAllocation:
    """Server-side market/risk inputs for one proposed spot allocation."""

    symbol: str
    asset_class: str
    price: Decimal
    conviction: Decimal
    realized_annual_volatility: Decimal
    average_daily_notional: Decimal
    trading_cost_bps: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class TrustedAllocationRequest:
    """Internal PIT allocation proposal; never accepted from the public API."""

    raw_target_weights: Mapping[str, Decimal]
    correlations: Mapping[tuple[str, str], Decimal]
    assets: Mapping[str, TrustedAssetAllocation]
    source: str
    observed_at: datetime
    available_at: datetime
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class AllocationLine:
    symbol: str
    asset_class: str
    capped_weight: Decimal
    sizing: SizingResult


@dataclass(frozen=True, slots=True)
class AllocationReservation:
    portfolio_id: UUID
    mandate_id: UUID
    policy_digest: str
    allocation_digest: str
    evidence_digest: str
    total_order_notional: Decimal
    lines: tuple[AllocationLine, ...]
    reservation: CashReservation


class PortfolioService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        idempotency_digest_key: bytes,
        capability_signing_key: bytes | None = None,
    ) -> None:
        if len(idempotency_digest_key) < 32:
            raise ValueError("portfolio idempotency key must contain at least 32 bytes")
        self._sessions = session_factory
        self._digest_key = idempotency_digest_key
        if capability_signing_key is not None and len(capability_signing_key) < 32:
            raise ValueError("portfolio capability key must contain at least 32 bytes")
        self._capability_key = capability_signing_key
        self._reservation_lock = threading.Lock()
        self._suitability_lock = threading.Lock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _set_tenant_scope(db: Session, tenant_id: UUID) -> None:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )

    @staticmethod
    def _capability_message(*fields: str) -> bytes:
        message = bytearray(b"TA-CAP-V1\0")
        for field in fields:
            encoded = field.encode("utf-8")
            if len(encoded) > 4096:
                raise PortfolioError("PORTFOLIO_CAPABILITY_INVALID")
            message.extend(struct.pack(">I", len(encoded)))
            message.extend(encoded)
        return bytes(message)

    def _mint_capability(
        self,
        *,
        principal: IdentityPrincipal,
        audience: str,
        fields: tuple[str, ...],
        now: datetime,
    ) -> dict[str, Any]:
        if self._capability_key is None or not re.fullmatch(
            r"[0-9a-f]{64}", principal.session_token_hash
        ):
            raise PortfolioError("PORTFOLIO_CAPABILITY_UNAVAILABLE")
        issued_at = int(now.timestamp())
        expires_at = issued_at + _CAPABILITY_TTL_SECONDS
        nonce = secrets.token_hex(32)
        common = (
            audience,
            str(principal.session_id),
            principal.session_token_hash,
            *fields,
            str(issued_at),
            str(expires_at),
            nonce,
        )
        message = self._capability_message(*common)
        mac = hmac.new(self._capability_key, message, hashlib.sha256).hexdigest()
        return {
            "cap_kid": _CAPABILITY_KID,
            "cap_session_id": str(principal.session_id),
            "cap_token_hash": principal.session_token_hash,
            "cap_issued_at": issued_at,
            "cap_expires_at": expires_at,
            "cap_nonce": nonce,
            "cap_mac": mac,
        }

    def _set_portfolio_scope(
        self, db: Session, principal: IdentityPrincipal
    ) -> None:
        PortfolioService._set_tenant_scope(db, principal.tenant_id)
        if db.get_bind().dialect.name != "postgresql":
            return
        backend_pid, transaction_id, database_now = db.execute(
            text("SELECT pg_backend_pid(), txid_current(), clock_timestamp()")
        ).one()
        capability = self._mint_capability(
            principal=principal,
            audience="portfolio.session.v1",
            fields=(str(backend_pid), str(transaction_id)),
            now=database_now,
        )
        db.execute(
            text(
                "SELECT set_config('app.portfolio_session_id', :cap_session_id, true), "
                "set_config('app.portfolio_token_hash', :cap_token_hash, true), "
                "set_config('app.portfolio_context_kid', :cap_kid, true), "
                "set_config('app.portfolio_context_issued_at', CAST(:cap_issued_at AS text), true), "
                "set_config('app.portfolio_context_expires_at', CAST(:cap_expires_at AS text), true), "
                "set_config('app.portfolio_context_nonce', :cap_nonce, true), "
                "set_config('app.portfolio_context_mac', :cap_mac, true), "
                "set_config('app.portfolio_context_backend_pid', CAST(:backend_pid AS text), true), "
                "set_config('app.portfolio_context_txid', CAST(:transaction_id AS text), true)"
            ),
            capability
            | {"backend_pid": backend_pid, "transaction_id": transaction_id},
        )

    @staticmethod
    def _authorize(principal: IdentityPrincipal, *, write: bool = False) -> None:
        allowed = {"owner", "admin", "operator"}
        if not write:
            allowed.update({"auditor", "viewer"})
        if principal.role.value not in allowed:
            raise PortfolioError("PORTFOLIO_FORBIDDEN")

    @staticmethod
    def _canonical_digest(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _key_hash(self, value: str) -> str:
        if not 16 <= len(value) <= 128 or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise PortfolioError("IDEMPOTENCY_KEY_INVALID")
        return hmac.new(self._digest_key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _decimal(name: str, value: Decimal, *, positive: bool = False) -> Decimal:
        if type(value) is not Decimal or not value.is_finite():
            raise PortfolioError(f"{name.upper()}_INVALID")
        if value < 0 or (positive and value <= 0):
            raise PortfolioError(f"{name.upper()}_INVALID")
        return value

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return "0" if rendered in {"", "-0"} else rendered

    @staticmethod
    def _ttl_microseconds(ttl: timedelta) -> int:
        return (
            ttl.days * 86_400_000_000
            + ttl.seconds * 1_000_000
            + ttl.microseconds
        )

    def assess_suitability(
        self,
        *,
        principal: IdentityPrincipal,
        requested_profile: RiskProfile,
        answers: dict[str, Any],
        valid_for: timedelta = timedelta(days=365),
    ) -> SuitabilityAssessmentResult:
        with self._suitability_lock:
            return self._assess_suitability_locked(
                principal=principal,
                requested_profile=requested_profile,
                answers=answers,
                valid_for=valid_for,
            )

    def _assess_suitability_locked(
        self,
        *,
        principal: IdentityPrincipal,
        requested_profile: RiskProfile,
        answers: dict[str, Any],
        valid_for: timedelta = timedelta(days=365),
    ) -> SuitabilityAssessmentResult:
        self._authorize(principal, write=True)
        if requested_profile is RiskProfile.CUSTOM:
            raise PortfolioError("CUSTOM_PROFILE_REQUIRES_REVIEW")
        if not timedelta(days=1) <= valid_for <= timedelta(days=730):
            raise PortfolioError("SUITABILITY_VALIDITY_INVALID")
        maximum = self._maximum_profile(answers)
        effective = min(
            requested_profile,
            maximum,
            key=lambda profile: _PROFILE_ORDER[profile],
        )
        with self._sessions() as db, db.begin():
            self._set_portfolio_scope(db, principal)
            now = self._database_now(db)
            payload = {
                "tenant_id": str(principal.tenant_id),
                "user_id": str(principal.user_id),
                "requested_profile": requested_profile.value,
                "maximum_profile": maximum.value,
                "effective_profile": effective.value,
                "policy_version": POLICY_VERSION,
                "answers": answers,
                "assessed_at": now.isoformat(),
                "expires_at": (now + valid_for).isoformat(),
            }
            self._lock_serialization_key(
                db, f"suitability:{principal.tenant_id}:{principal.user_id}"
            )
            current = db.scalar(
                select(func.max(SuitabilityRecord.version)).where(
                    SuitabilityRecord.tenant_id == principal.tenant_id,
                    SuitabilityRecord.user_id == principal.user_id,
                )
            )
            row = SuitabilityRecord(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                version=(current or 0) + 1,
                requested_profile=requested_profile.value,
                maximum_profile=maximum.value,
                effective_profile=effective.value,
                status="valid",
                policy_version=POLICY_VERSION,
                answers=answers,
                digest=self._canonical_digest(payload),
                assessed_at=now,
                expires_at=now + valid_for,
            )
            db.add(row)
            db.flush()
            owner_portfolios = db.scalars(
                select(Portfolio)
                .where(
                    Portfolio.tenant_id == principal.tenant_id,
                    Portfolio.owner_user_id == principal.user_id,
                )
                .with_for_update()
            ).all()
            paused_notices: list[PausedPortfolioNotice] = []
            for portfolio in owner_portfolios:
                current_mandate = self._current_mandate(
                    db, principal.tenant_id, portfolio.id
                )
                if current_mandate is None or current_mandate.status != "active":
                    continue
                mandate_payload = {
                    "previous_digest": current_mandate.digest,
                    "suitability_digest": row.digest,
                    "version": current_mandate.version + 1,
                    "status": "paused",
                    "reason": "suitability_reassessment",
                    "effective_at": now.isoformat(),
                }
                paused = PortfolioMandate(
                    tenant_id=principal.tenant_id,
                    portfolio_id=portfolio.id,
                    suitability_id=row.id,
                    version=current_mandate.version + 1,
                    profile=current_mandate.profile,
                    mode=current_mandate.mode,
                    status="paused",
                    pause_reason="suitability_reassessment",
                    policy_version=current_mandate.policy_version,
                    policy_digest=current_mandate.policy_digest,
                    digest=self._canonical_digest(mandate_payload),
                    limits=current_mandate.limits,
                    created_by_user_id=principal.user_id,
                    effective_at=now,
                )
                db.add(paused)
                db.flush()
                portfolio.current_mandate_id = paused.id
                required_action = (
                    PortfolioRequiredAction.RESUME_MANDATE
                    if _PROFILE_ORDER[RiskProfile(current_mandate.profile)]
                    <= _PROFILE_ORDER[effective]
                    else PortfolioRequiredAction.CREATE_COMPATIBLE_MANDATE
                )
                paused_notices.append(
                    PausedPortfolioNotice(
                        portfolio_id=portfolio.id,
                        name=portfolio.name,
                        mandate_id=paused.id,
                        profile=paused.profile,
                        mode=paused.mode,
                        pause_reason=paused.pause_reason,
                        required_action=required_action,
                    )
                )
            return SuitabilityAssessmentResult(
                id=row.id,
                version=row.version,
                requested_profile=row.requested_profile,
                maximum_profile=row.maximum_profile,
                effective_profile=row.effective_profile,
                status=row.status,
                policy_version=row.policy_version,
                digest=row.digest,
                assessed_at=row.assessed_at,
                expires_at=row.expires_at,
                paused_portfolios=tuple(paused_notices),
            )

    @staticmethod
    def _maximum_profile(answers: dict[str, Any]) -> RiskProfile:
        required = {
            "horizon_years": int,
            "maximum_loss_percent": int,
            "emergency_fund_months": int,
            "income_stability": str,
            "experience": str,
        }
        if set(answers) != set(required):
            raise PortfolioError("SUITABILITY_ANSWERS_INVALID")
        for name, expected in required.items():
            if type(answers[name]) is not expected:
                raise PortfolioError("SUITABILITY_ANSWERS_INVALID")
        horizon = answers["horizon_years"]
        loss = answers["maximum_loss_percent"]
        emergency = answers["emergency_fund_months"]
        income = answers["income_stability"]
        experience = answers["experience"]
        if not 0 <= horizon <= 80 or not 0 <= loss <= 100 or not 0 <= emergency <= 120:
            raise PortfolioError("SUITABILITY_ANSWERS_INVALID")
        if income not in {"unstable", "variable", "stable"} or experience not in {
            "none",
            "basic",
            "experienced",
        }:
            raise PortfolioError("SUITABILITY_ANSWERS_INVALID")
        if emergency < 3 or horizon < 1 or loss < 5:
            return RiskProfile.PRESERVATION
        if emergency < 6 or income == "unstable" or horizon < 2 or loss < 10:
            return RiskProfile.CONSERVATIVE
        if experience == "none" or horizon < 4 or loss < 20:
            return RiskProfile.MODERATE
        if experience == "basic" or horizon < 7 or loss < 35:
            return RiskProfile.GROWTH
        return RiskProfile.AGGRESSIVE

    @staticmethod
    def _lock_serialization_key(db: Session, value: str) -> None:
        if db.get_bind().dialect.name == "postgresql":
            key = int.from_bytes(
                hashlib.sha256(value.encode("utf-8")).digest()[:8],
                "big",
                signed=True,
            )
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    def _database_now(self, db: Session) -> datetime:
        if db.get_bind().dialect.name == "postgresql":
            value = db.scalar(select(func.clock_timestamp()))
            if isinstance(value, datetime):
                return value.astimezone(UTC)
            raise PortfolioError("DATABASE_TIME_UNAVAILABLE")
        return self._now()

    @staticmethod
    def _latest_suitability(
        db: Session, tenant_id: UUID, user_id: UUID
    ) -> SuitabilityRecord | None:
        return db.scalar(
            select(SuitabilityRecord)
            .where(
                SuitabilityRecord.tenant_id == tenant_id,
                SuitabilityRecord.user_id == user_id,
            )
            .order_by(SuitabilityRecord.version.desc())
            .limit(1)
        )

    def create_portfolio(
        self,
        *,
        principal: IdentityPrincipal,
        name: str,
        base_currency: str,
    ) -> Portfolio:
        self._authorize(principal, write=True)
        clean_name = name.strip()
        currency = base_currency.strip().upper()
        if (
            not 2 <= len(clean_name) <= 120
            or not 3 <= len(currency) <= 12
            or not currency.isalpha()
        ):
            raise PortfolioError("PORTFOLIO_INPUT_INVALID")
        try:
            with self._sessions() as db, db.begin():
                self._set_portfolio_scope(db, principal)
                row = Portfolio(
                    tenant_id=principal.tenant_id,
                    owner_user_id=principal.user_id,
                    name=clean_name,
                    base_currency=currency,
                    status="active",
                )
                db.add(row)
                db.flush()
                db.expunge(row)
                return row
        except IntegrityError:
            raise PortfolioError("PORTFOLIO_ALREADY_EXISTS") from None

    def activate_mandate(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        suitability_id: UUID,
        profile: RiskProfile,
        mode: RunMode,
    ) -> PortfolioMandate:
        self._authorize(principal, write=True)
        if profile is RiskProfile.CUSTOM:
            raise PortfolioError("CUSTOM_PROFILE_REQUIRES_REVIEW")
        if mode not in {RunMode.BACKTEST, RunMode.SHADOW, RunMode.PAPER}:
            raise PortfolioError("CAPITAL_MODE_NOT_ENABLED")
        with self._sessions() as db, db.begin():
            self._set_portfolio_scope(db, principal)
            now = self._database_now(db)
            owner_user_id = db.scalar(
                select(Portfolio.owner_user_id).where(
                    Portfolio.id == portfolio_id,
                    Portfolio.tenant_id == principal.tenant_id,
                )
            )
            if owner_user_id is None:
                raise PortfolioError("PORTFOLIO_NOT_FOUND")
            self._lock_serialization_key(
                db, f"suitability:{principal.tenant_id}:{owner_user_id}"
            )
            portfolio = db.scalar(
                select(Portfolio)
                .where(Portfolio.id == portfolio_id, Portfolio.tenant_id == principal.tenant_id)
                .with_for_update()
            )
            if portfolio is None or portfolio.owner_user_id != owner_user_id:
                raise PortfolioError("PORTFOLIO_NOT_FOUND")
            suitability = self._latest_suitability(
                db, principal.tenant_id, owner_user_id
            )
            if suitability is not None and suitability.id != suitability_id:
                raise PortfolioError("SUITABILITY_STALE")
            if (
                suitability is None
                or suitability.status != "valid"
                or suitability.expires_at <= now
            ):
                raise PortfolioError("SUITABILITY_REQUIRED")
            if _PROFILE_ORDER[profile] > _PROFILE_ORDER[RiskProfile(suitability.effective_profile)]:
                raise PortfolioError("PROFILE_EXCEEDS_SUITABILITY")
            policy = policy_for(profile)
            version = (
                db.scalar(
                    select(func.max(PortfolioMandate.version)).where(
                        PortfolioMandate.tenant_id == principal.tenant_id,
                        PortfolioMandate.portfolio_id == portfolio.id,
                    )
                )
                or 0
            ) + 1
            limits = {key: str(value) for key, value in asdict(policy).items() if key != "profile"}
            payload = {
                "tenant_id": str(principal.tenant_id),
                "portfolio_id": str(portfolio.id),
                "suitability_digest": suitability.digest,
                "version": version,
                "profile": profile.value,
                "mode": mode.value,
                "policy_version": POLICY_VERSION,
                "policy_digest": policy.digest,
                "limits": limits,
                "effective_at": now.isoformat(),
            }
            row = PortfolioMandate(
                tenant_id=principal.tenant_id,
                portfolio_id=portfolio.id,
                suitability_id=suitability.id,
                version=version,
                profile=profile.value,
                mode=mode.value,
                status="active",
                pause_reason=None,
                policy_version=POLICY_VERSION,
                policy_digest=policy.digest,
                digest=self._canonical_digest(payload),
                limits=limits,
                created_by_user_id=principal.user_id,
                effective_at=now,
            )
            db.add(row)
            db.flush()
            portfolio.current_mandate_id = row.id
            db.flush()
            db.expunge(row)
            return row

    def record_funding(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        amount: Decimal,
        idempotency_key: str,
        reference_id: str,
        mode: RunMode = RunMode.PAPER,
    ) -> LedgerTransaction:
        self._authorize(principal, write=True)
        self._decimal("amount", amount, positive=True)
        clean_reference = reference_id.strip()
        if not clean_reference or len(clean_reference) > 128:
            raise PortfolioError("FUNDING_REFERENCE_INVALID")
        if mode not in {RunMode.SHADOW, RunMode.PAPER}:
            raise PortfolioError("CAPITAL_MODE_NOT_ENABLED")
        return self._record_cash_transaction(
            principal=principal,
            portfolio_id=portfolio_id,
            amount=amount,
            idempotency_key=idempotency_key,
            event_type=f"{mode.value}_funding",
            reference_type=f"user_declared_{mode.value}",
            reference_id=clean_reference,
            cash_direction=1,
            provenance="user_declared",
            verification_status="unverified",
            affects_units=True,
            required_mode=mode,
        )

    def transition_mandate(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        target_status: str,
    ) -> PortfolioMandate:
        """Append a mandate lifecycle version; historical rows are never mutated."""

        self._authorize(principal, write=True)
        if target_status not in {"active", "paused", "closed"}:
            raise PortfolioError("MANDATE_TRANSITION_INVALID")
        with self._sessions() as db, db.begin():
            self._set_portfolio_scope(db, principal)
            now = self._database_now(db)
            owner_user_id: UUID | None = None
            if target_status == "active":
                owner_user_id = db.scalar(
                    select(Portfolio.owner_user_id).where(
                        Portfolio.id == portfolio_id,
                        Portfolio.tenant_id == principal.tenant_id,
                    )
                )
                if owner_user_id is None:
                    raise PortfolioError("PORTFOLIO_NOT_FOUND")
                self._lock_serialization_key(
                    db, f"suitability:{principal.tenant_id}:{owner_user_id}"
                )
            portfolio = db.scalar(
                select(Portfolio)
                .where(
                    Portfolio.id == portfolio_id,
                    Portfolio.tenant_id == principal.tenant_id,
                )
                .with_for_update()
            )
            if portfolio is None or (
                owner_user_id is not None and portfolio.owner_user_id != owner_user_id
            ):
                raise PortfolioError("PORTFOLIO_NOT_FOUND")
            current = self._current_mandate(db, principal.tenant_id, portfolio_id)
            if current is None or current.status == "closed":
                raise PortfolioError("MANDATE_TRANSITION_INVALID")
            if current.status == target_status:
                if target_status == "active":
                    self._require_active_mandate(
                        db, principal.tenant_id, portfolio_id, now
                    )
                db.expunge(current)
                return current
            allowed = {
                ("active", "paused"),
                ("paused", "active"),
                ("active", "closed"),
                ("paused", "closed"),
            }
            if (current.status, target_status) not in allowed:
                raise PortfolioError("MANDATE_TRANSITION_INVALID")
            if target_status == "active":
                assert owner_user_id is not None
                if db.scalar(
                    select(func.count())
                    .select_from(CircuitBreaker)
                    .where(
                        CircuitBreaker.tenant_id == principal.tenant_id,
                        CircuitBreaker.portfolio_id == portfolio_id,
                        CircuitBreaker.status == "open",
                    )
                ):
                    raise PortfolioError("CIRCUIT_BREAKER_OPEN")
                suitability = self._latest_suitability(
                    db, principal.tenant_id, owner_user_id
                )
                if (
                    suitability is None
                    or suitability.id != current.suitability_id
                    or suitability.status != "valid"
                    or suitability.expires_at <= now
                ):
                    raise PortfolioError("SUITABILITY_REQUIRED")
                if _PROFILE_ORDER[RiskProfile(current.profile)] > _PROFILE_ORDER[
                    RiskProfile(suitability.effective_profile)
                ]:
                    raise PortfolioError("PROFILE_EXCEEDS_SUITABILITY")
            payload = {
                "previous_digest": current.digest,
                "version": current.version + 1,
                "status": target_status,
                "effective_at": now.isoformat(),
            }
            row = PortfolioMandate(
                tenant_id=principal.tenant_id,
                portfolio_id=portfolio_id,
                suitability_id=current.suitability_id,
                version=current.version + 1,
                profile=current.profile,
                mode=current.mode,
                status=target_status,
                pause_reason="user_requested" if target_status == "paused" else None,
                policy_version=current.policy_version,
                policy_digest=current.policy_digest,
                digest=self._canonical_digest(payload),
                limits=current.limits,
                created_by_user_id=principal.user_id,
                effective_at=now,
                ended_at=now if target_status == "closed" else None,
            )
            db.add(row)
            db.flush()
            portfolio.current_mandate_id = row.id
            db.flush()
            db.expunge(row)
            return row

    @staticmethod
    def _assert_cash_replay_matches(
        existing: LedgerTransaction,
        *,
        amount: Decimal,
        event_type: str,
        reference_type: str,
        reference_id: str,
        currency: str,
        provenance: str,
        verification_status: str,
        cash_direction: int,
        affects_units: bool,
    ) -> None:
        expected_unit_delta = _ZERO
        if affects_units:
            expected_unit_delta = (amount / existing.nav_per_unit).quantize(
                Decimal("0.000000000001"), ROUND_HALF_UP
            )
            if cash_direction < 0:
                expected_unit_delta = -expected_unit_delta
        if (
            existing.amount != amount
            or existing.event_type != event_type
            or existing.reference_type != reference_type
            or existing.reference_id != reference_id[:128]
            or existing.currency != currency
            or existing.provenance != provenance
            or existing.verification_status != verification_status
            or existing.unit_delta != expected_unit_delta
        ):
            raise PortfolioError("IDEMPOTENCY_PAYLOAD_MISMATCH")

    def _record_cash_transaction(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        amount: Decimal,
        idempotency_key: str,
        event_type: str,
        reference_type: str,
        reference_id: str,
        cash_direction: int,
        provenance: str = "internal",
        verification_status: str = "internal",
        affects_units: bool = False,
        required_mode: RunMode | None = None,
    ) -> LedgerTransaction:
        if cash_direction not in {-1, 1}:
            raise PortfolioError("CASH_DIRECTION_INVALID")
        if provenance == "user_declared":
            expected_event = (
                f"{required_mode.value}_funding" if required_mode is not None else None
            )
            if (
                required_mode not in {RunMode.SHADOW, RunMode.PAPER}
                or event_type != expected_event
                or verification_status != "unverified"
                or cash_direction != 1
                or not affects_units
            ):
                raise PortfolioError("LEDGER_EVENT_INVALID")
        elif provenance == "internal" and (
            verification_status != "internal" or affects_units
        ):
            raise PortfolioError("LEDGER_EVENT_INVALID")
        key_hash = self._key_hash(idempotency_key)
        try:
            with self._sessions() as db, db.begin():
                self._set_portfolio_scope(db, principal)
                now = self._database_now(db)
                portfolio = db.scalar(
                    select(Portfolio)
                    .where(Portfolio.id == portfolio_id, Portfolio.tenant_id == principal.tenant_id)
                    .with_for_update()
                )
                if portfolio is None:
                    raise PortfolioError("PORTFOLIO_NOT_FOUND")
                existing = db.scalar(
                    select(LedgerTransaction).where(
                        LedgerTransaction.tenant_id == principal.tenant_id,
                        LedgerTransaction.portfolio_id == portfolio_id,
                        LedgerTransaction.idempotency_key_hash == key_hash,
                    )
                )
                if existing is not None:
                    self._assert_cash_replay_matches(
                        existing,
                        amount=amount,
                        event_type=event_type,
                        reference_type=reference_type,
                        reference_id=reference_id,
                        currency=portfolio.base_currency,
                        provenance=provenance,
                        verification_status=verification_status,
                        cash_direction=cash_direction,
                        affects_units=affects_units,
                    )
                    db.expunge(existing)
                    return existing
                if portfolio.status != "active":
                    raise PortfolioError("PORTFOLIO_NOT_ACTIVE")
                mandate = self._require_active_mandate(
                    db, principal.tenant_id, portfolio_id, now
                )
                if required_mode is not None and mandate.mode != required_mode.value:
                    raise PortfolioError("FUNDING_MODE_MISMATCH")
                if provenance == "user_declared" and mandate.mode not in {
                    RunMode.SHADOW.value,
                    RunMode.PAPER.value,
                }:
                    raise PortfolioError("CAPITAL_MODE_NOT_ENABLED")
                if provenance == "user_declared" and db.scalar(
                    select(func.count())
                    .select_from(PortfolioPosition)
                    .where(
                        PortfolioPosition.tenant_id == principal.tenant_id,
                        PortfolioPosition.portfolio_id == portfolio_id,
                        PortfolioPosition.quantity > 0,
                    )
                ):
                    raise PortfolioError("PAPER_CAPITAL_LOCKED_AFTER_TRADING")
                cash, reserved = self._cash_balances(
                    db, principal.tenant_id, portfolio_id, now
                )
                if cash_direction < 0 and amount > cash - reserved:
                    raise PortfolioError("INSUFFICIENT_AVAILABLE_CASH")
                nav_per_unit = portfolio.last_nav_per_unit
                unit_delta = _ZERO
                if affects_units:
                    unit_delta = (amount / nav_per_unit).quantize(
                        Decimal("0.000000000001"), ROUND_HALF_UP
                    )
                    if unit_delta == 0:
                        raise PortfolioError("AMOUNT_BELOW_UNIT_PRECISION")
                    if cash_direction < 0:
                        unit_delta = -unit_delta
                        if portfolio.unit_count + unit_delta < 0:
                            raise PortfolioError("INSUFFICIENT_PORTFOLIO_UNITS")
                payload = {
                    "tenant_id": str(principal.tenant_id),
                    "portfolio_id": str(portfolio_id),
                    "event_type": event_type,
                    "reference_type": reference_type,
                    "reference_id": reference_id,
                    "currency": portfolio.base_currency,
                    "amount": str(amount),
                    "cash_direction": cash_direction,
                    "provenance": provenance,
                    "verification_status": verification_status,
                    "unit_delta": str(unit_delta),
                    "nav_per_unit": str(nav_per_unit),
                    "occurred_at": now.isoformat(),
                }
                row = LedgerTransaction(
                    tenant_id=principal.tenant_id,
                    portfolio_id=portfolio_id,
                    idempotency_key_hash=key_hash,
                    event_type=event_type,
                    reference_type=reference_type,
                    reference_id=reference_id[:128],
                    currency=portfolio.base_currency,
                    amount=amount,
                    provenance=provenance,
                    verification_status=verification_status,
                    unit_delta=unit_delta,
                    nav_per_unit=nav_per_unit,
                    digest=self._canonical_digest(payload),
                    created_by_user_id=principal.user_id,
                    occurred_at=now,
                )
                db.add(row)
                db.flush()
                signed = amount if cash_direction > 0 else -amount
                db.add_all(
                    [
                        LedgerPosting(
                            tenant_id=principal.tenant_id,
                            transaction_id=row.id,
                            portfolio_id=portfolio_id,
                            account="cash",
                            asset=portfolio.base_currency,
                            quantity=signed,
                        ),
                        LedgerPosting(
                            tenant_id=principal.tenant_id,
                            transaction_id=row.id,
                            portfolio_id=portfolio_id,
                            account="external",
                            asset=portfolio.base_currency,
                            quantity=-signed,
                        ),
                    ]
                )
                portfolio.unit_count += unit_delta
                portfolio.balance_version += 1
                db.flush()
                db.expunge(row)
                return row
        except IntegrityError:
            with self._sessions() as db, db.begin():
                self._set_portfolio_scope(db, principal)
                existing = db.scalar(
                    select(LedgerTransaction).where(
                        LedgerTransaction.tenant_id == principal.tenant_id,
                        LedgerTransaction.portfolio_id == portfolio_id,
                        LedgerTransaction.idempotency_key_hash == key_hash,
                    )
                )
                if existing is None:
                    raise PortfolioError("LEDGER_WRITE_CONFLICT") from None
                portfolio = db.scalar(
                    select(Portfolio).where(
                        Portfolio.id == portfolio_id,
                        Portfolio.tenant_id == principal.tenant_id,
                    )
                )
                if portfolio is None:
                    raise PortfolioError("LEDGER_WRITE_CONFLICT") from None
                self._assert_cash_replay_matches(
                    existing,
                    amount=amount,
                    event_type=event_type,
                    reference_type=reference_type,
                    reference_id=reference_id,
                    currency=portfolio.base_currency,
                    provenance=provenance,
                    verification_status=verification_status,
                    cash_direction=cash_direction,
                    affects_units=affects_units,
                )
                db.expunge(existing)
                return existing

    def allocate_and_reserve(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        proposal: TrustedAllocationRequest,
        idempotency_key: str,
        ttl: timedelta = timedelta(minutes=10),
    ) -> AllocationReservation:
        """Apply every deterministic cap and atomically reserve the resulting buy cash.

        This is an internal application port. Public clients cannot provide prices,
        correlations, weights, sizing results or quantities through the HTTP API.
        """

        self._authorize(principal, write=True)
        key_hash = self._key_hash(idempotency_key)
        if not timedelta(seconds=30) <= ttl <= timedelta(hours=1):
            raise PortfolioError("RESERVATION_TTL_INVALID")
        with self._reservation_lock, self._sessions() as db, db.begin():
            self._set_portfolio_scope(db, principal)
            now = self._database_now(db)
            self._validate_allocation(proposal, now=now)
            portfolio = db.scalar(
                select(Portfolio)
                .where(
                    Portfolio.id == portfolio_id,
                    Portfolio.tenant_id == principal.tenant_id,
                )
                .with_for_update()
            )
            if portfolio is None or portfolio.status != "active":
                raise PortfolioError("PORTFOLIO_NOT_ACTIVE")
            mandate = self._require_active_mandate(
                db, principal.tenant_id, portfolio_id, now
            )
            existing_reservation = db.scalar(
                select(CashReservation).where(
                    CashReservation.tenant_id == principal.tenant_id,
                    CashReservation.portfolio_id == portfolio_id,
                    CashReservation.idempotency_key_hash == key_hash,
                )
            )
            if existing_reservation is not None and (
                existing_reservation.status != "active"
                or existing_reservation.expires_at <= now
            ):
                raise PortfolioError("IDEMPOTENCY_RESERVATION_FINALIZED")
            if db.scalar(
                select(func.count())
                .select_from(CircuitBreaker)
                .where(
                    CircuitBreaker.tenant_id == principal.tenant_id,
                    CircuitBreaker.portfolio_id == portfolio_id,
                    CircuitBreaker.status == "open",
                )
            ):
                raise PortfolioError("CIRCUIT_BREAKER_OPEN")
            snapshot = db.scalar(
                select(PortfolioSnapshot)
                .where(
                    PortfolioSnapshot.tenant_id == principal.tenant_id,
                    PortfolioSnapshot.portfolio_id == portfolio_id,
                )
                .order_by(PortfolioSnapshot.sequence.desc())
                .limit(1)
            )
            if snapshot is None:
                raise PortfolioError("BASELINE_SNAPSHOT_REQUIRED")
            if (
                snapshot.valuation_available_at < now - timedelta(minutes=15)
                or snapshot.captured_at < now - timedelta(minutes=15)
            ):
                raise PortfolioError("ALLOCATION_BASELINE_STALE")
            capital = self._decimal("capital", snapshot.equity, positive=True)
            cash, reserved = self._cash_balances(
                db, principal.tenant_id, portfolio_id, now
            )
            available_cash = cash - reserved
            if existing_reservation is not None:
                available_cash += existing_reservation.amount
            if available_cash <= 0:
                raise PortfolioError("INSUFFICIENT_AVAILABLE_CASH")
            policy = policy_for(RiskProfile(mandate.profile))
            if mandate.policy_version != POLICY_VERSION or not hmac.compare_digest(
                mandate.policy_digest, policy.digest
            ):
                raise PortfolioError("POLICY_VERSION_MISMATCH")
            positions = {
                row.symbol: row
                for row in db.scalars(
                    select(PortfolioPosition).where(
                        PortfolioPosition.tenant_id == principal.tenant_id,
                        PortfolioPosition.portfolio_id == portfolio_id,
                        PortfolioPosition.quantity > 0,
                    )
                )
            }
            if not set(positions).issubset(proposal.assets):
                raise PortfolioError("ALLOCATION_SYMBOL_SET_MISMATCH")
            current_weights: dict[str, Decimal] = {}
            for symbol, asset in proposal.assets.items():
                existing = positions.get(symbol)
                if existing is not None and existing.asset_class != asset.asset_class:
                    raise PortfolioError("ALLOCATION_ASSET_CLASS_MISMATCH")
                current_weights[symbol] = (
                    existing.quantity * asset.price / capital
                    if existing is not None
                    else _ZERO
                )
            asset_classes = {
                symbol: asset.asset_class for symbol, asset in proposal.assets.items()
            }
            try:
                capped = cap_incremental_target_weights(
                    policy,
                    dict(proposal.raw_target_weights),
                    current_weights,
                    dict(proposal.correlations),
                    asset_classes=asset_classes,
                )
            except ValueError as exc:
                raise PortfolioError("ALLOCATION_INPUT_INVALID") from exc
            lines: list[AllocationLine] = []
            remaining_cash = available_cash
            for symbol in sorted(capped):
                asset = proposal.assets[symbol]
                try:
                    sizing = size_position(
                        policy,
                        SizingInput(
                            capital=capital,
                            available_cash=max(_ZERO, remaining_cash),
                            price=asset.price,
                            conviction=asset.conviction,
                            realized_annual_volatility=asset.realized_annual_volatility,
                            average_daily_notional=asset.average_daily_notional,
                            current_weight=current_weights[symbol],
                            trading_cost_bps=asset.trading_cost_bps,
                            asset_class=asset.asset_class,
                            target_weight_cap=capped[symbol],
                        ),
                    )
                except ValueError as exc:
                    raise PortfolioError("ALLOCATION_INPUT_INVALID") from exc
                remaining_cash -= sizing.order_notional
                lines.append(
                    AllocationLine(
                        symbol=symbol,
                        asset_class=asset.asset_class,
                        capped_weight=capped[symbol],
                        sizing=sizing,
                    )
                )
            total = sum((line.sizing.order_notional for line in lines), _ZERO)
            if total <= 0:
                raise PortfolioError("NO_REBALANCE_REQUIRED")
            digest_payload = {
                "portfolio_id": str(portfolio_id),
                "mandate_id": str(mandate.id),
                "policy_digest": policy.digest,
                "evidence_digest": proposal.evidence_digest,
                "source": proposal.source,
                "observed_at": proposal.observed_at.isoformat(),
                "available_at": proposal.available_at.isoformat(),
                "lines": [
                    {
                        "symbol": line.symbol,
                        "asset_class": line.asset_class,
                        "capped_weight": str(line.capped_weight),
                        "target_weight": str(line.sizing.target_weight),
                        "order_notional": str(line.sizing.order_notional),
                        "quantity": str(line.sizing.quantity),
                        "limited_by": line.sizing.limited_by,
                    }
                    for line in lines
                ],
                "total_order_notional": str(total),
            }
            allocation_digest = self._canonical_digest(digest_payload)
            if existing_reservation is not None:
                if (
                    existing_reservation.amount != total
                    or not hmac.compare_digest(
                        existing_reservation.purpose_digest, allocation_digest
                    )
                    or existing_reservation.mandate_id != mandate.id
                ):
                    raise PortfolioError("IDEMPOTENCY_PAYLOAD_MISMATCH")
                db.expunge(existing_reservation)
                return AllocationReservation(
                    portfolio_id=portfolio_id,
                    mandate_id=mandate.id,
                    policy_digest=policy.digest,
                    allocation_digest=allocation_digest,
                    evidence_digest=proposal.evidence_digest,
                    total_order_notional=total,
                    lines=tuple(lines),
                    reservation=existing_reservation,
                )
            reservation = self._create_reservation(
                db,
                principal=principal,
                portfolio=portfolio,
                mandate=mandate,
                key_hash=key_hash,
                amount=total,
                purpose_digest=allocation_digest,
                ttl=ttl,
                now=now,
            )
            db.expunge(reservation)
            return AllocationReservation(
                portfolio_id=portfolio_id,
                mandate_id=mandate.id,
                policy_digest=policy.digest,
                allocation_digest=allocation_digest,
                evidence_digest=proposal.evidence_digest,
                total_order_notional=total,
                lines=tuple(lines),
                reservation=reservation,
            )

    def reserve_cash(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        amount: Decimal,
        purpose_digest: str,
        idempotency_key: str,
        ttl: timedelta = timedelta(minutes=10),
    ) -> CashReservation:
        # PostgreSQL serializes on the portfolio row. SQLite has no effective
        # FOR UPDATE, so the local runtime must serialize the same operation.
        with self._reservation_lock:
            return self._reserve_cash_locked(
                principal=principal,
                portfolio_id=portfolio_id,
                amount=amount,
                purpose_digest=purpose_digest,
                idempotency_key=idempotency_key,
                ttl=ttl,
            )

    def _reserve_cash_locked(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        amount: Decimal,
        purpose_digest: str,
        idempotency_key: str,
        ttl: timedelta = timedelta(minutes=10),
    ) -> CashReservation:
        self._authorize(principal, write=True)
        self._decimal("amount", amount, positive=True)
        if len(purpose_digest) != 64 or any(ch not in "0123456789abcdef" for ch in purpose_digest):
            raise PortfolioError("PURPOSE_DIGEST_INVALID")
        if not timedelta(seconds=30) <= ttl <= timedelta(hours=1):
            raise PortfolioError("RESERVATION_TTL_INVALID")
        key_hash = self._key_hash(idempotency_key)
        try:
            with self._sessions() as db, db.begin():
                self._set_portfolio_scope(db, principal)
                now = self._database_now(db)
                portfolio, mandate = self._reservation_authorization_context(
                    db,
                    tenant_id=principal.tenant_id,
                    portfolio_id=portfolio_id,
                    now=now,
                )
                existing = self._validated_reservation_replay(
                    db,
                    tenant_id=principal.tenant_id,
                    portfolio_id=portfolio_id,
                    key_hash=key_hash,
                    amount=amount,
                    purpose_digest=purpose_digest,
                    mandate_id=mandate.id,
                    now=now,
                )
                if existing is not None:
                    db.expunge(existing)
                    return existing
                cash, reserved = self._cash_balances(db, principal.tenant_id, portfolio_id, now)
                if amount > cash - reserved:
                    raise PortfolioError("INSUFFICIENT_AVAILABLE_CASH")
                row = self._create_reservation(
                    db,
                    principal=principal,
                    portfolio=portfolio,
                    mandate=mandate,
                    key_hash=key_hash,
                    amount=amount,
                    purpose_digest=purpose_digest,
                    ttl=ttl,
                    now=now,
                )
                db.expunge(row)
                return row
        except IntegrityError as exc:
            if not self._is_reservation_idempotency_conflict(exc):
                raise
            with self._sessions() as db, db.begin():
                self._set_portfolio_scope(db, principal)
                now = self._database_now(db)
                _portfolio, mandate = self._reservation_authorization_context(
                    db,
                    tenant_id=principal.tenant_id,
                    portfolio_id=portfolio_id,
                    now=now,
                )
                existing = self._validated_reservation_replay(
                    db,
                    tenant_id=principal.tenant_id,
                    portfolio_id=portfolio_id,
                    key_hash=key_hash,
                    amount=amount,
                    purpose_digest=purpose_digest,
                    mandate_id=mandate.id,
                    now=now,
                )
                if existing is None:
                    raise exc
                db.expunge(existing)
                return existing

    def release_reservation(self, *, principal: IdentityPrincipal, reservation_id: UUID) -> str:
        self._authorize(principal, write=True)
        with self._sessions() as db, db.begin():
            self._set_portfolio_scope(db, principal)
            if db.get_bind().dialect.name == "postgresql":
                now = self._database_now(db)
                capability = self._mint_capability(
                    principal=principal,
                    audience="portfolio.release.v1",
                    fields=(str(reservation_id),),
                    now=now,
                )
                status = db.scalar(
                    text(
                        "SELECT tradingagents_private.portfolio_release_cash_v1("
                        ":reservation_id, :cap_kid, :cap_session_id, :cap_token_hash, "
                        ":cap_issued_at, :cap_expires_at, :cap_nonce, :cap_mac)"
                    ),
                    {"reservation_id": reservation_id} | capability,
                )
                if not isinstance(status, str):
                    raise PortfolioError("RESERVATION_NOT_FOUND")
                return status
            now = self._database_now(db)
            row = db.scalar(
                select(CashReservation)
                .where(
                    CashReservation.id == reservation_id,
                    CashReservation.tenant_id == principal.tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                raise PortfolioError("RESERVATION_NOT_FOUND")
            if row.status == "active":
                row.status = "released"
                row.released_at = now
            return row.status

    def _create_reservation(
        self,
        db: Session,
        *,
        principal: IdentityPrincipal,
        portfolio: Portfolio,
        mandate: PortfolioMandate,
        key_hash: str,
        amount: Decimal,
        purpose_digest: str,
        ttl: timedelta,
        now: datetime,
    ) -> CashReservation:
        if db.get_bind().dialect.name == "postgresql":
            ttl_microseconds = self._ttl_microseconds(ttl)
            capability = self._mint_capability(
                principal=principal,
                audience="portfolio.reserve.v1",
                fields=(
                    str(portfolio.id),
                    self._canonical_decimal(amount),
                    purpose_digest,
                    key_hash,
                    str(ttl_microseconds),
                ),
                now=now,
            )
            statement = select(CashReservation).from_statement(
                text(
                    "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
                    ":portfolio_id, :amount, :purpose_digest, :key_hash, :ttl_microseconds, "
                    ":cap_kid, :cap_session_id, :cap_token_hash, :cap_issued_at, "
                    ":cap_expires_at, :cap_nonce, :cap_mac)"
                )
            )
            row = db.scalars(
                statement,
                {
                    "portfolio_id": portfolio.id,
                    "amount": amount,
                    "purpose_digest": purpose_digest,
                    "key_hash": key_hash,
                    "ttl_microseconds": ttl_microseconds,
                }
                | capability,
            ).one()
            db.expire(portfolio, ["balance_version"])
            return row
        row = CashReservation(
            tenant_id=portfolio.tenant_id,
            portfolio_id=portfolio.id,
            mandate_id=mandate.id,
            idempotency_key_hash=key_hash,
            currency=portfolio.base_currency,
            amount=amount,
            status="active",
            purpose_digest=purpose_digest,
            expires_at=now + ttl,
            created_at=now,
        )
        db.add(row)
        portfolio.balance_version += 1
        db.flush()
        return row

    def list_portfolios(self, *, principal: IdentityPrincipal) -> tuple[PortfolioOverview, ...]:
        self._authorize(principal)
        with self._sessions() as db, db.begin():
            self._set_portfolio_scope(db, principal)
            now = self._database_now(db)
            rows = db.scalars(
                select(Portfolio)
                .where(Portfolio.tenant_id == principal.tenant_id)
                .order_by(Portfolio.created_at)
            ).all()
            result = []
            for row in rows:
                current = self._current_mandate(db, principal.tenant_id, row.id)
                mandate, mandate_error = self._mandate_eligibility(
                    db, principal.tenant_id, row.id, now
                )
                cash, reserved = self._cash_balances(db, principal.tenant_id, row.id, now)
                required_action: PortfolioRequiredAction | None = None
                breaker_open = bool(
                    db.scalar(
                        select(func.count())
                        .select_from(CircuitBreaker)
                        .where(
                            CircuitBreaker.tenant_id == principal.tenant_id,
                            CircuitBreaker.portfolio_id == row.id,
                            CircuitBreaker.status == "open",
                        )
                    )
                )
                latest = self._latest_suitability(
                    db, principal.tenant_id, row.owner_user_id
                )
                if breaker_open:
                    required_action = PortfolioRequiredAction.REVIEW_RISK_HOLD
                elif current is None:
                    required_action = (
                        PortfolioRequiredAction.COMPLETE_SUITABILITY_AND_CREATE_MANDATE
                    )
                elif current.status == "paused":
                    resumable = (
                        latest is not None
                        and latest.id == current.suitability_id
                        and latest.status == "valid"
                        and latest.assessed_at <= now < latest.expires_at
                        and _PROFILE_ORDER[RiskProfile(current.profile)]
                        <= _PROFILE_ORDER[RiskProfile(latest.effective_profile)]
                    )
                    required_action = (
                        PortfolioRequiredAction.RESUME_MANDATE
                        if resumable
                        else PortfolioRequiredAction.CREATE_COMPATIBLE_MANDATE
                    )
                elif current.status == "circuit_broken":
                    required_action = PortfolioRequiredAction.REVIEW_RISK_HOLD
                elif current.status == "closed":
                    required_action = PortfolioRequiredAction.CREATE_NEW_PORTFOLIO
                elif mandate_error == "SUITABILITY_REQUIRED":
                    required_action = (
                        PortfolioRequiredAction.COMPLETE_SUITABILITY_AND_CREATE_MANDATE
                    )
                elif mandate_error in {
                    "SUITABILITY_STALE",
                    "PROFILE_EXCEEDS_SUITABILITY",
                }:
                    required_action = PortfolioRequiredAction.CREATE_COMPATIBLE_MANDATE
                result.append(
                    PortfolioOverview(
                        id=row.id,
                        name=row.name,
                        base_currency=row.base_currency,
                        status=row.status,
                        mandate_profile=current.profile if current else None,
                        suitability_effective_profile=(
                            latest.effective_profile if latest is not None else None
                        ),
                        mode=current.mode if current else None,
                        cash=cash,
                        reserved_cash=reserved,
                        available_cash=cash - reserved,
                        current_mandate_id=current.id if current else None,
                        mandate_status=current.status if current else None,
                        pause_reason=current.pause_reason if current else None,
                        required_action=required_action,
                        circuit_breaker_open=breaker_open,
                    )
                )
            return tuple(result)

    def latest_snapshot(
        self, *, principal: IdentityPrincipal, portfolio_id: UUID
    ) -> PortfolioSnapshotOverview | None:
        """Return the latest internally-produced snapshot without accepting valuation input."""

        self._authorize(principal)
        with self._sessions() as db, db.begin():
            self._set_portfolio_scope(db, principal)
            if not db.scalar(
                select(func.count()).select_from(Portfolio).where(
                    Portfolio.id == portfolio_id,
                    Portfolio.tenant_id == principal.tenant_id,
                )
            ):
                raise PortfolioError("PORTFOLIO_NOT_FOUND")
            row = db.scalar(
                select(PortfolioSnapshot)
                .where(
                    PortfolioSnapshot.tenant_id == principal.tenant_id,
                    PortfolioSnapshot.portfolio_id == portfolio_id,
                )
                .order_by(PortfolioSnapshot.sequence.desc())
                .limit(1)
            )
            if row is None:
                return None
            now = self._database_now(db)
            freshness = "fresh" if row.valuation_available_at >= now - timedelta(minutes=15) else "stale"
            return PortfolioSnapshotOverview(
                portfolio_id=row.portfolio_id,
                sequence=row.sequence,
                cash=row.cash,
                reserved_cash=row.reserved_cash,
                market_value=row.market_value,
                equity=row.equity,
                nav_per_unit=row.nav_per_unit,
                realized_pnl=row.realized_pnl,
                unrealized_pnl=row.unrealized_pnl,
                drawdown=row.drawdown,
                daily_drawdown=row.daily_drawdown,
                unit_count=row.unit_count,
                high_water_nav=row.high_water_nav,
                valuation_source=row.valuation_source,
                valuation_observed_at=row.valuation_observed_at,
                valuation_available_at=row.valuation_available_at,
                captured_at=row.captured_at,
                freshness=freshness,
            )

    def capture_trusted_snapshot(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        valuation: TrustedValuation,
    ) -> PortfolioSnapshot:
        self._authorize(principal, write=True)
        with self._sessions() as db, db.begin():
            self._set_portfolio_scope(db, principal)
            now = self._database_now(db)
            portfolio = db.scalar(
                select(Portfolio)
                .where(Portfolio.id == portfolio_id, Portfolio.tenant_id == principal.tenant_id)
                .with_for_update()
            )
            if portfolio is None:
                raise PortfolioError("PORTFOLIO_NOT_FOUND")
            mandate = self._require_active_mandate(
                db, principal.tenant_id, portfolio_id, now
            )
            if mandate.mode not in {
                RunMode.SHADOW.value,
                RunMode.PAPER.value,
            }:
                raise PortfolioError("ACTIVE_MANDATE_REQUIRED")
            self._validate_valuation(valuation, now=now)
            positions = db.scalars(
                select(PortfolioPosition).where(
                    PortfolioPosition.tenant_id == principal.tenant_id,
                    PortfolioPosition.portfolio_id == portfolio_id,
                )
            ).all()
            expected_symbols = {position.symbol for position in positions}
            if set(valuation.prices) != expected_symbols:
                raise PortfolioError("VALUATION_SYMBOL_SET_MISMATCH")
            normalized_prices: dict[str, str] = {}
            market_value = _ZERO
            unrealized = _ZERO
            realized = _ZERO
            position_payload: dict[str, Any] = {}
            for position in positions:
                price = valuation.prices.get(position.symbol)
                self._decimal("price", price, positive=True)  # type: ignore[arg-type]
                normalized_prices[position.symbol] = str(price)
                value = position.quantity * price
                market_value += value
                unrealized += (price - position.average_cost) * position.quantity
                realized += position.realized_pnl
                position_payload[position.symbol] = {
                    "asset_class": position.asset_class,
                    "quantity": str(position.quantity),
                    "average_cost": str(position.average_cost),
                    "market_value": str(value),
                }
            cash, reserved = self._cash_balances(db, principal.tenant_id, portfolio_id, now)
            equity = cash + market_value
            if portfolio.unit_count <= 0:
                raise PortfolioError("PORTFOLIO_HAS_NO_CAPITAL")
            nav_per_unit = (equity / portfolio.unit_count).quantize(
                Decimal("0.000000000001")
            )
            if nav_per_unit <= 0:
                raise PortfolioError("PORTFOLIO_NAV_INVALID")
            previous = db.scalar(
                select(PortfolioSnapshot)
                .where(
                    PortfolioSnapshot.tenant_id == principal.tenant_id,
                    PortfolioSnapshot.portfolio_id == portfolio_id,
                )
                .order_by(PortfolioSnapshot.sequence.desc())
                .limit(1)
            )
            sequence = (previous.sequence if previous else 0) + 1
            opening_nav = db.scalar(
                select(LedgerTransaction.nav_per_unit)
                .where(
                    LedgerTransaction.tenant_id == principal.tenant_id,
                    LedgerTransaction.portfolio_id == portfolio_id,
                    LedgerTransaction.unit_delta > 0,
                )
                .order_by(LedgerTransaction.occurred_at.asc(), LedgerTransaction.id.asc())
                .limit(1)
            )
            initial_baseline_nav = opening_nav or portfolio.last_nav_per_unit
            high_water_nav = max(
                nav_per_unit,
                previous.high_water_nav if previous else initial_baseline_nav,
            )
            drawdown = (high_water_nav - nav_per_unit) / high_water_nav
            high_water = high_water_nav * portfolio.unit_count
            day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
            daily_baseline = db.scalar(
                select(PortfolioSnapshot)
                .where(
                    PortfolioSnapshot.tenant_id == principal.tenant_id,
                    PortfolioSnapshot.portfolio_id == portfolio_id,
                    PortfolioSnapshot.captured_at >= day_start,
                    PortfolioSnapshot.captured_at < day_start + timedelta(days=1),
                )
                .order_by(PortfolioSnapshot.sequence.asc())
                .limit(1)
            )
            if daily_baseline is None:
                daily_baseline = previous
            daily_drawdown = _ZERO
            daily_baseline_nav = (
                daily_baseline.nav_per_unit
                if daily_baseline is not None
                else initial_baseline_nav
            )
            if daily_baseline_nav > nav_per_unit:
                daily_drawdown = (daily_baseline_nav - nav_per_unit) / daily_baseline_nav
            payload = {
                "tenant_id": str(principal.tenant_id),
                "portfolio_id": str(portfolio_id),
                "sequence": sequence,
                "cash": str(cash),
                "reserved_cash": str(reserved),
                "market_value": str(market_value),
                "equity": str(equity),
                "realized_pnl": str(realized),
                "unrealized_pnl": str(unrealized),
                "high_water_mark": str(high_water),
                "drawdown": str(drawdown),
                "unit_count": str(portfolio.unit_count),
                "nav_per_unit": str(nav_per_unit),
                "high_water_nav": str(high_water_nav),
                "daily_drawdown": str(daily_drawdown),
                "positions": position_payload,
                "prices": normalized_prices,
                "valuation_source": valuation.source,
                "valuation_observed_at": valuation.observed_at.isoformat(),
                "valuation_available_at": valuation.available_at.isoformat(),
                "valuation_digest": valuation.evidence_digest,
                "captured_at": now.isoformat(),
            }
            row = PortfolioSnapshot(
                tenant_id=principal.tenant_id,
                portfolio_id=portfolio_id,
                sequence=sequence,
                cash=cash,
                reserved_cash=reserved,
                market_value=market_value,
                equity=equity,
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                high_water_mark=high_water,
                drawdown=drawdown,
                unit_count=portfolio.unit_count,
                nav_per_unit=nav_per_unit,
                high_water_nav=high_water_nav,
                daily_drawdown=daily_drawdown,
                positions=position_payload,
                prices=normalized_prices,
                valuation_source=valuation.source,
                valuation_observed_at=valuation.observed_at,
                valuation_available_at=valuation.available_at,
                valuation_digest=valuation.evidence_digest,
                digest=self._canonical_digest(payload),
                captured_at=now,
            )
            db.add(row)
            portfolio.last_nav_per_unit = nav_per_unit
            policy = policy_for(RiskProfile(mandate.profile))
            breaker_kind: str | None = None
            observed = drawdown
            threshold = policy.max_total_drawdown
            if daily_drawdown >= policy.max_daily_drawdown:
                breaker_kind = "daily_drawdown"
                observed = daily_drawdown
                threshold = policy.max_daily_drawdown
            elif drawdown >= policy.max_total_drawdown:
                breaker_kind = "maximum_drawdown"
            if breaker_kind is not None and not db.scalar(
                select(func.count())
                .select_from(CircuitBreaker)
                .where(
                    CircuitBreaker.tenant_id == principal.tenant_id,
                    CircuitBreaker.portfolio_id == portfolio_id,
                    CircuitBreaker.status == "open",
                )
            ):
                db.add(
                    CircuitBreaker(
                        tenant_id=principal.tenant_id,
                        portfolio_id=portfolio_id,
                        mandate_id=mandate.id,
                        kind=breaker_kind,
                        status="open",
                        observed_value=observed,
                        threshold_value=threshold,
                        evidence_digest=row.digest,
                        triggered_at=now,
                    )
                )
            db.flush()
            db.expunge(row)
            return row

    @staticmethod
    def _validate_valuation(valuation: TrustedValuation, *, now: datetime) -> None:
        if valuation.source not in {"broker_reconciliation", "market_data_pit"}:
            raise PortfolioError("VALUATION_SOURCE_INVALID")
        if (
            valuation.observed_at.tzinfo is None
            or valuation.observed_at.utcoffset() is None
            or valuation.available_at.tzinfo is None
            or valuation.available_at.utcoffset() is None
            or valuation.observed_at > valuation.available_at
            or valuation.available_at > now
        ):
            raise PortfolioError("VALUATION_TIMELINE_INVALID")
        digest = valuation.evidence_digest
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise PortfolioError("VALUATION_DIGEST_INVALID")
        if not isinstance(valuation.prices, Mapping):
            raise PortfolioError("VALUATION_PRICES_INVALID")

    @staticmethod
    def _validate_allocation(
        proposal: TrustedAllocationRequest, *, now: datetime
    ) -> None:
        if proposal.source not in {"analysis_pit", "broker_reconciliation"}:
            raise PortfolioError("ALLOCATION_SOURCE_INVALID")
        if (
            proposal.observed_at.tzinfo is None
            or proposal.observed_at.utcoffset() is None
            or proposal.available_at.tzinfo is None
            or proposal.available_at.utcoffset() is None
            or proposal.observed_at > proposal.available_at
            or proposal.available_at > now
            or proposal.available_at < now - timedelta(minutes=15)
        ):
            raise PortfolioError("ALLOCATION_TIMELINE_INVALID")
        digest = proposal.evidence_digest
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise PortfolioError("ALLOCATION_DIGEST_INVALID")
        if not all(
            isinstance(value, Mapping)
            for value in (
                proposal.raw_target_weights,
                proposal.correlations,
                proposal.assets,
            )
        ):
            raise PortfolioError("ALLOCATION_INPUT_INVALID")
        symbols = set(proposal.raw_target_weights)
        if not symbols or symbols != set(proposal.assets):
            raise PortfolioError("ALLOCATION_SYMBOL_SET_MISMATCH")
        ordered_symbols = sorted(symbols)
        expected_pairs = {
            (left, right)
            for index, left in enumerate(ordered_symbols)
            for right in ordered_symbols[index + 1 :]
        }
        if set(proposal.correlations) != expected_pairs:
            raise PortfolioError("ALLOCATION_CORRELATION_SET_MISMATCH")
        for symbol, asset in proposal.assets.items():
            if symbol != asset.symbol or not symbol or len(symbol) > 64:
                raise PortfolioError("ALLOCATION_SYMBOL_SET_MISMATCH")
            if asset.asset_class not in {"crypto", "stock"}:
                raise PortfolioError("ALLOCATION_ASSET_CLASS_MISMATCH")

    @staticmethod
    def _active_mandate(
        db: Session, tenant_id: UUID, portfolio_id: UUID, at: datetime
    ) -> PortfolioMandate | None:
        current, error = PortfolioService._mandate_eligibility(
            db, tenant_id, portfolio_id, at
        )
        return current if error is None else None

    @staticmethod
    def _require_active_mandate(
        db: Session, tenant_id: UUID, portfolio_id: UUID, at: datetime
    ) -> PortfolioMandate:
        current, error = PortfolioService._mandate_eligibility(
            db, tenant_id, portfolio_id, at
        )
        if error is not None or current is None:
            raise PortfolioError(error or "ACTIVE_MANDATE_REQUIRED")
        return current

    @staticmethod
    def _mandate_eligibility(
        db: Session, tenant_id: UUID, portfolio_id: UUID, at: datetime
    ) -> tuple[PortfolioMandate | None, str | None]:
        current = PortfolioService._current_mandate(db, tenant_id, portfolio_id)
        if (
            current is None
            or current.status != "active"
            or current.effective_at > at
            or current.ended_at is not None
        ):
            return current, "ACTIVE_MANDATE_REQUIRED"
        owner_user_id = db.scalar(
            select(Portfolio.owner_user_id).where(
                Portfolio.tenant_id == tenant_id,
                Portfolio.id == portfolio_id,
            )
        )
        if owner_user_id is None:
            return current, "PORTFOLIO_NOT_FOUND"
        suitability = PortfolioService._latest_suitability(
            db, tenant_id, owner_user_id
        )
        if (
            suitability is None
            or suitability.status != "valid"
            or suitability.assessed_at > at
            or suitability.expires_at <= at
        ):
            return current, "SUITABILITY_REQUIRED"
        if current.suitability_id != suitability.id:
            return current, "SUITABILITY_STALE"
        try:
            if _PROFILE_ORDER[RiskProfile(current.profile)] > _PROFILE_ORDER[
                RiskProfile(suitability.effective_profile)
            ]:
                return current, "PROFILE_EXCEEDS_SUITABILITY"
        except ValueError:
            return current, "PROFILE_EXCEEDS_SUITABILITY"
        return current, None

    @staticmethod
    def _current_mandate(
        db: Session, tenant_id: UUID, portfolio_id: UUID
    ) -> PortfolioMandate | None:
        current_id = db.scalar(
            select(Portfolio.current_mandate_id)
            .where(
                Portfolio.tenant_id == tenant_id,
                Portfolio.id == portfolio_id,
            )
        )
        if current_id is None:
            return None
        return db.scalar(
            select(PortfolioMandate).where(
                PortfolioMandate.id == current_id,
                PortfolioMandate.tenant_id == tenant_id,
                PortfolioMandate.portfolio_id == portfolio_id,
            )
        )

    @staticmethod
    def _cash_balances(
        db: Session, tenant_id: UUID, portfolio_id: UUID, at: datetime
    ) -> tuple[Decimal, Decimal]:
        cash = db.scalar(
            select(func.coalesce(func.sum(LedgerPosting.quantity), 0)).where(
                LedgerPosting.tenant_id == tenant_id,
                LedgerPosting.portfolio_id == portfolio_id,
                LedgerPosting.account == "cash",
            )
        )
        reserved = db.scalar(
            select(func.coalesce(func.sum(CashReservation.amount), 0)).where(
                CashReservation.tenant_id == tenant_id,
                CashReservation.portfolio_id == portfolio_id,
                CashReservation.status == "active",
                CashReservation.expires_at > at,
            )
        )
        return Decimal(cash or 0), Decimal(reserved or 0)

    def _reservation_authorization_context(
        self,
        db: Session,
        *,
        tenant_id: UUID,
        portfolio_id: UUID,
        now: datetime,
    ) -> tuple[Portfolio, PortfolioMandate]:
        portfolio = db.scalar(
            select(Portfolio)
            .where(Portfolio.id == portfolio_id, Portfolio.tenant_id == tenant_id)
            .with_for_update()
        )
        if portfolio is None or portfolio.status != "active":
            raise PortfolioError("PORTFOLIO_NOT_ACTIVE")
        mandate = self._require_active_mandate(db, tenant_id, portfolio_id, now)
        if not db.scalar(
            select(func.count())
            .select_from(PortfolioSnapshot)
            .where(
                PortfolioSnapshot.tenant_id == tenant_id,
                PortfolioSnapshot.portfolio_id == portfolio_id,
            )
        ):
            raise PortfolioError("BASELINE_SNAPSHOT_REQUIRED")
        if db.scalar(
            select(func.count())
            .select_from(CircuitBreaker)
            .where(
                CircuitBreaker.tenant_id == tenant_id,
                CircuitBreaker.portfolio_id == portfolio_id,
                CircuitBreaker.status == "open",
            )
        ):
            raise PortfolioError("CIRCUIT_BREAKER_OPEN")
        return portfolio, mandate

    @staticmethod
    def _validated_reservation_replay(
        db: Session,
        *,
        tenant_id: UUID,
        portfolio_id: UUID,
        key_hash: str,
        amount: Decimal,
        purpose_digest: str,
        mandate_id: UUID,
        now: datetime,
    ) -> CashReservation | None:
        existing = db.scalar(
            select(CashReservation).where(
                CashReservation.tenant_id == tenant_id,
                CashReservation.portfolio_id == portfolio_id,
                CashReservation.idempotency_key_hash == key_hash,
            )
        )
        if existing is None:
            return None
        if existing.amount != amount or not hmac.compare_digest(
            existing.purpose_digest, purpose_digest
        ):
            raise PortfolioError("IDEMPOTENCY_PAYLOAD_MISMATCH")
        if (
            existing.status != "active"
            or existing.expires_at <= now
            or existing.mandate_id != mandate_id
        ):
            raise PortfolioError("IDEMPOTENCY_RESERVATION_FINALIZED")
        return existing

    @staticmethod
    def _is_reservation_idempotency_conflict(exc: IntegrityError) -> bool:
        original = exc.orig
        diagnostic = getattr(original, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name is not None:
            return constraint_name == "uq_reservation_idempotency"
        message = str(original).lower()
        return (
            "unique constraint failed" in message
            and "portfolio_cash_reservations.tenant_id" in message
            and "portfolio_cash_reservations.portfolio_id" in message
            and "portfolio_cash_reservations.idempotency_key_hash" in message
        )
