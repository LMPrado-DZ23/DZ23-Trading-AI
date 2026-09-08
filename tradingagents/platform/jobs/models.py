"""Durable, tenant-owned analysis job records."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradingagents.platform.identity.models import Base, UTCDateTime, utc_now


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=JobStatus.QUEUED)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_schema_version: Mapped[int] = mapped_column(nullable=False)
    run_context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    run_context_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    llm_requests: Mapped[int] = mapped_column(nullable=False, default=0)
    unmetered_llm_requests: Mapped[int] = mapped_column(nullable=False, default=0)
    estimated_cost_microusd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reserved_token_budget: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_cost_budget_microusd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    lease_id: Mapped[UUID | None] = mapped_column(nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_analysis_job_requester_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "idempotency_key_hash", name="uq_job_tenant_idem"),
        CheckConstraint(
            "status IN ('queued','leased','running','cancel_requested','succeeded',"
            "'failed','cancelled')",
            name="ck_analysis_job_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_analysis_job_attempts"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens = input_tokens + output_tokens",
            name="ck_analysis_job_token_usage",
        ),
        CheckConstraint(
            "llm_requests >= 0 AND unmetered_llm_requests BETWEEN 0 AND llm_requests",
            name="ck_analysis_job_request_usage",
        ),
        CheckConstraint(
            "estimated_cost_microusd IS NULL OR estimated_cost_microusd >= 0",
            name="ck_analysis_job_cost_usage",
        ),
        CheckConstraint("reserved_token_budget > 0", name="ck_analysis_job_token_budget"),
        CheckConstraint(
            "reserved_cost_budget_microusd IS NULL OR reserved_cost_budget_microusd > 0",
            name="ck_analysis_job_cost_budget",
        ),
        CheckConstraint("max_attempts BETWEEN 1 AND 10", name="ck_analysis_job_max_attempts"),
        CheckConstraint(
            "length(payload_digest) = 64 AND length(request_digest) = 64",
            name="ck_analysis_job_digests",
        ),
        Index("ix_analysis_job_tenant_created", "tenant_id", "created_at"),
        Index("ix_analysis_job_claim", "status", "available_at", "created_at"),
        Index("ix_analysis_job_lease", "lease_expires_at"),
    )
