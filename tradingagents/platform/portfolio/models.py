"""SQLAlchemy persistence for the tenant-owned portfolio risk domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradingagents.platform.identity.models import Base, UTCDateTime, utc_now


class SuitabilityRecord(Base):
    __tablename__ = "portfolio_suitability_assessments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    requested_profile: Mapped[str] = mapped_column(String(24), nullable=False)
    maximum_profile: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_profile: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[int] = mapped_column(nullable=False)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_portfolio_suitability_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "user_id", "version", name="uq_suitability_version"),
        UniqueConstraint("tenant_id", "id", name="uq_suitability_tenant_id"),
        CheckConstraint("version >= 1", name="ck_suitability_version"),
        CheckConstraint("policy_version >= 1", name="ck_suitability_policy_version"),
        CheckConstraint(
            "requested_profile IN ('preservation','conservative','moderate','growth','aggressive')",
            name="ck_suitability_requested_profile",
        ),
        CheckConstraint(
            "maximum_profile IN ('preservation','conservative','moderate','growth','aggressive')",
            name="ck_suitability_maximum_profile",
        ),
        CheckConstraint(
            "effective_profile IN ('preservation','conservative','moderate','growth','aggressive')",
            name="ck_suitability_effective_profile",
        ),
        CheckConstraint("status IN ('valid','expired','revoked')", name="ck_suitability_status"),
        CheckConstraint("length(digest) = 64", name="ck_suitability_digest"),
        CheckConstraint("expires_at > assessed_at", name="ck_suitability_window"),
        Index("ix_suitability_tenant_user", "tenant_id", "user_id", "version"),
    )


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    balance_version: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_portfolio_owner_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_portfolio_tenant_id"),
        UniqueConstraint("tenant_id", "owner_user_id", "name", name="uq_portfolio_owner_name"),
        CheckConstraint("status IN ('active','paused','closed')", name="ck_portfolio_status"),
        CheckConstraint("balance_version >= 0", name="ck_portfolio_balance_version"),
        CheckConstraint("base_currency = upper(base_currency)", name="ck_portfolio_currency_upper"),
        Index("ix_portfolio_tenant_owner", "tenant_id", "owner_user_id"),
    )


class PortfolioMandate(Base):
    __tablename__ = "portfolio_mandates"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(nullable=False)
    suitability_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    profile: Mapped[str] = mapped_column(String(24), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    policy_version: Mapped[int] = mapped_column(nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    effective_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["portfolio_id", "tenant_id"],
            ["portfolios.id", "portfolios.tenant_id"],
            name="fk_mandate_portfolio",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["suitability_id", "tenant_id"],
            ["portfolio_suitability_assessments.id", "portfolio_suitability_assessments.tenant_id"],
            name="fk_mandate_suitability",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_mandate_creator_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "portfolio_id", "version", name="uq_mandate_version"),
        UniqueConstraint("tenant_id", "id", name="uq_mandate_tenant_id"),
        CheckConstraint("version >= 1", name="ck_mandate_version"),
        CheckConstraint("policy_version >= 1", name="ck_mandate_policy_version"),
        CheckConstraint(
            "profile IN ('preservation','conservative','moderate','growth','aggressive')",
            name="ck_mandate_profile",
        ),
        CheckConstraint(
            "mode IN ('backtest','shadow','paper','live_supervised','live_automated')",
            name="ck_mandate_mode",
        ),
        CheckConstraint(
            "status IN ('draft','active','paused','circuit_broken','closed')",
            name="ck_mandate_status",
        ),
        CheckConstraint(
            "length(policy_digest) = 64 AND length(digest) = 64",
            name="ck_mandate_digests",
        ),
        Index("ix_mandate_portfolio_status", "tenant_id", "portfolio_id", "status"),
    )


class LedgerTransaction(Base):
    __tablename__ = "portfolio_ledger_transactions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["portfolio_id", "tenant_id"],
            ["portfolios.id", "portfolios.tenant_id"],
            name="fk_ledger_transaction_portfolio",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_ledger_transaction_actor",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_ledger_transaction_tenant_id"),
        UniqueConstraint(
            "tenant_id", "portfolio_id", "idempotency_key_hash", name="uq_ledger_idempotency"
        ),
        CheckConstraint("amount > 0", name="ck_ledger_transaction_amount"),
        CheckConstraint("length(digest) = 64", name="ck_ledger_transaction_digest"),
        CheckConstraint("currency = upper(currency)", name="ck_ledger_transaction_currency"),
        Index("ix_ledger_transaction_portfolio_time", "tenant_id", "portfolio_id", "occurred_at"),
    )


class LedgerPosting(Base):
    __tablename__ = "portfolio_ledger_postings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    transaction_id: Mapped[UUID] = mapped_column(nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(nullable=False)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    asset: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["transaction_id", "tenant_id"],
            ["portfolio_ledger_transactions.id", "portfolio_ledger_transactions.tenant_id"],
            name="fk_ledger_posting_transaction",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "tenant_id"],
            ["portfolios.id", "portfolios.tenant_id"],
            name="fk_ledger_posting_portfolio",
            ondelete="RESTRICT",
        ),
        CheckConstraint("quantity <> 0", name="ck_ledger_posting_quantity"),
        Index(
            "ix_ledger_posting_portfolio_account", "tenant_id", "portfolio_id", "account", "asset"
        ),
    )


class CashReservation(Base):
    __tablename__ = "portfolio_cash_reservations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(nullable=False)
    mandate_id: Mapped[UUID] = mapped_column(nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["portfolio_id", "tenant_id"],
            ["portfolios.id", "portfolios.tenant_id"],
            name="fk_reservation_portfolio",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["mandate_id", "tenant_id"],
            ["portfolio_mandates.id", "portfolio_mandates.tenant_id"],
            name="fk_reservation_mandate",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "portfolio_id", "idempotency_key_hash", name="uq_reservation_idempotency"
        ),
        CheckConstraint("amount > 0", name="ck_reservation_amount"),
        CheckConstraint(
            "status IN ('active','consumed','released','expired')", name="ck_reservation_status"
        ),
        CheckConstraint("length(purpose_digest) = 64", name="ck_reservation_digest"),
        Index("ix_reservation_active", "tenant_id", "portfolio_id", "status", "expires_at"),
    )


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False, default=0)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["portfolio_id", "tenant_id"],
            ["portfolios.id", "portfolios.tenant_id"],
            name="fk_position_portfolio",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "portfolio_id", "symbol", name="uq_position_symbol"),
        CheckConstraint("quantity >= 0", name="ck_position_quantity"),
        CheckConstraint("average_cost >= 0", name="ck_position_average_cost"),
        CheckConstraint("version >= 1", name="ck_position_version"),
        Index("ix_position_portfolio", "tenant_id", "portfolio_id"),
    )


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    reserved_cash: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    market_value: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    high_water_mark: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    drawdown: Mapped[Decimal] = mapped_column(Numeric(18, 12), nullable=False)
    positions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    prices: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["portfolio_id", "tenant_id"],
            ["portfolios.id", "portfolios.tenant_id"],
            name="fk_snapshot_portfolio",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "portfolio_id", "sequence", name="uq_snapshot_sequence"),
        CheckConstraint("sequence >= 1", name="ck_snapshot_sequence"),
        CheckConstraint("cash >= 0 AND reserved_cash >= 0", name="ck_snapshot_cash"),
        CheckConstraint("equity >= 0 AND high_water_mark >= 0", name="ck_snapshot_equity"),
        CheckConstraint("drawdown >= 0 AND drawdown <= 1", name="ck_snapshot_drawdown"),
        CheckConstraint("length(digest) = 64", name="ck_snapshot_digest"),
        Index("ix_snapshot_portfolio_time", "tenant_id", "portfolio_id", "captured_at"),
    )


class CircuitBreaker(Base):
    __tablename__ = "portfolio_circuit_breakers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(nullable=False)
    mandate_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_value: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    threshold_value: Mapped[Decimal] = mapped_column(Numeric(36, 12), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reset_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reset_by_user_id: Mapped[UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["portfolio_id", "tenant_id"],
            ["portfolios.id", "portfolios.tenant_id"],
            name="fk_breaker_portfolio",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["mandate_id", "tenant_id"],
            ["portfolio_mandates.id", "portfolio_mandates.tenant_id"],
            name="fk_breaker_mandate",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('open','reset')", name="ck_breaker_status"),
        CheckConstraint("length(evidence_digest) = 64", name="ck_breaker_digest"),
        Index("ix_breaker_open", "tenant_id", "portfolio_id", "status"),
    )


def _reject_append_only_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("record is append-only")


for _append_only_type in (
    SuitabilityRecord,
    PortfolioMandate,
    LedgerTransaction,
    LedgerPosting,
    PortfolioSnapshot,
):
    event.listen(_append_only_type, "before_update", _reject_append_only_mutation)
    event.listen(_append_only_type, "before_delete", _reject_append_only_mutation)
