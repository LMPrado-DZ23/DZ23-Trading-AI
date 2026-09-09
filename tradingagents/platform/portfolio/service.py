"""Transactional application service for deterministic portfolio risk controls."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from tradingagents.platform.portfolio.policies import POLICY_VERSION, policy_for

_PROFILE_ORDER = {
    RiskProfile.PRESERVATION: 0,
    RiskProfile.CONSERVATIVE: 1,
    RiskProfile.MODERATE: 2,
    RiskProfile.GROWTH: 3,
    RiskProfile.AGGRESSIVE: 4,
}
_ZERO = Decimal("0")


class PortfolioError(RuntimeError):
    """Stable, non-sensitive portfolio error code."""


@dataclass(frozen=True, slots=True)
class PortfolioOverview:
    id: UUID
    name: str
    base_currency: str
    status: str
    effective_profile: str | None
    mode: str | None
    cash: Decimal
    reserved_cash: Decimal
    available_cash: Decimal


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


class PortfolioService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        idempotency_digest_key: bytes,
    ) -> None:
        if len(idempotency_digest_key) < 32:
            raise ValueError("portfolio idempotency key must contain at least 32 bytes")
        self._sessions = session_factory
        self._digest_key = idempotency_digest_key
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

    def assess_suitability(
        self,
        *,
        principal: IdentityPrincipal,
        requested_profile: RiskProfile,
        answers: dict[str, Any],
        valid_for: timedelta = timedelta(days=365),
    ) -> SuitabilityRecord:
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
    ) -> SuitabilityRecord:
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
        now = self._now()
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
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
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
            db.expunge(row)
            return row

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
                self._set_tenant_scope(db, principal.tenant_id)
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
            self._set_tenant_scope(db, principal.tenant_id)
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
    ) -> LedgerTransaction:
        self._authorize(principal, write=True)
        self._decimal("amount", amount, positive=True)
        clean_reference = reference_id.strip()
        if not clean_reference or len(clean_reference) > 128:
            raise PortfolioError("FUNDING_REFERENCE_INVALID")
        return self._record_cash_transaction(
            principal=principal,
            portfolio_id=portfolio_id,
            amount=amount,
            idempotency_key=idempotency_key,
            event_type="paper_funding",
            reference_type="user_declared",
            reference_id=clean_reference,
            cash_direction=1,
            provenance="user_declared",
            verification_status="unverified",
            affects_units=True,
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
            self._set_tenant_scope(db, principal.tenant_id)
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
                Decimal("0.000000000001")
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
    ) -> LedgerTransaction:
        key_hash = self._key_hash(idempotency_key)
        try:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
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
                mandate = self._active_mandate(db, principal.tenant_id, portfolio_id, now)
                if mandate is None:
                    raise PortfolioError("ACTIVE_MANDATE_REQUIRED")
                if provenance == "user_declared" and mandate.mode not in {
                    RunMode.BACKTEST.value,
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
                    unit_delta = (amount / nav_per_unit).quantize(Decimal("0.000000000001"))
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
                self._set_tenant_scope(db, principal.tenant_id)
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
        now = self._now()
        try:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
                portfolio = db.scalar(
                    select(Portfolio)
                    .where(Portfolio.id == portfolio_id, Portfolio.tenant_id == principal.tenant_id)
                    .with_for_update()
                )
                if portfolio is None or portfolio.status != "active":
                    raise PortfolioError("PORTFOLIO_NOT_ACTIVE")
                mandate = self._active_mandate(db, principal.tenant_id, portfolio_id, now)
                if mandate is None:
                    raise PortfolioError("ACTIVE_MANDATE_REQUIRED")
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
                cash, reserved = self._cash_balances(db, principal.tenant_id, portfolio_id, now)
                if amount > cash - reserved:
                    raise PortfolioError("INSUFFICIENT_AVAILABLE_CASH")
                row = CashReservation(
                    tenant_id=principal.tenant_id,
                    portfolio_id=portfolio_id,
                    mandate_id=mandate.id,
                    idempotency_key_hash=key_hash,
                    currency=portfolio.base_currency,
                    amount=amount,
                    status="active",
                    purpose_digest=purpose_digest,
                    expires_at=now + ttl,
                )
                db.add(row)
                portfolio.balance_version += 1
                db.flush()
                db.expunge(row)
                return row
        except IntegrityError:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
                existing = db.scalar(
                    select(CashReservation).where(
                        CashReservation.tenant_id == principal.tenant_id,
                        CashReservation.portfolio_id == portfolio_id,
                        CashReservation.idempotency_key_hash == key_hash,
                    )
                )
                if (
                    existing is None
                    or existing.amount != amount
                    or existing.purpose_digest != purpose_digest
                ):
                    raise PortfolioError("IDEMPOTENCY_PAYLOAD_MISMATCH") from None
                db.expunge(existing)
                return existing

    def release_reservation(self, *, principal: IdentityPrincipal, reservation_id: UUID) -> str:
        self._authorize(principal, write=True)
        now = self._now()
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
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

    def list_portfolios(self, *, principal: IdentityPrincipal) -> tuple[PortfolioOverview, ...]:
        self._authorize(principal)
        now = self._now()
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            rows = db.scalars(
                select(Portfolio)
                .where(Portfolio.tenant_id == principal.tenant_id)
                .order_by(Portfolio.created_at)
            ).all()
            result = []
            for row in rows:
                mandate = self._active_mandate(db, principal.tenant_id, row.id, now)
                cash, reserved = self._cash_balances(db, principal.tenant_id, row.id, now)
                result.append(
                    PortfolioOverview(
                        id=row.id,
                        name=row.name,
                        base_currency=row.base_currency,
                        status=row.status,
                        effective_profile=mandate.profile if mandate else None,
                        mode=mandate.mode if mandate else None,
                        cash=cash,
                        reserved_cash=reserved,
                        available_cash=cash - reserved,
                    )
                )
            return tuple(result)

    def capture_trusted_snapshot(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        valuation: TrustedValuation,
    ) -> PortfolioSnapshot:
        self._authorize(principal, write=True)
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            now = self._database_now(db)
            portfolio = db.scalar(
                select(Portfolio)
                .where(Portfolio.id == portfolio_id, Portfolio.tenant_id == principal.tenant_id)
                .with_for_update()
            )
            if portfolio is None:
                raise PortfolioError("PORTFOLIO_NOT_FOUND")
            mandate = self._active_mandate(db, principal.tenant_id, portfolio_id, now)
            if mandate is None or mandate.mode not in {
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
            high_water_nav = max(
                nav_per_unit,
                previous.high_water_nav if previous else nav_per_unit,
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
            if daily_baseline is not None and daily_baseline.nav_per_unit > nav_per_unit:
                daily_drawdown = (
                    daily_baseline.nav_per_unit - nav_per_unit
                ) / daily_baseline.nav_per_unit
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
    def _active_mandate(
        db: Session, tenant_id: UUID, portfolio_id: UUID, at: datetime
    ) -> PortfolioMandate | None:
        current = PortfolioService._current_mandate(db, tenant_id, portfolio_id)
        if current is None or current.status != "active" or current.effective_at > at:
            return None
        return current

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
