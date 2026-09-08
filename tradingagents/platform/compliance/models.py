"""SQLAlchemy models for legal consent, LGPD requests, and MFA."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradingagents.platform.identity.models import Base, UTCDateTime, utc_now


class ConsentDecision(StrEnum):
    ACCEPTED = "accepted"
    WITHDRAWN = "withdrawn"


class PrivacyRequestKind(StrEnum):
    EXPORT = "export"
    DELETE = "delete"


class PrivacyRequestStatus(StrEnum):
    REQUESTED = "requested"
    IDENTITY_VERIFIED = "identity_verified"
    PROCESSING = "processing"
    EXPORT_READY = "export_ready"
    DELETION_SCHEDULED = "deletion_scheduled"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class MfaCredentialStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class TermsDocument(Base):
    __tablename__ = "compliance_terms_documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_type: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        UniqueConstraint("document_type", "version", name="uq_compliance_terms_type_version"),
        CheckConstraint("length(document_type) >= 2", name="ck_compliance_terms_type_length"),
        CheckConstraint("length(content_sha256) = 64", name="ck_compliance_terms_sha_length"),
    )


class ConsentRecord(Base):
    __tablename__ = "compliance_consent_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    terms_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("compliance_terms_documents.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_compliance_consent_membership",
        ),
        CheckConstraint(
            "decision IN ('accepted','withdrawn')", name="ck_compliance_consent_decision"
        ),
        CheckConstraint("length(evidence_sha256) = 64", name="ck_compliance_consent_sha"),
        Index("ix_compliance_consent_tenant_user_time", "tenant_id", "user_id", "recorded_at"),
    )


class PrivacyRequest(Base):
    __tablename__ = "compliance_privacy_requests"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    artifact_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    rejection_code: Mapped[str | None] = mapped_column(String(80), nullable=True)

    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_compliance_privacy_tenant_binding"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_compliance_privacy_membership",
        ),
        CheckConstraint("kind IN ('export','delete')", name="ck_compliance_privacy_kind"),
        CheckConstraint(
            "status IN ('requested','identity_verified','processing','export_ready',"
            "'deletion_scheduled','fulfilled','rejected','expired','cancelled')",
            name="ck_compliance_privacy_status",
        ),
        Index("ix_compliance_privacy_tenant_user", "tenant_id", "user_id", "requested_at"),
        Index("ix_compliance_privacy_due", "status", "due_at"),
    )


class PrivacyRequestEvent(Base):
    __tablename__ = "compliance_privacy_request_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["compliance_privacy_requests.id", "compliance_privacy_requests.tenant_id"],
            ondelete="RESTRICT",
            name="fk_compliance_privacy_event_request",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_compliance_privacy_event_actor",
        ),
        Index("ix_compliance_privacy_event_tenant_time", "tenant_id", "occurred_at"),
    )


class MfaCredential(Base):
    __tablename__ = "compliance_mfa_credentials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_key_id: Mapped[str] = mapped_column(String(160), nullable=False)
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    last_accepted_counter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_compliance_mfa_membership",
        ),
        UniqueConstraint("tenant_id", "user_id", name="uq_compliance_mfa_tenant_user"),
        UniqueConstraint("id", "tenant_id", "user_id", name="uq_compliance_mfa_binding"),
        CheckConstraint(
            "status IN ('pending','active','disabled')", name="ck_compliance_mfa_status"
        ),
        Index("ix_compliance_mfa_tenant_status", "tenant_id", "status"),
    )


class RecoveryCode(Base):
    __tablename__ = "compliance_recovery_codes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    credential_id: Mapped[UUID] = mapped_column(nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["credential_id", "tenant_id", "user_id"],
            [
                "compliance_mfa_credentials.id",
                "compliance_mfa_credentials.tenant_id",
                "compliance_mfa_credentials.user_id",
            ],
            ondelete="RESTRICT",
            name="fk_compliance_recovery_mfa",
        ),
        UniqueConstraint("credential_id", "code_hash", name="uq_compliance_recovery_hash"),
        Index("ix_compliance_recovery_tenant_user", "tenant_id", "user_id"),
    )


def _reject_append_only_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("compliance evidence is append-only")


for _model in (TermsDocument, ConsentRecord, PrivacyRequestEvent):
    event.listen(_model, "before_update", _reject_append_only_mutation)
    event.listen(_model, "before_delete", _reject_append_only_mutation)
