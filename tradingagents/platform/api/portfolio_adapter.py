"""Authenticated async adapter for the synchronous portfolio service."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID

from anyio import to_thread

from tradingagents.platform.api.identity import (
    IdentityError as ApiIdentityError,
    IdentityPrincipal as ApiIdentityPrincipal,
)
from tradingagents.platform.contracts import RiskProfile, RunMode
from tradingagents.platform.identity import IdentityError, IdentityPrincipal, IdentityService
from tradingagents.platform.portfolio import PortfolioError, PortfolioService


class SqlAlchemyPortfolioAdapter:
    def __init__(self, *, identity: IdentityService, portfolios: PortfolioService) -> None:
        self._identity = identity
        self._portfolios = portfolios

    async def assess_suitability(self, *, token, principal, requested_profile, answers):
        actor = await self._principal(token, principal)
        row = await self._invoke(
            self._portfolios.assess_suitability,
            principal=actor,
            requested_profile=RiskProfile(requested_profile),
            answers=answers,
        )
        return self._public(
            row,
            "id",
            "version",
            "requested_profile",
            "maximum_profile",
            "effective_profile",
            "status",
            "policy_version",
            "digest",
            "assessed_at",
            "expires_at",
            "paused_portfolios",
        )

    async def create_portfolio(self, *, token, principal, name, base_currency):
        actor = await self._principal(token, principal)
        row = await self._invoke(
            self._portfolios.create_portfolio,
            principal=actor,
            name=name,
            base_currency=base_currency,
        )
        return self._public(row, "id", "name", "base_currency", "status")

    async def activate_mandate(
        self, *, token, principal, portfolio_id, suitability_id, profile, mode
    ):
        actor = await self._principal(token, principal)
        row = await self._invoke(
            self._portfolios.activate_mandate,
            principal=actor,
            portfolio_id=portfolio_id,
            suitability_id=suitability_id,
            profile=RiskProfile(profile),
            mode=RunMode(mode),
        )
        return self._mandate_public(row)

    async def record_funding(
        self, *, token, principal, portfolio_id, amount, mode, idempotency_key, reference_id
    ) -> dict[str, Any]:
        actor = await self._principal(token, principal)
        row = await self._invoke(
            self._portfolios.record_funding,
            principal=actor,
            portfolio_id=portfolio_id,
            amount=amount,
            mode=RunMode(mode),
            idempotency_key=idempotency_key,
            reference_id=reference_id,
        )
        return {
            "transaction_id": UUID(row["id"]),
            "mode": mode,
            "provenance": row["provenance"],
            "verification_status": row["verification_status"],
            "message": (
                "Saldo informado pelo usuário e marcado como não verificado; "
                "ele não comprova o saldo existente na corretora."
            ),
        }

    async def transition_mandate(self, *, token, principal, portfolio_id, target_status):
        actor = await self._principal(token, principal)
        row = await self._invoke(
            self._portfolios.transition_mandate,
            principal=actor,
            portfolio_id=portfolio_id,
            target_status=target_status,
        )
        return self._mandate_public(row)

    async def list_portfolios(self, *, token, principal):
        actor = await self._principal(token, principal)
        rows = await self._run_sync(self._portfolios.list_portfolios, principal=actor)
        return tuple(asdict(row) for row in rows)

    async def get_latest_snapshot(self, *, token, principal, portfolio_id):
        actor = await self._principal(token, principal)
        try:
            row = await self._run_sync(
                self._portfolios.latest_snapshot,
                principal=actor,
                portfolio_id=portfolio_id,
            )
        except PortfolioError as exc:
            raise ApiIdentityError(str(exc)) from exc
        return self._jsonable(asdict(row)) if row is not None else None

    @classmethod
    def _mandate_public(cls, row):
        return cls._public(
            row,
            "id",
            "portfolio_id",
            "suitability_id",
            "version",
            "profile",
            "mode",
            "status",
            "policy_version",
            "policy_digest",
            "digest",
            "limits",
            "effective_at",
        )

    @staticmethod
    def _public(row, *fields):
        return {field: row[field] for field in fields}

    async def _invoke(self, function, **kwargs) -> dict[str, Any]:
        try:
            row = await self._run_sync(function, **kwargs)
        except (PortfolioError, ValueError) as exc:
            raise ApiIdentityError(str(exc)) from exc
        payload = (
            asdict(row)
            if hasattr(row, "__dataclass_fields__")
            else {column.name: getattr(row, column.name) for column in row.__table__.columns}
        )
        return self._jsonable(payload)

    @classmethod
    def _jsonable(cls, value):
        if isinstance(value, dict):
            return {key: cls._jsonable(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, (UUID, datetime, Decimal)):
            return str(value)
        return value

    async def _principal(self, token: str, expected: ApiIdentityPrincipal) -> IdentityPrincipal:
        try:
            profile = await self._run_sync(self._identity.authenticate_session_token, token=token)
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

    @staticmethod
    async def _run_sync(function, /, *args, **kwargs):
        return await to_thread.run_sync(partial(function, *args, **kwargs))
