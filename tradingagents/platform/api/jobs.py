"""Application port for durable tenant analysis jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from tradingagents.platform.api.identity import IdentityPrincipal


@dataclass(frozen=True, slots=True)
class AnalysisJobView:
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
class AnalysisJobDetailView:
    job: AnalysisJobView
    result: dict[str, Any] | None
    manifest: dict[str, Any] | None


@runtime_checkable
class AnalysisJobServicePort(Protocol):
    async def enqueue(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        idempotency_key: str,
        command: dict[str, Any],
    ) -> UUID: ...

    async def list_jobs(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        limit: int,
    ) -> tuple[AnalysisJobView, ...]: ...

    async def get_job(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        job_id: UUID,
    ) -> AnalysisJobDetailView: ...

    async def cancel(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        job_id: UUID,
    ) -> str: ...
