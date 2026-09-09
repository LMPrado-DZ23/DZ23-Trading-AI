from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from tradingagents.platform.api import (
    ApiSecuritySettings,
    AuthenticatedSession,
    IdentityPrincipal,
    create_platform_app,
)
from tradingagents.platform.api.security import (
    SESSION_COOKIE,
    InMemorySlidingWindowRateLimiter,
)


class VerifiedIdentity:
    def __init__(self):
        self.session = AuthenticatedSession(
            token="portfolio-session-token-with-at-least-32-characters",
            principal=IdentityPrincipal(
                user_id=uuid4(),
                tenant_id=uuid4(),
                email="portfolio@example.test",
                display_name="Portfolio User",
                roles=("owner",),
                email_verified=True,
                session_id=uuid4(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        )

    async def resolve_session(self, **_kwargs):
        return self.session.principal

    async def ready(self):
        return True


class FakePortfolio:
    def __init__(self):
        self.calls = []
        self.portfolio_id = uuid4()
        self.suitability_id = uuid4()

    async def assess_suitability(self, **kwargs):
        self.calls.append(("assess", kwargs))
        now = datetime.now(UTC)
        return {
            "id": self.suitability_id,
            "version": 1,
            "requested_profile": kwargs["requested_profile"],
            "maximum_profile": "moderate",
            "effective_profile": "moderate",
            "status": "valid",
            "policy_version": 1,
            "digest": "a" * 64,
            "assessed_at": now,
            "expires_at": now + timedelta(days=365),
        }

    async def create_portfolio(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {
            "id": self.portfolio_id,
            "name": kwargs["name"],
            "base_currency": kwargs["base_currency"].upper(),
            "status": "active",
        }

    async def list_portfolios(self, **kwargs):
        self.calls.append(("list", kwargs))
        return (
            {
                "id": self.portfolio_id,
                "name": "Principal",
                "base_currency": "BRL",
                "status": "active",
                "effective_profile": "moderate",
                "mode": "paper",
                "cash": Decimal("1000"),
                "reserved_cash": Decimal("0"),
                "available_cash": Decimal("1000"),
            },
        )

    async def activate_mandate(self, **kwargs):
        self.calls.append(("mandate", kwargs))
        return {
            "id": uuid4(),
            "portfolio_id": kwargs["portfolio_id"],
            "suitability_id": kwargs["suitability_id"],
            "version": 1,
            "profile": kwargs["profile"],
            "mode": kwargs["mode"],
            "status": "active",
            "policy_version": 1,
            "policy_digest": "b" * 64,
            "digest": "c" * 64,
            "limits": {},
            "effective_at": datetime.now(UTC),
        }

    async def record_funding(self, **kwargs):
        self.calls.append(("funding", kwargs))
        return uuid4()

    async def deterministic_size(self, **kwargs):
        self.calls.append(("sizing", kwargs))
        return {
            "target_weight": Decimal("0.1"),
            "target_notional": Decimal("100"),
            "order_notional": Decimal("50"),
            "quantity": Decimal("0.5"),
            "limited_by": ("order_cap",),
        }


def _client():
    identity = VerifiedIdentity()
    portfolio = FakePortfolio()
    app = create_platform_app(
        identity_service=identity,
        security=ApiSecuritySettings(csrf_secret=b"portfolio-api-csrf-secret-value-32"),
        rate_limiter=InMemorySlidingWindowRateLimiter(),
        portfolio_service=portfolio,
    )
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set(SESSION_COOKIE, identity.session.token)
    csrf = client.get("/v1/auth/csrf").json()["csrf_token"]
    return client, portfolio, {"x-csrf-token": csrf}


def test_suitability_and_portfolio_journey_is_explicit_and_authenticated() -> None:
    client, portfolio, headers = _client()
    suitability = client.post(
        "/v1/portfolio/suitability",
        headers=headers,
        json={
            "requested_profile": "aggressive",
            "horizon_years": 10,
            "maximum_loss_percent": 30,
            "emergency_fund_months": 12,
            "income_stability": "stable",
            "experience": "experienced",
        },
    )
    assert suitability.status_code == 201
    assert suitability.json()["effective_profile"] == "moderate"
    created = client.post(
        "/v1/portfolios",
        headers=headers,
        json={"name": "Principal", "base_currency": "brl"},
    )
    assert created.status_code == 201
    assert created.json()["base_currency"] == "BRL"
    listed = client.get("/v1/portfolios")
    assert listed.status_code == 200
    assert listed.json()[0]["available_cash"] == "1000"
    assert [name for name, _kwargs in portfolio.calls] == ["assess", "create", "list"]


def test_public_sizing_input_cannot_supply_executable_quantity() -> None:
    client, portfolio, headers = _client()
    response = client.post(
        f"/v1/portfolios/{portfolio.portfolio_id}/sizing-preview",
        headers=headers,
        json={
            "conviction": "0.8",
            "price": "100",
            "realized_annual_volatility": "0.5",
            "average_daily_notional": "100000",
            "quantity": "999999",
        },
    )
    assert response.status_code == 422
    assert not [call for call in portfolio.calls if call[0] == "sizing"]


def test_live_mode_is_not_in_the_mandate_api_schema() -> None:
    client, portfolio, headers = _client()
    response = client.post(
        f"/v1/portfolios/{portfolio.portfolio_id}/mandates",
        headers=headers,
        json={
            "suitability_id": str(portfolio.suitability_id),
            "profile": "moderate",
            "mode": "live_automated",
        },
    )
    assert response.status_code == 422
    assert not [call for call in portfolio.calls if call[0] == "mandate"]


def test_mutating_portfolio_routes_require_csrf() -> None:
    client, _portfolio, _headers = _client()
    response = client.post(
        "/v1/portfolios",
        json={"name": "Sem CSRF", "base_currency": "BRL"},
    )
    assert response.status_code == 403
