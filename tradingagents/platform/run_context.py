"""Immutable identity and provenance contract for one analysis execution."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import exchange_calendars as xcals
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.platform.contracts import RunMode
from tradingagents.platform.llm_provenance import LlmReferenceSnapshot

WORKFLOW_SCHEMA_VERSION = 7
DAILY_BAR_SETTLEMENT_LAG = timedelta(minutes=15)


def calendar_schedule_digest(
    *, market_calendar: str, trade_date: str, decision_at: datetime
) -> str:
    return hashlib.sha256(
        (
            f"{market_calendar}:{trade_date}:"
            f"{decision_at.astimezone(UTC).isoformat()}"
        ).encode()
    ).hexdigest()


def decision_cutoff(
    *,
    trade_date: str,
    asset_type: str,
    market_calendar: str,
    mode: RunMode = RunMode.BACKTEST,
    observed_at: datetime | None = None,
) -> datetime:
    """Return the immutable market cutoff for the supported v1 asset classes."""

    try:
        calendar = xcals.get_calendar(market_calendar)
        cutoff = calendar.session_close(trade_date).to_pydatetime()
    except (KeyError, ValueError) as exc:
        raise ValueError("trade_date is not a session in the selected market calendar") from exc
    now = (observed_at or datetime.now(UTC)).astimezone(UTC)
    # A daily bar is not decision-usable at the exchange's closing instant.
    # The immutable decision timestamp is when the completed bar is considered
    # settled by policy; next-open execution still occurs strictly afterwards.
    available_at = cutoff + DAILY_BAR_SETTLEMENT_LAG
    if mode is RunMode.BACKTEST:
        if available_at > now:
            raise ValueError("backtest requires a settled market session")
        return available_at
    if mode not in {RunMode.SHADOW, RunMode.PAPER}:
        raise ValueError("analysis mode is not supported")
    local_date = now.astimezone(calendar.tz).date()
    latest_session = calendar.date_to_session(local_date, direction="previous")
    if (
        calendar.session_close(latest_session).to_pydatetime()
        + DAILY_BAR_SETTLEMENT_LAG
        > now
    ):
        latest_session = calendar.previous_session(latest_session)
    if latest_session.date().isoformat() != trade_date or available_at > now:
        raise ValueError("paper and shadow require the latest settled market session")
    return available_at


class AnalysisRunContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    run_id: UUID
    job_id: UUID
    tenant_id: UUID
    requested_by_user_id: UUID
    symbol: str = Field(min_length=1, max_length=64)
    asset_type: str = Field(pattern=r"^(stock|crypto)$")
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    decision_at: datetime
    market_calendar: str = Field(pattern=r"^(24/7|XNYS|BVMF)$")
    mode: RunMode
    provider: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    model: str = Field(min_length=1, max_length=160)
    llm_connection_id: UUID
    llm_credential_version: int = Field(ge=1)
    selected_analysts: tuple[str, ...] = Field(min_length=1, max_length=4)
    workflow_schema_version: int = WORKFLOW_SCHEMA_VERSION
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_endpoint: str = Field(min_length=1, max_length=2048)
    provider_allowed_origins: tuple[str, ...] = Field(max_length=16)
    transport_policy_version: str = Field(min_length=1, max_length=80)
    provider_adapter_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    application_build_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    calendar_engine_version: str = Field(min_length=1, max_length=80)
    calendar_schedule_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_budget: int = Field(ge=1_000, le=2_000_000)
    cost_budget_microusd: int | None = Field(default=None, ge=1_000, le=1_000_000_000)
    pricing_catalog_version: str = Field(pattern=r"^[0-9a-f]{16}$")
    max_execution_seconds: int = Field(ge=30, le=3600)
    deadline_at: datetime
    created_at: datetime

    @field_validator("created_at", "decision_at", "deadline_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_schema_and_analysts(self):
        if self.workflow_schema_version != WORKFLOW_SCHEMA_VERSION:
            raise ValueError("unsupported workflow schema version")
        allowed = {"market", "social", "news", "fundamentals"}
        if len(set(self.selected_analysts)) != len(self.selected_analysts):
            raise ValueError("selected analysts must be unique")
        if not set(self.selected_analysts).issubset(allowed):
            raise ValueError("selected analyst is not supported")
        if self.decision_at != decision_cutoff(
            trade_date=self.trade_date,
            asset_type=self.asset_type,
            market_calendar=self.market_calendar,
            mode=self.mode,
            observed_at=self.created_at,
        ):
            raise ValueError("decision_at does not match the market cutoff contract")
        if self.deadline_at != self.created_at + timedelta(seconds=self.max_execution_seconds):
            raise ValueError("deadline_at does not match the execution deadline contract")
        snapshot = LlmReferenceSnapshot(
            provider=self.provider,
            model=self.model,
            connection_id=str(self.llm_connection_id),
            credential_version=self.llm_credential_version,
            endpoint=self.provider_endpoint,
            allowed_origins=self.provider_allowed_origins,
            transport_policy_version=self.transport_policy_version,
            adapter_build_digest=self.provider_adapter_digest,
            application_build_digest=self.application_build_digest,
            calendar_engine_version=self.calendar_engine_version,
        )
        if snapshot.digest != self.provider_digest:
            raise ValueError("provider provenance digest does not match its snapshot")
        expected_schedule = calendar_schedule_digest(
            market_calendar=self.market_calendar,
            trade_date=self.trade_date,
            decision_at=self.decision_at,
        )
        if expected_schedule != self.calendar_schedule_digest:
            raise ValueError("calendar schedule digest does not match the decision cutoff")
        return self

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def manifest(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["run_context_digest"] = self.digest
        payload["schema"] = "analysis-run-manifest/1"
        return payload


_current_run: ContextVar[AnalysisRunContext | None] = ContextVar(
    "tradingagents_analysis_run_context", default=None
)


def current_run_context() -> AnalysisRunContext | None:
    return _current_run.get()


@contextmanager
def activate_run_context(context: AnalysisRunContext):
    token = _current_run.set(context)
    try:
        yield context
    finally:
        _current_run.reset(token)
