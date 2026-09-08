"""Transactional job queue with leases, retry, cancellation and immutable context."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.dataflows.redact import redact_secrets
from tradingagents.dataflows.symbol_utils import crypto_base
from tradingagents.default_config import PLATFORM_DEFAULT_CONFIG
from tradingagents.platform.contracts import RunMode
from tradingagents.platform.identity.service import IdentityPrincipal
from tradingagents.platform.jobs.models import AnalysisJob, JobStatus, JobUsageRecord
from tradingagents.platform.llm_connections import LlmConnectionError
from tradingagents.platform.llm_provenance import LlmReferenceSnapshot
from tradingagents.platform.run_context import (
    WORKFLOW_SCHEMA_VERSION,
    AnalysisRunContext,
    calendar_schedule_digest,
    decision_cutoff,
)
from tradingagents.platform.usage import JobUsage, PricingCatalog

_ACTIVE = (
    JobStatus.QUEUED.value,
    JobStatus.LEASED.value,
    JobStatus.RUNNING.value,
    JobStatus.CANCEL_REQUESTED.value,
)
_PUBLIC_CONFIG_KEYS = frozenset(
    {
        "output_language",
        "max_debate_rounds",
        "max_risk_discuss_rounds",
        "max_recur_limit",
        "temperature",
        "llm_max_retries",
        "max_tokens",
        "news_article_limit",
        "global_news_article_limit",
        "global_news_lookback_days",
        "global_news_queries",
        "data_vendors",
        "tool_vendors",
        "benchmark_ticker",
        "benchmark_map",
        "checkpoint_enabled",
    }
)
_CONFIG_INT_BOUNDS = {
    "max_debate_rounds": (0, 10),
    "max_risk_discuss_rounds": (0, 10),
    "max_recur_limit": (10, 500),
    "news_article_limit": (1, 100),
    "global_news_article_limit": (1, 100),
    "global_news_lookback_days": (1, 365),
}
_VENDOR_VALUE_RE = re.compile(r"^[a-z0-9_,-]{1,160}$")
_TICKER_VALUE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,5})?$")
_BVMF_TICKER_RE = re.compile(r"^[A-Z]{4}[0-9A-Z]{1,4}\.SA$")
_BENCHMARK_TICKER_RE = re.compile(r"^[A-Za-z0-9._^=-]{1,64}$")


class JobError(RuntimeError):
    pass


class LlmReferenceValidator(Protocol):
    def assert_reference(
        self,
        *,
        tenant_id: UUID,
        connection_id: UUID,
        credential_version: int,
        provider: str,
        model: str,
    ) -> LlmReferenceSnapshot: ...


class AnalysisJobCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(min_length=1, max_length=64)
    asset_type: str = Field(pattern=r"^(stock|crypto)$")
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    market_calendar: str = Field(default="", pattern=r"^(|24/7|XNYS|BVMF)$")
    mode: RunMode
    provider: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    model: str = Field(min_length=1, max_length=160)
    llm_connection_id: UUID
    llm_credential_version: int = Field(ge=1)
    selected_analysts: tuple[str, ...] = Field(min_length=1, max_length=4)
    token_budget: int = Field(default=250_000, ge=1_000, le=2_000_000)
    cost_budget_microusd: int | None = Field(default=None, ge=1_000, le=1_000_000_000)
    max_execution_seconds: int = Field(default=900, ge=30, le=3600)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized or any(ord(character) < 33 for character in normalized):
            raise ValueError("symbol is invalid")
        return normalized

    @model_validator(mode="before")
    @classmethod
    def infer_and_validate_market_calendar(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        value = dict(raw)
        asset_type = value.get("asset_type")
        symbol = str(value.get("symbol", "")).upper()
        recognized_crypto = crypto_base(symbol) is not None
        if (asset_type == "crypto") != recognized_crypto:
            raise ValueError("asset type is incompatible with the recognized symbol universe")
        if asset_type == "crypto":
            expected_calendar = "24/7"
        elif _BVMF_TICKER_RE.fullmatch(symbol):
            expected_calendar = "BVMF"
        elif _TICKER_VALUE_RE.fullmatch(symbol):
            expected_calendar = "XNYS"
        else:
            raise ValueError("symbol is invalid for the supported market calendar contract")
        calendar = value.get("market_calendar") or expected_calendar
        if calendar != expected_calendar:
            raise ValueError("market calendar is incompatible with the asset type")
        try:
            decision_cutoff(
                trade_date=str(value.get("trade_date", "")),
                asset_type=str(asset_type),
                market_calendar=str(calendar),
                mode=RunMode(str(value.get("mode", ""))),
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("trade_date is incompatible with the requested mode") from exc
        value["market_calendar"] = calendar
        value["symbol"] = symbol
        return value

    @field_validator("mode")
    @classmethod
    def reject_capital_modes(cls, value: RunMode) -> RunMode:
        if value in {RunMode.LIVE_SUPERVISED, RunMode.LIVE_AUTOMATED}:
            raise ValueError("analysis jobs cannot directly request a LIVE mode")
        return value

    @field_validator("selected_analysts")
    @classmethod
    def validate_analysts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or set(values) != {"market"}:
            raise ValueError(
                "platform jobs currently support only the market analyst with verified PIT data"
            )
        return values

    @field_validator("config")
    @classmethod
    def validate_public_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        value = dict(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("job config is too large")
        def credential_key(key: object) -> bool:
            normalized = str(key).casefold().replace("-", "_")
            compact = normalized.replace("_", "")
            if normalized in {"api_key", "apikey", "secret", "token", "password", "credential"}:
                return True
            return (
                normalized.endswith(
                ("_api_key", "_secret", "_token", "_password", "_credential")
                )
                or (compact.endswith("token") and not compact.endswith("tokens"))
            )

        pending: list[Any] = [value]
        while pending:
            current = pending.pop()
            if isinstance(current, dict):
                if any(credential_key(key) for key in current):
                    raise ValueError("job config cannot contain credentials")
                pending.extend(current.values())
            elif isinstance(current, (list, tuple)):
                pending.extend(current)
            elif isinstance(current, str) and redact_secrets(current) != current:
                raise ValueError("job config cannot contain credentials")
        if any(part in encoded.casefold() for part in ('"bearer ', '"basic ')):
            raise ValueError("job config cannot contain credentials")
        unknown = set(value) - _PUBLIC_CONFIG_KEYS
        if unknown:
            raise ValueError("job config contains unsupported settings")
        for key, (minimum, maximum) in _CONFIG_INT_BOUNDS.items():
            if key not in value:
                continue
            item = value[key]
            if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
                raise ValueError(f"{key} is outside the supported range")
        if "temperature" in value and value["temperature"] is not None:
            item = value["temperature"]
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError("temperature must be a finite number")
            normalized_temperature = float(item)
            if not math.isfinite(normalized_temperature) or not 0 <= normalized_temperature <= 2:
                raise ValueError("temperature must be between zero and two")
            value["temperature"] = normalized_temperature
        if "output_language" in value:
            language = value["output_language"]
            if (
                not isinstance(language, str)
                or not 2 <= len(language) <= 40
                or any(ord(character) < 32 for character in language)
            ):
                raise ValueError("output_language is invalid")
        for key in ("data_vendors", "tool_vendors"):
            if key not in value:
                continue
            mapping = value[key]
            if not isinstance(mapping, dict) or len(mapping) > 40:
                raise ValueError(f"{key} must be a bounded mapping")
            for vendor_key, vendor_value in mapping.items():
                if (
                    not isinstance(vendor_key, str)
                    or not isinstance(vendor_value, str)
                    or not _VENDOR_VALUE_RE.fullmatch(vendor_value)
                    or not 1 <= len(vendor_key) <= 80
                ):
                    raise ValueError(f"{key} contains an invalid vendor route")
        if "global_news_queries" in value:
            queries = value["global_news_queries"]
            if (
                not isinstance(queries, list)
                or not 1 <= len(queries) <= 20
                or any(
                    not isinstance(query, str)
                    or not 1 <= len(query.strip()) <= 200
                    or any(ord(character) < 32 for character in query)
                    for query in queries
                )
            ):
                raise ValueError("global_news_queries is invalid")
        if (
            "benchmark_ticker" in value
            and value["benchmark_ticker"] is not None
            and (
                not isinstance(value["benchmark_ticker"], str)
                or not _BENCHMARK_TICKER_RE.fullmatch(value["benchmark_ticker"])
            )
        ):
            raise ValueError("benchmark_ticker is invalid")
        if "benchmark_map" in value:
            mapping = value["benchmark_map"]
            if (
                not isinstance(mapping, dict)
                or not 1 <= len(mapping) <= 40
                or any(
                    not isinstance(suffix, str)
                    or len(suffix) > 16
                    or not isinstance(ticker, str)
                    or not _BENCHMARK_TICKER_RE.fullmatch(ticker)
                    for suffix, ticker in mapping.items()
                )
            ):
                raise ValueError("benchmark_map is invalid")
        if "checkpoint_enabled" in value and value["checkpoint_enabled"] is not True:
            raise ValueError("platform analysis jobs require checkpoints")
        retry_value = value.get("llm_max_retries", 0)
        if isinstance(retry_value, bool):
            raise ValueError("analysis worker LLM retries must be zero")
        try:
            retries = int(retry_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("analysis worker LLM retries must be zero") from exc
        if retries != 0:
            raise ValueError("analysis worker LLM retries must be zero")
        value["llm_max_retries"] = 0
        value["checkpoint_enabled"] = True
        return value

    @model_validator(mode="after")
    def bind_per_request_output_cap(self) -> AnalysisJobCommand:
        config = dict(self.config)
        raw_cap = config.get("max_tokens")
        if raw_cap is None or raw_cap == "":
            cap = min(16_384, self.token_budget)
        else:
            if isinstance(raw_cap, bool):
                raise ValueError("max_tokens must be a positive integer")
            try:
                cap = int(raw_cap)
            except (TypeError, ValueError) as exc:
                raise ValueError("max_tokens must be a positive integer") from exc
            if cap <= 0:
                raise ValueError("max_tokens must be a positive integer")
        if cap > self.token_budget:
            raise ValueError("max_tokens cannot exceed the job token budget")
        config["max_tokens"] = cap
        config["llm_max_retries"] = 0
        config["checkpoint_enabled"] = True
        object.__setattr__(self, "config", config)
        return self


def effective_analysis_config(command: AnalysisJobCommand) -> dict[str, Any]:
    """Materialize every public graph knob from an environment-free baseline."""

    config = {
        key: deepcopy(PLATFORM_DEFAULT_CONFIG.get(key))
        for key in _PUBLIC_CONFIG_KEYS
    }
    config.update(deepcopy(command.config))
    return config


@dataclass(frozen=True, slots=True)
class JobView:
    id: UUID
    run_id: UUID
    status: str
    symbol: str
    asset_type: str
    trade_date: str
    mode: str
    attempts: int
    max_attempts: int
    error_code: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    llm_requests: int
    unmetered_llm_requests: int
    estimated_cost_microusd: int | None
    pricing_version: str | None
    token_budget: int
    cost_budget_microusd: int | None
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class JobDetail:
    job: JobView
    result: dict[str, Any] | None
    manifest: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: UUID
    run_id: UUID
    tenant_id: UUID
    requested_by_user_id: UUID
    lease_id: UUID
    lease_owner: str
    lease_expires_at: datetime
    command: AnalysisJobCommand
    run_context: AnalysisRunContext
    attempts: int
    prior_usage: JobUsage
    claimed_at: datetime


class JobService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        idempotency_digest_key: bytes,
        llm_reference_validator: LlmReferenceValidator,
        max_active_jobs_per_tenant: int = 20,
        monthly_token_quota: int = 5_000_000,
        monthly_cost_quota_microusd: int | None = None,
        pricing_catalog_version: str = "44136fa355b3678a",
        pricing_catalog: PricingCatalog | None = None,
    ) -> None:
        if len(idempotency_digest_key) < 32:
            raise ValueError("job idempotency key must contain at least 32 bytes")
        if not 1 <= max_active_jobs_per_tenant <= 1000:
            raise ValueError("active job quota is invalid")
        if not 10_000 <= monthly_token_quota <= 10_000_000_000:
            raise ValueError("monthly token quota is invalid")
        if monthly_cost_quota_microusd is not None and not (
            10_000 <= monthly_cost_quota_microusd <= 1_000_000_000_000
        ):
            raise ValueError("monthly cost quota is invalid")
        if not re.fullmatch(r"[0-9a-f]{16}", pricing_catalog_version):
            raise ValueError("pricing catalog version is invalid")
        self._sessions = session_factory
        self._digest_key = idempotency_digest_key
        self._quota = max_active_jobs_per_tenant
        self._monthly_token_quota = monthly_token_quota
        self._monthly_cost_quota = monthly_cost_quota_microusd
        self._pricing_version = pricing_catalog_version
        self._pricing_catalog = pricing_catalog
        if self._monthly_cost_quota is not None and pricing_catalog is None:
            raise ValueError("monthly cost quota requires an exact pricing catalog")
        self._llm_references = llm_reference_validator

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _db_now(self, db: Session) -> datetime:
        """Use PostgreSQL as the inter-process lease and quota clock authority."""

        if db.bind is not None and db.bind.dialect.name == "postgresql":
            value = db.scalar(select(func.clock_timestamp()))
            if value is None:
                raise JobError("DATABASE_CLOCK_UNAVAILABLE")
            return value.astimezone(UTC)
        return self._now()

    @staticmethod
    def _set_tenant_scope(db: Session, tenant_id: UUID) -> None:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )

    @staticmethod
    def _set_worker_claim_scope(
        db: Session,
        *,
        lease_id: UUID,
        lease_owner: str,
    ) -> None:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT set_config('app.worker_lease_id', :lease_id, true)"),
                {"lease_id": str(lease_id)},
            )
            db.execute(
                text("SELECT set_config('app.worker_lease_owner', :lease_owner, true)"),
                {"lease_owner": lease_owner},
            )

    @staticmethod
    def _lock_tenant_admission(db: Session, tenant_id: UUID) -> None:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            key = int.from_bytes(hashlib.sha256(tenant_id.bytes).digest()[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    @staticmethod
    def _authorize(principal: IdentityPrincipal, *, write: bool = False) -> None:
        allowed = {"owner", "admin", "operator"}
        if not write:
            allowed.add("auditor")
        if principal.role.value not in allowed:
            raise JobError("JOB_FORBIDDEN")

    def enqueue(
        self,
        *,
        principal: IdentityPrincipal,
        idempotency_key: str,
        command: AnalysisJobCommand,
        max_attempts: int = 3,
    ) -> UUID:
        self._authorize(principal, write=True)
        if not 16 <= len(idempotency_key) <= 128 or any(
            ord(character) < 33 or ord(character) > 126 for character in idempotency_key
        ):
            raise JobError("IDEMPOTENCY_KEY_INVALID")
        if not 1 <= max_attempts <= 10:
            raise JobError("MAX_ATTEMPTS_INVALID")
        try:
            provider_snapshot = self._llm_references.assert_reference(
                tenant_id=principal.tenant_id,
                connection_id=command.llm_connection_id,
                credential_version=command.llm_credential_version,
                provider=command.provider,
                model=command.model,
            )
        except LlmConnectionError:
            raise JobError("LLM_CONNECTION_INVALID") from None
        if not isinstance(provider_snapshot, LlmReferenceSnapshot):
            raise JobError("LLM_PROVENANCE_UNAVAILABLE")
        payload = command.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        canonical_provider = json.dumps(
            provider_snapshot.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_digest = hashlib.sha256(canonical.encode()).hexdigest()
        idem_hash = hashlib.sha256(f"{principal.tenant_id}:{idempotency_key}".encode()).hexdigest()
        request_digest = hmac.new(
            self._digest_key,
            f"{principal.tenant_id}:{canonical}:{canonical_provider}".encode(),
            hashlib.sha256,
        ).hexdigest()
        try:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
                self._lock_tenant_admission(db, principal.tenant_id)
                replay = db.scalar(
                    select(AnalysisJob).where(
                        AnalysisJob.tenant_id == principal.tenant_id,
                        AnalysisJob.idempotency_key_hash == idem_hash,
                    )
                )
                if replay is not None:
                    if not hmac.compare_digest(replay.request_digest, request_digest):
                        raise JobError("IDEMPOTENCY_PAYLOAD_MISMATCH")
                    return replay.id
                active_count = db.scalar(
                    select(func.count())
                    .select_from(AnalysisJob)
                    .where(
                        AnalysisJob.tenant_id == principal.tenant_id,
                        AnalysisJob.status.in_(_ACTIVE),
                    )
                )
                if (active_count or 0) >= self._quota:
                    raise JobError("ACTIVE_JOB_QUOTA_EXCEEDED")
                db_now = self._db_now(db)
                month_start = db_now.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
                used_tokens = (
                    db.scalar(
                        select(func.coalesce(func.sum(JobUsageRecord.total_tokens), 0))
                        .join(AnalysisJob, AnalysisJob.id == JobUsageRecord.job_id)
                        .where(
                            JobUsageRecord.tenant_id == principal.tenant_id,
                            JobUsageRecord.occurred_at >= month_start,
                            AnalysisJob.status.not_in(_ACTIVE),
                        )
                    )
                    or 0
                )
                unmetered_tokens = (
                    db.scalar(
                        select(func.coalesce(func.sum(JobUsageRecord.unmetered_llm_requests), 0))
                        .join(AnalysisJob, AnalysisJob.id == JobUsageRecord.job_id)
                        .where(
                            JobUsageRecord.tenant_id == principal.tenant_id,
                            JobUsageRecord.occurred_at >= month_start,
                            AnalysisJob.status.not_in(_ACTIVE),
                        )
                    )
                    or 0
                )
                if unmetered_tokens:
                    raise JobError("MONTHLY_TOKEN_USAGE_UNMETERED")
                reserved_tokens = (
                    db.scalar(
                        select(func.coalesce(func.sum(AnalysisJob.reserved_token_budget), 0)).where(
                            AnalysisJob.tenant_id == principal.tenant_id,
                            AnalysisJob.status.in_(_ACTIVE),
                        )
                    )
                    or 0
                )
                if used_tokens + reserved_tokens + command.token_budget > self._monthly_token_quota:
                    raise JobError("MONTHLY_TOKEN_QUOTA_EXCEEDED")
                if self._monthly_cost_quota is not None:
                    if command.cost_budget_microusd is None:
                        raise JobError("JOB_COST_BUDGET_REQUIRED")
                    if self._pricing_catalog is None or not self._pricing_catalog.has_price(
                        provider=command.provider,
                        model=command.model,
                    ):
                        raise JobError("LLM_COST_UNMETERED")
                    used_cost = (
                        db.scalar(
                            select(
                                func.coalesce(
                                    func.sum(JobUsageRecord.estimated_cost_microusd), 0
                                )
                            )
                            .join(AnalysisJob, AnalysisJob.id == JobUsageRecord.job_id)
                            .where(
                                JobUsageRecord.tenant_id == principal.tenant_id,
                                JobUsageRecord.occurred_at >= month_start,
                                AnalysisJob.status.not_in(_ACTIVE),
                            )
                        )
                        or 0
                    )
                    reserved_cost = (
                        db.scalar(
                            select(
                                func.coalesce(
                                    func.sum(AnalysisJob.reserved_cost_budget_microusd), 0
                                )
                            ).where(
                                AnalysisJob.tenant_id == principal.tenant_id,
                                AnalysisJob.status.in_(_ACTIVE),
                            )
                        )
                        or 0
                    )
                    if (
                        used_cost + reserved_cost + command.cost_budget_microusd
                        > self._monthly_cost_quota
                    ):
                        raise JobError("MONTHLY_COST_QUOTA_EXCEEDED")
                job = AnalysisJob(
                    id=uuid4(),
                    run_id=uuid4(),
                    tenant_id=principal.tenant_id,
                    requested_by_user_id=principal.user_id,
                    status=JobStatus.QUEUED.value,
                    payload=payload,
                    payload_digest=payload_digest,
                    idempotency_key_hash=idem_hash,
                    request_digest=request_digest,
                    workflow_schema_version=WORKFLOW_SCHEMA_VERSION,
                    provider_snapshot=provider_snapshot.model_dump(mode="json"),
                    attempts=0,
                    max_attempts=max_attempts,
                    reserved_token_budget=command.token_budget,
                    reserved_cost_budget_microusd=command.cost_budget_microusd,
                    pricing_catalog_version=self._pricing_version,
                    available_at=db_now,
                    created_at=db_now,
                    updated_at=db_now,
                )
                db.add(job)
                db.flush()
                return job.id
        except IntegrityError:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
                replay = db.scalar(
                    select(AnalysisJob).where(
                        AnalysisJob.tenant_id == principal.tenant_id,
                        AnalysisJob.idempotency_key_hash == idem_hash,
                    )
                )
                if replay is not None and hmac.compare_digest(
                    replay.request_digest, request_digest
                ):
                    return replay.id
            raise JobError("JOB_CONFLICT") from None

    def claim_next(self, *, worker_id: str, lease_seconds: int = 120) -> ClaimedJob | None:
        if not 3 <= len(worker_id) <= 120 or any(ord(char) < 33 for char in worker_id):
            raise JobError("WORKER_ID_INVALID")
        if not 30 <= lease_seconds <= 900:
            raise JobError("LEASE_DURATION_INVALID")
        with self._sessions() as db, db.begin():
            now = self._db_now(db)
            self._requeue_expired(db, now)
            statement = (
                select(AnalysisJob)
                .where(
                    AnalysisJob.status == JobStatus.QUEUED.value,
                    AnalysisJob.available_at <= now,
                )
                .order_by(AnalysisJob.available_at, AnalysisJob.created_at, AnalysisJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            job = db.scalar(statement)
            if job is None:
                return None
            try:
                command = self._validated_command(job)
                context = self._load_or_create_context(job, command, now)
            except JobError as exc:
                job.status = JobStatus.FAILED.value
                job.error_code = str(exc)
                job.completed_at = now
                self._clear_lease(job)
                return None
            lease_id = uuid4()
            if context.deadline_at <= now:
                job.status = JobStatus.FAILED.value
                job.error_code = "JOB_DEADLINE_EXCEEDED"
                job.completed_at = now
                self._clear_lease(job)
                return None
            expires_at = min(
                now + timedelta(seconds=lease_seconds),
                context.deadline_at,
            )
            self._set_worker_claim_scope(
                db,
                lease_id=lease_id,
                lease_owner=worker_id,
            )
            job.status = JobStatus.LEASED.value
            job.lease_id = lease_id
            job.lease_owner = worker_id
            job.lease_expires_at = expires_at
            job.heartbeat_at = now
            job.attempts += 1
            job.run_context = context.model_dump(mode="json")
            job.run_context_digest = context.digest
            return ClaimedJob(
                id=job.id,
                run_id=job.run_id,
                tenant_id=job.tenant_id,
                requested_by_user_id=job.requested_by_user_id,
                lease_id=lease_id,
                lease_owner=worker_id,
                lease_expires_at=expires_at,
                command=command,
                run_context=context,
                attempts=job.attempts,
                prior_usage=JobUsage(
                    input_tokens=job.input_tokens,
                    output_tokens=job.output_tokens,
                    total_tokens=job.total_tokens,
                    llm_requests=job.llm_requests,
                    unmetered_llm_requests=job.unmetered_llm_requests,
                    estimated_cost_microusd=job.estimated_cost_microusd,
                    pricing_version=job.pricing_version,
                ),
                claimed_at=now,
            )

    def start(self, claim: ClaimedJob) -> None:
        with self._sessions() as db, db.begin():
            now = self._db_now(db)
            job = self._locked_claim(db, claim, now=now)
            if job.status == JobStatus.CANCEL_REQUESTED.value:
                job.status = JobStatus.CANCELLED.value
                job.completed_at = now
                self._clear_lease(job)
                raise JobError("JOB_CANCELLED")
            if job.status != JobStatus.LEASED.value:
                raise JobError("JOB_NOT_STARTABLE")
            job.status = JobStatus.RUNNING.value
            job.started_at = job.started_at or now

    def heartbeat(self, claim: ClaimedJob, *, lease_seconds: int = 120) -> datetime:
        if not 30 <= lease_seconds <= 900:
            raise JobError("LEASE_DURATION_INVALID")
        with self._sessions() as db, db.begin():
            now = self._db_now(db)
            job = self._locked_claim(db, claim, now=now)
            if job.status == JobStatus.CANCEL_REQUESTED.value:
                raise JobError("JOB_CANCELLED")
            if job.status not in {JobStatus.LEASED.value, JobStatus.RUNNING.value}:
                raise JobError("JOB_NOT_HEARTBEATABLE")
            job.heartbeat_at = now
            job.lease_expires_at = min(
                now + timedelta(seconds=lease_seconds),
                claim.run_context.deadline_at,
            )
            return job.lease_expires_at

    def complete(
        self,
        claim: ClaimedJob,
        *,
        result: dict[str, Any],
        manifest: dict[str, Any],
        usage: JobUsage | None = None,
    ) -> None:
        self._bounded_json(result, "result")
        with self._sessions() as db, db.begin():
            now = self._db_now(db)
            job = self._locked_claim(db, claim, now=now)
            self._record_usage(db, job, claim, usage or JobUsage())
            prior_attempts = []
            if isinstance(job.manifest, dict):
                candidate = job.manifest.get("attempt_history")
                if isinstance(candidate, list):
                    prior_attempts = candidate
            if prior_attempts:
                manifest = dict(manifest)
                manifest["failed_attempts"] = prior_attempts
            self._bounded_json(manifest, "manifest")
            if job.status == JobStatus.CANCEL_REQUESTED.value:
                job.status = JobStatus.CANCELLED.value
                job.result = None
                job.manifest = manifest
            elif job.status in {JobStatus.LEASED.value, JobStatus.RUNNING.value}:
                job.status = JobStatus.SUCCEEDED.value
                job.result = result
                job.manifest = manifest
            else:
                raise JobError("JOB_NOT_COMPLETABLE")
            job.completed_at = now
            self._clear_lease(job)

    def record_usage(self, claim: ClaimedJob, *, usage: JobUsage) -> None:
        """Persist billed usage before a retry/failure loses the worker process."""

        with self._sessions() as db, db.begin():
            job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == claim.id).with_for_update())
            if job is None:
                raise JobError("JOB_NOT_FOUND")
            self._set_worker_claim_scope(
                db,
                lease_id=claim.lease_id,
                lease_owner=claim.lease_owner,
            )
            self._record_usage(db, job, claim, usage)

    def fail(
        self,
        claim: ClaimedJob,
        *,
        error_code: str,
        retryable: bool = True,
        partial_manifest: dict[str, Any] | None = None,
    ) -> None:
        if not error_code or len(error_code) > 80 or not error_code.replace("_", "").isalnum():
            raise JobError("ERROR_CODE_INVALID")
        with self._sessions() as db, db.begin():
            now = self._db_now(db)
            job = self._locked_claim(db, claim, now=now)
            if partial_manifest is not None:
                self._bounded_json(partial_manifest, "manifest")
                prior_attempts: list[dict[str, Any]] = []
                if isinstance(job.manifest, dict):
                    existing = job.manifest.get("attempt_history")
                    if isinstance(existing, list):
                        prior_attempts = [
                            item for item in existing if isinstance(item, dict)
                        ]
                attempt = dict(partial_manifest)
                attempt["error_code"] = error_code
                attempt["attempt"] = claim.attempts
                job.manifest = {
                    "schema": "analysis-attempt-history/1",
                    "attempt_history": [*prior_attempts, attempt],
                }
                self._bounded_json(job.manifest, "manifest")
            if job.status == JobStatus.CANCEL_REQUESTED.value:
                job.status = JobStatus.CANCELLED.value
                job.completed_at = now
            elif retryable and job.attempts < job.max_attempts:
                job.status = JobStatus.QUEUED.value
                job.available_at = now + timedelta(
                    seconds=min(300, 2 ** min(job.attempts, 8))
                )
            else:
                job.status = JobStatus.FAILED.value
                job.completed_at = now
            job.error_code = error_code
            self._clear_lease(job)

    def cancel(self, *, principal: IdentityPrincipal, job_id: UUID) -> str:
        self._authorize(principal, write=True)
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            job = db.scalar(
                select(AnalysisJob)
                .where(AnalysisJob.id == job_id, AnalysisJob.tenant_id == principal.tenant_id)
                .with_for_update()
            )
            if job is None:
                raise JobError("JOB_NOT_FOUND")
            if job.status == JobStatus.QUEUED.value:
                job.status = JobStatus.CANCELLED.value
                job.completed_at = self._db_now(db)
            elif job.status in {JobStatus.LEASED.value, JobStatus.RUNNING.value}:
                job.status = JobStatus.CANCEL_REQUESTED.value
            return job.status

    def list_jobs(self, *, principal: IdentityPrincipal, limit: int = 50) -> tuple[JobView, ...]:
        self._authorize(principal)
        if not 1 <= limit <= 200:
            raise JobError("JOB_LIMIT_INVALID")
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            jobs = tuple(
                db.scalars(
                    select(AnalysisJob)
                    .where(AnalysisJob.tenant_id == principal.tenant_id)
                    .order_by(AnalysisJob.created_at.desc())
                    .limit(limit)
                )
            )
            return tuple(self._view(job) for job in jobs)

    def get_job(self, *, principal: IdentityPrincipal, job_id: UUID) -> JobDetail:
        self._authorize(principal)
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            job = db.scalar(
                select(AnalysisJob).where(
                    AnalysisJob.id == job_id,
                    AnalysisJob.tenant_id == principal.tenant_id,
                )
            )
            if job is None:
                raise JobError("JOB_NOT_FOUND")
            return JobDetail(job=self._view(job), result=job.result, manifest=job.manifest)

    @staticmethod
    def _bounded_json(value: dict[str, Any], label: str) -> None:
        if len(json.dumps(value, default=str).encode()) > 2_000_000:
            raise JobError(f"JOB_{label.upper()}_TOO_LARGE")

    def _locked_claim(
        self,
        db: Session,
        claim: ClaimedJob,
        *,
        now: datetime | None = None,
    ) -> AnalysisJob:
        job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == claim.id).with_for_update())
        now = now or self._db_now(db)
        if (
            job is None
            or job.lease_id != claim.lease_id
            or job.lease_owner != claim.lease_owner
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise JobError("JOB_LEASE_INVALID")
        self._set_worker_claim_scope(
            db,
            lease_id=claim.lease_id,
            lease_owner=claim.lease_owner,
        )
        return job

    def _validated_command(self, job: AnalysisJob) -> AnalysisJobCommand:
        if job.workflow_schema_version != WORKFLOW_SCHEMA_VERSION:
            raise JobError("WORKFLOW_SCHEMA_UNSUPPORTED")
        if not hmac.compare_digest(job.pricing_catalog_version, self._pricing_version):
            raise JobError("PRICING_CATALOG_MISMATCH")
        try:
            command = AnalysisJobCommand.model_validate(job.payload)
        except ValueError:
            raise JobError("JOB_PAYLOAD_INVALID") from None
        canonical = json.dumps(
            command.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        if not hmac.compare_digest(
            hashlib.sha256(canonical.encode()).hexdigest(), job.payload_digest
        ):
            raise JobError("JOB_PAYLOAD_INTEGRITY_FAILED")
        expected_request_digest = hmac.new(
            self._digest_key,
            (
                f"{job.tenant_id}:{canonical}:"
                + json.dumps(
                    job.provider_snapshot,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_request_digest, job.request_digest):
            raise JobError("JOB_REQUEST_AUTHENTICATION_FAILED")
        return command

    @staticmethod
    def _load_or_create_context(
        job: AnalysisJob,
        command: AnalysisJobCommand,
        now: datetime,
    ) -> AnalysisRunContext:
        config_digest = hashlib.sha256(
            json.dumps(
                effective_analysis_config(command),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        try:
            provider_snapshot = LlmReferenceSnapshot.model_validate(job.provider_snapshot)
        except ValueError:
            raise JobError("LLM_PROVENANCE_INVALID") from None
        if (
            provider_snapshot.provider != command.provider
            or provider_snapshot.model != command.model
            or provider_snapshot.connection_id != str(command.llm_connection_id)
            or provider_snapshot.credential_version != command.llm_credential_version
        ):
            raise JobError("LLM_PROVENANCE_MISMATCH")
        provider_digest = provider_snapshot.digest
        if job.run_context is None:
            try:
                decision_at = decision_cutoff(
                    trade_date=command.trade_date,
                    asset_type=command.asset_type,
                    market_calendar=command.market_calendar,
                    mode=command.mode,
                    observed_at=now,
                )
                context = AnalysisRunContext(
                    run_id=job.run_id,
                    job_id=job.id,
                    tenant_id=job.tenant_id,
                    requested_by_user_id=job.requested_by_user_id,
                    symbol=command.symbol,
                    asset_type=command.asset_type,
                    trade_date=command.trade_date,
                    decision_at=decision_at,
                    market_calendar=command.market_calendar,
                    mode=command.mode,
                    provider=command.provider,
                    model=command.model,
                    llm_connection_id=command.llm_connection_id,
                    llm_credential_version=command.llm_credential_version,
                    selected_analysts=command.selected_analysts,
                    config_digest=config_digest,
                    provider_digest=provider_digest,
                    provider_endpoint=provider_snapshot.endpoint,
                    provider_allowed_origins=provider_snapshot.allowed_origins,
                    transport_policy_version=provider_snapshot.transport_policy_version,
                    provider_adapter_digest=provider_snapshot.adapter_build_digest,
                    application_build_digest=provider_snapshot.application_build_digest,
                    calendar_engine_version=provider_snapshot.calendar_engine_version,
                    calendar_schedule_digest=calendar_schedule_digest(
                        market_calendar=command.market_calendar,
                        trade_date=command.trade_date,
                        decision_at=decision_at,
                    ),
                    token_budget=command.token_budget,
                    cost_budget_microusd=command.cost_budget_microusd,
                    pricing_catalog_version=job.pricing_catalog_version,
                    max_execution_seconds=command.max_execution_seconds,
                    deadline_at=now + timedelta(seconds=command.max_execution_seconds),
                    created_at=now,
                )
            except ValueError:
                raise JobError("RUN_CONTEXT_INVALID") from None
            job.run_context = context.model_dump(mode="json")
            job.run_context_digest = context.digest
            return context
        try:
            context = AnalysisRunContext.model_validate(job.run_context)
        except ValueError:
            raise JobError("RUN_CONTEXT_INVALID") from None
        if (
            job.run_context_digest is None
            or not hmac.compare_digest(context.digest, job.run_context_digest)
            or context.workflow_schema_version != job.workflow_schema_version
            or context.run_id != job.run_id
            or context.job_id != job.id
            or context.tenant_id != job.tenant_id
            or context.requested_by_user_id != job.requested_by_user_id
            or context.symbol != command.symbol
            or context.asset_type != command.asset_type
            or context.trade_date != command.trade_date
            or context.market_calendar != command.market_calendar
            or context.mode != command.mode
            or context.provider != command.provider
            or context.model != command.model
            or context.llm_connection_id != command.llm_connection_id
            or context.llm_credential_version != command.llm_credential_version
            or context.selected_analysts != command.selected_analysts
            or context.config_digest != config_digest
            or context.provider_digest != provider_digest
            or context.provider_endpoint != provider_snapshot.endpoint
            or context.provider_allowed_origins != provider_snapshot.allowed_origins
            or context.transport_policy_version
            != provider_snapshot.transport_policy_version
            or context.provider_adapter_digest != provider_snapshot.adapter_build_digest
            or context.application_build_digest
            != provider_snapshot.application_build_digest
            or context.calendar_engine_version
            != provider_snapshot.calendar_engine_version
            or context.token_budget != command.token_budget
            or context.cost_budget_microusd != command.cost_budget_microusd
            or context.pricing_catalog_version != job.pricing_catalog_version
            or context.max_execution_seconds != command.max_execution_seconds
            or context.deadline_at != context.created_at
            + timedelta(seconds=command.max_execution_seconds)
        ):
            raise JobError("RUN_CONTEXT_INTEGRITY_FAILED")
        return context

    @staticmethod
    def _clear_lease(job: AnalysisJob) -> None:
        job.lease_id = None
        job.lease_owner = None
        job.lease_expires_at = None

    @staticmethod
    def _view(job: AnalysisJob) -> JobView:
        payload = job.payload
        return JobView(
            id=job.id,
            run_id=job.run_id,
            status=job.status,
            symbol=str(payload["symbol"]),
            asset_type=str(payload["asset_type"]),
            trade_date=str(payload["trade_date"]),
            mode=str(payload["mode"]),
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error_code=job.error_code,
            input_tokens=job.input_tokens,
            output_tokens=job.output_tokens,
            total_tokens=job.total_tokens,
            llm_requests=job.llm_requests,
            unmetered_llm_requests=job.unmetered_llm_requests,
            estimated_cost_microusd=job.estimated_cost_microusd,
            pricing_version=job.pricing_version,
            token_budget=job.reserved_token_budget,
            cost_budget_microusd=job.reserved_cost_budget_microusd,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )

    @staticmethod
    def _record_usage(
        db: Session,
        job: AnalysisJob,
        claim: ClaimedJob,
        usage: JobUsage,
    ) -> None:
        if (
            job.id != claim.id
            or job.run_id != claim.run_id
            or job.tenant_id != claim.tenant_id
            or job.requested_by_user_id != claim.requested_by_user_id
            or claim.attempts <= 0
            or claim.attempts > job.attempts
        ):
            raise JobError("JOB_USAGE_FENCE_INVALID")
        canonical = json.dumps(usage.as_dict(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        existing = db.scalar(
            select(JobUsageRecord).where(
                JobUsageRecord.job_id == job.id,
                JobUsageRecord.attempt_number == claim.attempts,
            )
        )
        if existing is not None:
            if not hmac.compare_digest(existing.usage_digest, digest):
                raise JobError("JOB_USAGE_REPLAY_MISMATCH")
            return
        if (
            job.lease_id != claim.lease_id
            or job.lease_owner != claim.lease_owner
            or job.status
            not in {
                JobStatus.LEASED.value,
                JobStatus.RUNNING.value,
                JobStatus.CANCEL_REQUESTED.value,
            }
        ):
            raise JobError("JOB_USAGE_FENCE_INVALID")
        db.add(
            JobUsageRecord(
                tenant_id=job.tenant_id,
                job_id=job.id,
                run_id=job.run_id,
                attempt_number=claim.attempts,
                lease_id=claim.lease_id,
                usage_digest=digest,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                llm_requests=usage.llm_requests,
                unmetered_llm_requests=usage.unmetered_llm_requests,
                estimated_cost_microusd=usage.estimated_cost_microusd,
                pricing_version=usage.pricing_version,
            )
        )
        job.input_tokens += usage.input_tokens
        job.output_tokens += usage.output_tokens
        job.total_tokens += usage.total_tokens
        job.llm_requests += usage.llm_requests
        job.unmetered_llm_requests += usage.unmetered_llm_requests
        if usage.estimated_cost_microusd is not None:
            job.estimated_cost_microusd = (
                job.estimated_cost_microusd or 0
            ) + usage.estimated_cost_microusd
            if job.pricing_version is None:
                job.pricing_version = usage.pricing_version
            elif job.pricing_version != usage.pricing_version:
                job.pricing_version = "mixed"

    def _requeue_expired(self, db: Session, now: datetime) -> None:
        # Bound cleanup work inside a claim transaction so a large outage does
        # not turn every worker into a global full-backlog locker.
        expired = tuple(
            db.scalars(
                select(AnalysisJob)
                .where(
                    AnalysisJob.status.in_(
                        (
                            JobStatus.LEASED.value,
                            JobStatus.RUNNING.value,
                            JobStatus.CANCEL_REQUESTED.value,
                        )
                    ),
                    AnalysisJob.lease_expires_at <= now,
                )
                .order_by(AnalysisJob.lease_expires_at, AnalysisJob.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        for job in expired:
            integrity_error: str | None = None
            try:
                command = self._validated_command(job)
                self._load_or_create_context(job, command, now)
            except JobError as exc:
                integrity_error = str(exc)
            # A hard-killed process cannot flush provider usage. Conservatively
            # consume the entire reserved job budget and terminate the job;
            # re-leasing it could otherwise spend the same budget twice while
            # the stale provider request is still in flight.
            if job.lease_id is not None:
                JobService._record_expired_reservation(db, job)
            if job.status == JobStatus.CANCEL_REQUESTED.value:
                job.status = JobStatus.CANCELLED.value
                job.completed_at = now
            else:
                job.status = JobStatus.FAILED.value
                job.error_code = integrity_error or "LEASE_EXPIRED_BUDGET_CONSUMED"
                job.completed_at = now
            JobService._clear_lease(job)

    @staticmethod
    def _record_expired_reservation(db: Session, job: AnalysisJob) -> None:
        existing = db.scalar(
            select(JobUsageRecord.id).where(
                JobUsageRecord.job_id == job.id,
                JobUsageRecord.attempt_number == job.attempts,
            )
        )
        if existing is not None or job.lease_id is None:
            return
        usage = JobUsage(
            input_tokens=job.reserved_token_budget,
            output_tokens=0,
            total_tokens=job.reserved_token_budget,
            llm_requests=1,
            unmetered_llm_requests=0,
            estimated_cost_microusd=job.reserved_cost_budget_microusd,
            pricing_version=(
                job.pricing_catalog_version
                if job.reserved_cost_budget_microusd is not None
                else None
            ),
        )
        canonical = json.dumps(usage.as_dict(), sort_keys=True, separators=(",", ":"))
        db.add(
            JobUsageRecord(
                tenant_id=job.tenant_id,
                job_id=job.id,
                run_id=job.run_id,
                attempt_number=job.attempts,
                lease_id=job.lease_id,
                usage_digest=hashlib.sha256(canonical.encode()).hexdigest(),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                llm_requests=usage.llm_requests,
                unmetered_llm_requests=usage.unmetered_llm_requests,
                estimated_cost_microusd=usage.estimated_cost_microusd,
                pricing_version=usage.pricing_version,
            )
        )
        job.input_tokens += usage.input_tokens
        job.total_tokens += usage.total_tokens
        job.llm_requests += usage.llm_requests
        job.unmetered_llm_requests += usage.unmetered_llm_requests
        if usage.estimated_cost_microusd is not None:
            job.estimated_cost_microusd = (
                (job.estimated_cost_microusd or 0) + usage.estimated_cost_microusd
            )
            job.pricing_version = usage.pricing_version
