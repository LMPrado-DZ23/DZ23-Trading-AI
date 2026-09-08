"""Immutable identity and provenance contract for one analysis execution."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from uuid import UUID

import exchange_calendars as xcals
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.platform.contracts import RunMode

WORKFLOW_SCHEMA_VERSION = 3


def decision_cutoff(*, trade_date: str, asset_type: str, market_calendar: str) -> datetime:
    """Return the immutable market cutoff for the supported v1 asset classes."""

    try:
        calendar = xcals.get_calendar(market_calendar)
        cutoff = calendar.session_close(trade_date)
    except (KeyError, ValueError) as exc:
        raise ValueError("trade_date is not a session in the selected market calendar") from exc
    return cutoff.to_pydatetime()


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
    created_at: datetime

    @field_validator("created_at", "decision_at")
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
        ):
            raise ValueError("decision_at does not match the market cutoff contract")
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
