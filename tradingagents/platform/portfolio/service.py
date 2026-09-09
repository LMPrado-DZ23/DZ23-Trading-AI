"""Transactional application service for deterministic portfolio risk controls."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
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
from tradingagents.platform.portfolio.policies import (
    POLICY_VERSION,
    SizingInput,
    SizingResult,
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
        now = self._now()
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            portfolio = db.scalar(
                select(Portfolio)
                .where(Portfolio.id == portfolio_id, Portfolio.tenant_id == principal.tenant_id)
                .with_for_update()
            )
            if portfolio is None:
                raise PortfolioError("PORTFOLIO_NOT_FOUND")
            suitability = db.scalar(
                select(SuitabilityRecord).where(
                    SuitabilityRecord.id == suitability_id,
                    SuitabilityRecord.tenant_id == principal.tenant_id,
                    SuitabilityRecord.user_id == portfolio.owner_user_id,
                )
            )
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
        return self._record_cash_transaction(
            principal=principal,
            portfolio_id=portfolio_id,
            amount=amount,
            idempotency_key=idempotency_key,
            event_type="funding",
            reference_type="deposit",
            reference_id=reference_id,
            cash_direction=1,
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
        now = self._now()
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            portfolio = db.scalar(
                select(Portfolio)
                .where(
                    Portfolio.id == portfolio_id,
                    Portfolio.tenant_id == principal.tenant_id,
                )
                .with_for_update()
            )
            if portfolio is None:
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
                suitability = db.scalar(
                    select(SuitabilityRecord).where(
                        SuitabilityRecord.id == current.suitability_id,
                        SuitabilityRecord.tenant_id == principal.tenant_id,
                        SuitabilityRecord.status == "valid",
                        SuitabilityRecord.expires_at > now,
                    )
                )
                if suitability is None:
                    raise PortfolioError("SUITABILITY_REQUIRED")
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
            db.expunge(row)
            return row

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
    ) -> LedgerTransaction:
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
                payload = {
                    "tenant_id": str(principal.tenant_id),
                    "portfolio_id": str(portfolio_id),
                    "event_type": event_type,
                    "reference_type": reference_type,
                    "reference_id": reference_id,
                    "currency": portfolio.base_currency,
                    "amount": str(amount),
                    "cash_direction": cash_direction,
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
                if (
                    existing.amount != amount
                    or existing.event_type != event_type
                    or existing.reference_type != reference_type
                    or existing.reference_id != reference_id[:128]
                ):
                    raise PortfolioError("IDEMPOTENCY_PAYLOAD_MISMATCH") from None
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

    def deterministic_size(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        conviction: Decimal,
        price: Decimal,
        realized_annual_volatility: Decimal,
        average_daily_notional: Decimal,
        current_weight: Decimal = _ZERO,
        trading_cost_bps: Decimal = _ZERO,
        asset_class: str = "crypto",
    ) -> SizingResult:
        self._authorize(principal, write=True)
        now = self._now()
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            portfolio = db.scalar(
                select(Portfolio).where(
                    Portfolio.id == portfolio_id,
                    Portfolio.tenant_id == principal.tenant_id,
                )
            )
            mandate = self._active_mandate(db, principal.tenant_id, portfolio_id, now)
            if portfolio is None or mandate is None:
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
            latest = db.scalar(
                select(PortfolioSnapshot)
                .where(
                    PortfolioSnapshot.tenant_id == principal.tenant_id,
                    PortfolioSnapshot.portfolio_id == portfolio_id,
                )
                .order_by(PortfolioSnapshot.sequence.desc())
                .limit(1)
            )
            capital = latest.equity if latest else cash
            if capital <= 0:
                raise PortfolioError("PORTFOLIO_HAS_NO_CAPITAL")
            return size_position(
                policy_for(RiskProfile(mandate.profile)),
                SizingInput(
                    capital=capital,
                    available_cash=cash - reserved,
                    price=price,
                    conviction=conviction,
                    realized_annual_volatility=realized_annual_volatility,
                    average_daily_notional=average_daily_notional,
                    current_weight=current_weight,
                    trading_cost_bps=trading_cost_bps,
                    asset_class=asset_class,
                ),
            )

    def capture_snapshot(
        self,
        *,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        prices: dict[str, Decimal],
        captured_at: datetime | None = None,
    ) -> PortfolioSnapshot:
        self._authorize(principal, write=True)
        now = captured_at or self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise PortfolioError("SNAPSHOT_TIME_INVALID")
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            portfolio = db.scalar(
                select(Portfolio)
                .where(Portfolio.id == portfolio_id, Portfolio.tenant_id == principal.tenant_id)
                .with_for_update()
            )
            if portfolio is None:
                raise PortfolioError("PORTFOLIO_NOT_FOUND")
            mandate = self._active_mandate(db, principal.tenant_id, portfolio_id, now)
            positions = db.scalars(
                select(PortfolioPosition).where(
                    PortfolioPosition.tenant_id == principal.tenant_id,
                    PortfolioPosition.portfolio_id == portfolio_id,
                )
            ).all()
            normalized_prices: dict[str, str] = {}
            market_value = _ZERO
            unrealized = _ZERO
            realized = _ZERO
            position_payload: dict[str, Any] = {}
            for position in positions:
                price = prices.get(position.symbol)
                self._decimal("price", price, positive=True)  # type: ignore[arg-type]
                normalized_prices[position.symbol] = str(price)
                value = position.quantity * price
                market_value += value
                unrealized += (price - position.average_cost) * position.quantity
                realized += position.realized_pnl
                position_payload[position.symbol] = {
                    "quantity": str(position.quantity),
                    "average_cost": str(position.average_cost),
                    "market_value": str(value),
                }
            cash, reserved = self._cash_balances(db, principal.tenant_id, portfolio_id, now)
            equity = cash + market_value
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
            high_water = max(equity, previous.high_water_mark if previous else equity)
            drawdown = (high_water - equity) / high_water if high_water > 0 else _ZERO
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
                "positions": position_payload,
                "prices": normalized_prices,
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
                positions=position_payload,
                prices=normalized_prices,
                digest=self._canonical_digest(payload),
                captured_at=now,
            )
            db.add(row)
            if mandate is not None:
                policy = policy_for(RiskProfile(mandate.profile))
                daily_drawdown = _ZERO
                if (
                    previous is not None
                    and previous.captured_at.date() == now.date()
                    and previous.equity > 0
                    and equity < previous.equity
                ):
                    daily_drawdown = (previous.equity - equity) / previous.equity
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
        return db.scalar(
            select(PortfolioMandate)
            .where(
                PortfolioMandate.tenant_id == tenant_id,
                PortfolioMandate.portfolio_id == portfolio_id,
            )
            .order_by(PortfolioMandate.version.desc())
            .limit(1)
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
