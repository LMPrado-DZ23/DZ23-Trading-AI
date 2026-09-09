"""Application port for the deterministic portfolio slice."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from tradingagents.platform.api.identity import IdentityPrincipal


@runtime_checkable
class PortfolioServicePort(Protocol):
    async def assess_suitability(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        requested_profile: str,
        answers: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def create_portfolio(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        name: str,
        base_currency: str,
    ) -> dict[str, Any]: ...

    async def activate_mandate(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        suitability_id: UUID,
        profile: str,
        mode: str,
    ) -> dict[str, Any]: ...

    async def record_funding(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        amount: Decimal,
        idempotency_key: str,
        reference_id: str,
    ) -> UUID: ...

    async def transition_mandate(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        portfolio_id: UUID,
        target_status: str,
    ) -> dict[str, Any]: ...

    async def list_portfolios(
        self, *, token: str, principal: IdentityPrincipal
    ) -> tuple[dict[str, Any], ...]: ...
