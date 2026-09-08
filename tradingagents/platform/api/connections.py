"""Application port and DTOs for exchange connection routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from tradingagents.platform.api.identity import IdentityPrincipal


@dataclass(frozen=True, slots=True)
class ExchangeCatalogView:
    slug: str
    display_name: str
    has_real_testnet: bool
    required_fields: tuple[str, ...]
    live_permission_probe: bool


@dataclass(frozen=True, slots=True)
class BrokerConnectionView:
    id: UUID
    label: str
    exchange_slug: str
    display_name: str
    environment: str
    status: str
    credential_version: int
    capability_digest: str
    ccxt_version: str | None
    permission_status: str
    last_diagnostic_code: str | None
    last_checked_at: datetime | None


@dataclass(frozen=True, slots=True)
class BrokerDiagnosticView:
    success: bool
    code: str
    market_count: int
    nonzero_asset_count: int
    permissions_verified: bool
    permissions_safe: bool
    permission_status: str
    spot_only: bool
    adapter_version: str


@runtime_checkable
class BrokerConnectionServicePort(Protocol):
    async def catalog(self) -> tuple[ExchangeCatalogView, ...]: ...

    async def list_connections(
        self, *, token: str, principal: IdentityPrincipal
    ) -> tuple[BrokerConnectionView, ...]: ...

    async def save_connection(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        idempotency_key: str,
        label: str,
        exchange_slug: str,
        environment: str,
        api_key: str,
        secret: str,
        passphrase: str | None,
        uid: str | None,
    ) -> UUID: ...

    async def diagnose(
        self, *, token: str, principal: IdentityPrincipal, connection_id: UUID
    ) -> BrokerDiagnosticView: ...

    async def remove_connection(
        self, *, token: str, principal: IdentityPrincipal, connection_id: UUID
    ) -> None: ...
