"""SQLAlchemy models for encrypted tenant exchange connections."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradingagents.platform.identity.models import Base, UTCDateTime, utc_now


class ConnectionStatus(StrEnum):
    PENDING_DIAGNOSTIC = "pending_diagnostic"
    READY_READONLY = "ready_readonly"
    ATTENTION_REQUIRED = "attention_required"
    REJECTED = "rejected"
    DISABLED = "disabled"


class BrokerConnection(Base):
    __tablename__ = "broker_connections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    label_normalized: Mapped[str] = mapped_column(String(80), nullable=False)
    exchange_slug: Mapped[str] = mapped_column(String(40), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ConnectionStatus.PENDING_DIAGNOSTIC.value
    )
    active_credential_version: Mapped[int] = mapped_column(nullable=False, default=1)
    capability_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    ccxt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    permission_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unverifiable"
    )
    last_diagnostic_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    diagnostic_lease_id: Mapped[UUID | None] = mapped_column(nullable=True)
    diagnostic_lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_broker_connection_creator_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_broker_connection_tenant_binding"),
        UniqueConstraint(
            "tenant_id", "label_normalized", name="uq_broker_connection_tenant_label"
        ),
        CheckConstraint(
            "environment IN ('testnet','live')", name="ck_broker_connection_environment"
        ),
        CheckConstraint(
            "status IN ('pending_diagnostic','ready_readonly','attention_required',"
            "'rejected','disabled')",
            name="ck_broker_connection_status",
        ),
        CheckConstraint(
            "permission_status IN ('verified_safe','unsafe','unverifiable','not_supported')",
            name="ck_broker_connection_permission_status",
        ),
        CheckConstraint(
            "length(capability_digest) = 64", name="ck_broker_connection_capability_digest"
        ),
        CheckConstraint(
            "active_credential_version >= 1", name="ck_broker_credential_version_positive"
        ),
        Index("ix_broker_connection_tenant_status", "tenant_id", "status"),
    )


class BrokerCredential(Base):
    __tablename__ = "broker_credentials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    connection_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    key_id: Mapped[str] = mapped_column(String(160), nullable=False)
    wrapped_key_nonce: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    payload_nonce: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "tenant_id"],
            ["broker_connections.id", "broker_connections.tenant_id"],
            name="fk_broker_credential_connection",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_broker_credential_creator_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "connection_id", "version", name="uq_broker_credential_connection_version"
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key_hash",
            name="uq_broker_credential_tenant_idempotency",
        ),
        CheckConstraint("version >= 1", name="ck_broker_credential_version_positive_row"),
        CheckConstraint(
            "length(request_digest) = 64", name="ck_broker_credential_request_digest"
        ),
        CheckConstraint(
            "length(wrapped_key_nonce) IN (0, 12)",
            name="ck_broker_wrapped_key_nonce_length",
        ),
        CheckConstraint(
            "length(wrapped_key) BETWEEN 1 AND 8192",
            name="ck_broker_wrapped_key_length",
        ),
        CheckConstraint(
            "length(payload_nonce) = 12", name="ck_broker_payload_nonce_length"
        ),
        Index("ix_broker_credential_tenant_connection", "tenant_id", "connection_id"),
    )


class BrokerCredentialAccessEvent(Base):
    __tablename__ = "broker_credential_access_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    connection_id: Mapped[UUID] = mapped_column(nullable=False)
    credential_version: Mapped[int] = mapped_column(nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "tenant_id"],
            ["broker_connections.id", "broker_connections.tenant_id"],
            name="fk_broker_access_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_broker_access_actor_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "purpose IN ('diagnostic','rotation','execution')",
            name="ck_broker_access_purpose",
        ),
        CheckConstraint(
            "outcome IN ('allowed','denied','failed')", name="ck_broker_access_outcome"
        ),
        Index("ix_broker_access_tenant_time", "tenant_id", "occurred_at"),
    )


def _reject_access_event_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("credential access events are append-only")


event.listen(BrokerCredentialAccessEvent, "before_update", _reject_access_event_mutation)
event.listen(BrokerCredentialAccessEvent, "before_delete", _reject_access_event_mutation)
