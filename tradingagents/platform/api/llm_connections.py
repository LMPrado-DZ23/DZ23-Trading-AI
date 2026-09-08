"""Application port for tenant LLM provider connections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from tradingagents.platform.api.identity import IdentityPrincipal


@dataclass(frozen=True, slots=True)
class LlmProviderView:
    slug: str
    display_name: str
    requires_endpoint: bool


@dataclass(frozen=True, slots=True)
class LlmConnectionView:
    id: UUID
    label: str
    provider: str
    display_name: str
    base_url: str | None
    status: str
    credential_version: int
    created_at: datetime


@runtime_checkable
class LlmConnectionServicePort(Protocol):
    async def catalog(self) -> tuple[LlmProviderView, ...]: ...

    async def list_connections(
        self, *, token: str, principal: IdentityPrincipal
    ) -> tuple[LlmConnectionView, ...]: ...

    async def save_connection(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        idempotency_key: str,
        label: str,
        provider: str,
        api_key: str,
        base_url: str | None,
    ) -> UUID: ...

    async def disable_connection(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        connection_id: UUID,
    ) -> None: ...
