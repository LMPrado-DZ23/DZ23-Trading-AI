"""Async adapter for the encrypted broker connection domain."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from uuid import UUID

from anyio import to_thread

from tradingagents.platform.api.connections import (
    BrokerConnectionView,
    BrokerDiagnosticView,
    ExchangeCatalogView,
)
from tradingagents.platform.api.identity import (
    IdentityError as ApiIdentityError,
    IdentityPrincipal as ApiIdentityPrincipal,
)
from tradingagents.platform.compliance import ComplianceError, MfaService
from tradingagents.platform.connections import (
    BrokerCredentialInput,
    BrokerEnvironment,
    ConnectionError,
    ConnectionService,
    exchange_catalog,
)
from tradingagents.platform.identity import IdentityError, IdentityPrincipal, IdentityService


class SqlAlchemyBrokerConnectionAdapter:
    def __init__(
        self,
        *,
        identity: IdentityService,
        mfa: MfaService,
        connections: ConnectionService,
    ) -> None:
        self._identity = identity
        self._mfa = mfa
        self._connections = connections

    async def catalog(self) -> tuple[ExchangeCatalogView, ...]:
        return tuple(
            ExchangeCatalogView(
                slug=item.slug,
                display_name=item.display_name,
                has_real_testnet=item.has_real_testnet,
                required_fields=item.credential_fields,
                live_permission_probe=item.permission_probe,
            )
            for item in exchange_catalog()
        )

    async def list_connections(
        self, *, token: str, principal: ApiIdentityPrincipal
    ) -> tuple[BrokerConnectionView, ...]:
        actor = await self._principal(token, principal)
        try:
            rows = await _run_sync(self._connections.list_connections, principal=actor)
        except ConnectionError as exc:
            raise ApiIdentityError(str(exc)) from exc
        return tuple(
            BrokerConnectionView(
                id=row.id,
                label=row.label,
                exchange_slug=row.exchange_slug,
                display_name=row.display_name,
                environment=row.environment,
                status=row.status,
                credential_version=row.credential_version,
                capability_digest=row.capability_digest,
                ccxt_version=row.ccxt_version,
                permission_status=row.permission_status,
                last_diagnostic_code=row.last_diagnostic_code,
                last_checked_at=row.last_checked_at,
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
        exchange_slug: str,
        environment: str,
        api_key: str,
        secret: str,
        passphrase: str | None,
        uid: str | None,
    ) -> UUID:
        actor = await self._principal(token, principal, require_step_up=True)
        try:
            return await _run_sync(
                self._connections.upsert_connection,
                principal=actor,
                idempotency_key=idempotency_key,
                label=label,
                exchange_slug=exchange_slug,
                environment=BrokerEnvironment(environment),
                credentials=BrokerCredentialInput(
                    api_key=api_key,
                    secret=secret,
                    passphrase=passphrase,
                    uid=uid,
                ),
            )
        except (ConnectionError, ValueError) as exc:
            raise ApiIdentityError(str(exc)) from exc

    async def diagnose(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        connection_id: UUID,
    ) -> BrokerDiagnosticView:
        actor = await self._principal(token, principal, require_step_up=True)
        try:
            result = await _run_sync(
                self._connections.diagnose,
                principal=actor,
                connection_id=connection_id,
            )
        except ConnectionError as exc:
            raise ApiIdentityError(str(exc)) from exc
        return BrokerDiagnosticView(
            success=result.success,
            code=result.code,
            market_count=result.market_count,
            nonzero_asset_count=result.nonzero_asset_count,
            permissions_verified=result.permissions_verified,
            permissions_safe=result.permissions_safe,
            permission_status=result.permission_status,
            spot_only=result.spot_only,
            adapter_version=result.adapter_version,
        )

    async def remove_connection(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        connection_id: UUID,
    ) -> None:
        actor = await self._principal(token, principal, require_step_up=True)
        try:
            await _run_sync(
                self._connections.remove_connection,
                principal=actor,
                connection_id=connection_id,
            )
        except ConnectionError as exc:
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
