"""Async API adapter for the durable analysis job queue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from functools import partial
from typing import Any
from uuid import UUID

from anyio import to_thread

from tradingagents.platform.api.identity import (
    IdentityError as ApiIdentityError,
    IdentityPrincipal as ApiIdentityPrincipal,
)
from tradingagents.platform.api.jobs import AnalysisJobDetailView, AnalysisJobView
from tradingagents.platform.identity import IdentityError, IdentityPrincipal, IdentityService
from tradingagents.platform.jobs import AnalysisJobCommand, JobError, JobService


class SqlAlchemyAnalysisJobAdapter:
    def __init__(self, *, identity: IdentityService, jobs: JobService) -> None:
        self._identity = identity
        self._jobs = jobs

    async def enqueue(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        idempotency_key: str,
        command: dict[str, Any],
    ) -> UUID:
        actor = await self._principal(token, principal)
        try:
            parsed = AnalysisJobCommand.model_validate(command)
            return await _run_sync(
                self._jobs.enqueue,
                principal=actor,
                idempotency_key=idempotency_key,
                command=parsed,
            )
        except (JobError, ValueError) as exc:
            raise ApiIdentityError(str(exc)) from exc

    async def list_jobs(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        limit: int,
    ) -> tuple[AnalysisJobView, ...]:
        actor = await self._principal(token, principal)
        try:
            rows = await _run_sync(self._jobs.list_jobs, principal=actor, limit=limit)
        except JobError as exc:
            raise ApiIdentityError(str(exc)) from exc
        return tuple(AnalysisJobView(**asdict(row)) for row in rows)

    async def cancel(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        job_id: UUID,
    ) -> str:
        actor = await self._principal(token, principal)
        try:
            return await _run_sync(self._jobs.cancel, principal=actor, job_id=job_id)
        except JobError as exc:
            raise ApiIdentityError(str(exc)) from exc

    async def get_job(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        job_id: UUID,
    ) -> AnalysisJobDetailView:
        actor = await self._principal(token, principal)
        try:
            detail = await _run_sync(self._jobs.get_job, principal=actor, job_id=job_id)
        except JobError as exc:
            raise ApiIdentityError(str(exc)) from exc
        return AnalysisJobDetailView(
            job=AnalysisJobView(**asdict(detail.job)),
            result=detail.result,
            manifest=detail.manifest,
        )

    async def _principal(self, token: str, expected: ApiIdentityPrincipal) -> IdentityPrincipal:
        try:
            profile = await _run_sync(self._identity.authenticate_session_token, token=token)
        except IdentityError as exc:
            raise ApiIdentityError("SESSION_INVALID") from exc
        if profile.user_id != expected.user_id or profile.tenant_id != expected.tenant_id:
            raise ApiIdentityError("SESSION_INVALID")
        return IdentityPrincipal(
            tenant_id=profile.tenant_id,
            user_id=profile.user_id,
            membership_id=profile.membership_id,
            session_id=profile.session_id,
            role=profile.role,
        )


async def _run_sync(function: Callable, /, *args, **kwargs):
    return await to_thread.run_sync(partial(function, *args, **kwargs))
