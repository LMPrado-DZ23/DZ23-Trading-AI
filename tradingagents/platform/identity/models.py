"""SQLAlchemy 2 identity, tenancy, RBAC, session, reset, and audit models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Preserve aware UTC values on PostgreSQL and SQLite alike."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        normalized = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class MembershipRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    AUDITOR = "auditor"


class Tenant(Base):
    __tablename__ = "identity_tenants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (CheckConstraint("length(slug) >= 3", name="ck_identity_tenant_slug_length"),)


class User(Base):
    __tablename__ = "identity_users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    auth_subject: Mapped[UUID | None] = mapped_column(nullable=True, unique=True)
    auth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    auth_locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "failed_login_attempts >= 0", name="ck_identity_user_failed_login_nonnegative"
        ),
    )


class Membership(Base):
    __tablename__ = "identity_memberships"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_tenants.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_identity_membership_tenant_user"),
        UniqueConstraint(
            "id", "tenant_id", "user_id", name="uq_identity_membership_identity_binding"
        ),
        CheckConstraint(
            "role IN ('owner','admin','operator','viewer','auditor')",
            name="ck_identity_membership_role",
        ),
        Index("ix_identity_membership_tenant_role", "tenant_id", "role"),
        Index("ix_identity_membership_user", "user_id"),
    )


class UserSession(Base):
    __tablename__ = "identity_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    membership_id: Mapped[UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    mfa_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    mfa_method: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "tenant_id", "user_id"],
            [
                "identity_memberships.id",
                "identity_memberships.tenant_id",
                "identity_memberships.user_id",
            ],
            ondelete="RESTRICT",
            name="fk_identity_session_membership_binding",
        ),
        Index("ix_identity_session_tenant_user", "tenant_id", "user_id"),
        Index("ix_identity_session_active_lookup", "tenant_id", "token_hash", "revoked_at"),
        CheckConstraint(
            "mfa_method IS NULL OR mfa_method IN ('totp','recovery_code')",
            name="ck_identity_session_mfa_method",
        ),
    )


class PasswordResetToken(Base):
    __tablename__ = "identity_password_reset_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_identity_reset_membership",
        ),
        Index("ix_identity_reset_tenant_hash", "tenant_id", "token_hash"),
    )


class EmailVerificationToken(Base):
    __tablename__ = "identity_email_verification_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_identity_verification_membership",
        ),
        Index("ix_identity_verification_tenant_hash", "tenant_id", "token_hash"),
    )


class AuditEvent(Base):
    __tablename__ = "identity_audit_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_tenants.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_identity_audit_tenant_time", "tenant_id", "occurred_at"),)


def _reject_audit_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("audit events are append-only")


event.listen(AuditEvent, "before_update", _reject_audit_mutation)
event.listen(AuditEvent, "before_delete", _reject_audit_mutation)
