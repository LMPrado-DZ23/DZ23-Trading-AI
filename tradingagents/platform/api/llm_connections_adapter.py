"""Async API adapter for encrypted LLM provider connections."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from uuid import UUID

from anyio import to_thread

from tradingagents.platform.api.identity import (
    IdentityError as ApiIdentityError,
    IdentityPrincipal as ApiIdentityPrincipal,
)
from tradingagents.platform.api.llm_connections import (
    LlmConnectionView,
    LlmProviderView,
)
from tradingagents.platform.compliance import ComplianceError, MfaService
from tradingagents.platform.identity import IdentityError, IdentityPrincipal, IdentityService
from tradingagents.platform.llm_connections import (
    LlmConnectionError,
    LlmConnectionService,
    llm_provider_catalog,
)


class SqlAlchemyLlmConnectionAdapter:
    def __init__(
        self,
        *,
        identity: IdentityService,
        mfa: MfaService,
        connections: LlmConnectionService,
    ) -> None:
        self._identity = identity
        self._mfa = mfa
        self._connections = connections

    async def catalog(self) -> tuple[LlmProviderView, ...]:
        return tuple(
            LlmProviderView(item.slug, item.display_name, item.requires_endpoint)
            for item in llm_provider_catalog()
        )

    async def list_connections(
        self, *, token: str, principal: ApiIdentityPrincipal
    ) -> tuple[LlmConnectionView, ...]:
        actor = await self._principal(token, principal)
        try:
            rows = await _run_sync(self._connections.list_connections, principal=actor)
        except LlmConnectionError as exc:
            raise ApiIdentityError(str(exc)) from exc
        return tuple(
            LlmConnectionView(
                id=row.id,
                label=row.label,
                provider=row.provider,
                display_name=row.display_name,
                base_url=row.base_url,
                status=row.status,
                credential_version=row.credential_version,
                created_at=row.created_at,
            )
            for row in rows
        )

    async def save_connection(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        idempotency_key: str,
        label: str,
        provider: str,
        api_key: str,
        base_url: str | None,
    ) -> UUID:
        actor = await self._principal(token, principal, require_step_up=True)
        try:
            return await _run_sync(
                self._connections.upsert,
                principal=actor,
                idempotency_key=idempotency_key,
                label=label,
                provider=provider,
                api_key=api_key,
                base_url=base_url,
            )
        except LlmConnectionError as exc:
            raise ApiIdentityError(str(exc)) from exc

    async def disable_connection(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        connection_id: UUID,
    ) -> None:
        actor = await self._principal(token, principal, require_step_up=True)
        try:
            await _run_sync(
                self._connections.disable,
                principal=actor,
                connection_id=connection_id,
            )
        except LlmConnectionError as exc:
            raise ApiIdentityError(str(exc)) from exc

    async def _principal(
        self,
        token: str,
        expected: ApiIdentityPrincipal,
        *,
        require_step_up: bool = False,
    ) -> IdentityPrincipal:
        try:
            profile = await _run_sync(self._identity.authenticate_session_token, token=token)
        except IdentityError as exc:
            raise ApiIdentityError("SESSION_INVALID") from exc
        if profile.user_id != expected.user_id or profile.tenant_id != expected.tenant_id:
            raise ApiIdentityError("SESSION_INVALID")
        actor = IdentityPrincipal(
            tenant_id=profile.tenant_id,
            user_id=profile.user_id,
            membership_id=profile.membership_id,
            session_id=profile.session_id,
            role=profile.role,
        )
        if require_step_up:
            try:
                await _run_sync(self._mfa.assert_recent_step_up, principal=actor)
            except ComplianceError as exc:
                raise ApiIdentityError("MFA_STEP_UP_REQUIRED") from exc
        return actor


async def _run_sync(function: Callable, /, *args, **kwargs):
    return await to_thread.run_sync(partial(function, *args, **kwargs))
