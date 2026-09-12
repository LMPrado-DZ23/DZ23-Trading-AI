"""Versioned API/job contracts. No credentials or arbitrary config are accepted."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.graph.analyst_execution import ANALYST_NODE_SPECS

JOB_SCHEMA_VERSION = "tradingagents/job/1"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["analysis", "backtest"] = "analysis"
    ticker: str = Field(min_length=1, max_length=32)
    asset_type: Literal["stock", "crypto", "forex", "index"] = "stock"
    selected_analysts: tuple[Literal["market", "social", "news", "fundamentals"], ...] = (
        "market", "social", "news", "fundamentals"
    )
    trade_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    benchmark_ticker: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_contract(self):
        safe_ticker_component(self.ticker)
        if self.benchmark_ticker:
            safe_ticker_component(self.benchmark_ticker)
        if not self.selected_analysts or len(set(self.selected_analysts)) != len(
            self.selected_analysts
        ):
            raise ValueError("selected_analysts must be non-empty and unique")
        if any(key not in ANALYST_NODE_SPECS for key in self.selected_analysts):
            raise ValueError("unknown analyst")
        if self.asset_type == "crypto" and "fundamentals" in self.selected_analysts:
            raise ValueError("fundamentals analyst is not supported for crypto")
        if self.kind == "analysis":
            if self.trade_date is None or any(
                value is not None for value in (self.start_date, self.end_date, self.benchmark_ticker)
            ):
                raise ValueError("analysis requires trade_date and forbids backtest fields")
        else:
            if self.trade_date is not None or self.start_date is None or self.end_date is None:
                raise ValueError("backtest requires start_date/end_date and forbids trade_date")
            if self.start_date >= self.end_date:
                raise ValueError("backtest start_date must be before end_date")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    idempotency_key: str
    request_sha256: str
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    created_at: datetime
    updated_at: datetime
    lease_until: datetime | None = None
    worker_id: str | None = None
    lease_token: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    schema: str = JOB_SCHEMA_VERSION

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "job_id": self.job_id,
            "status": self.status.value,
            "attempts": self.attempts,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "result": self.result,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }
