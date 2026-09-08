"""Encrypted tenant-owned LLM provider connections."""

from __future__ import annotations

from datetime import datetime
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


class LlmConnection(Base):
    __tablename__ = "llm_connections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    label_normalized: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    active_credential_version: Mapped[int] = mapped_column(nullable=False, default=1)
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_llm_connection_creator_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_llm_connection_tenant_binding"),
        UniqueConstraint("tenant_id", "label_normalized", name="uq_llm_connection_tenant_label"),
        CheckConstraint("status IN ('ready','disabled')", name="ck_llm_connection_status"),
        CheckConstraint(
            "active_credential_version >= 1",
            name="ck_llm_credential_version_positive",
        ),
        Index("ix_llm_connection_tenant_status", "tenant_id", "status"),
    )


class LlmCredential(Base):
    __tablename__ = "llm_credentials"

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
            ["llm_connections.id", "llm_connections.tenant_id"],
            name="fk_llm_credential_connection",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_llm_credential_creator_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("connection_id", "version", name="uq_llm_credential_connection_version"),
        UniqueConstraint("tenant_id", "idempotency_key_hash", name="uq_llm_credential_tenant_idem"),
        CheckConstraint("version >= 1", name="ck_llm_credential_version_row"),
        CheckConstraint("length(request_digest) = 64", name="ck_llm_request_digest"),
        CheckConstraint("length(wrapped_key_nonce) IN (0, 12)", name="ck_llm_wrapped_key_nonce"),
        CheckConstraint("length(wrapped_key) BETWEEN 1 AND 8192", name="ck_llm_wrapped_key"),
        CheckConstraint("length(payload_nonce) = 12", name="ck_llm_payload_nonce"),
        Index("ix_llm_credential_tenant_connection", "tenant_id", "connection_id"),
    )


class LlmCredentialAccessEvent(Base):
    __tablename__ = "llm_credential_access_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    connection_id: Mapped[UUID] = mapped_column(nullable=False)
    credential_version: Mapped[int] = mapped_column(nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "tenant_id"],
            ["llm_connections.id", "llm_connections.tenant_id"],
            name="fk_llm_access_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_llm_access_actor_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("purpose IN ('analysis','rotation')", name="ck_llm_access_purpose"),
        CheckConstraint("outcome IN ('allowed','denied','failed')", name="ck_llm_access_outcome"),
        Index("ix_llm_access_tenant_time", "tenant_id", "occurred_at"),
    )


def _reject_event_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("LLM credential access events are append-only")


event.listen(LlmCredentialAccessEvent, "before_update", _reject_event_mutation)
event.listen(LlmCredentialAccessEvent, "before_delete", _reject_event_mutation)
