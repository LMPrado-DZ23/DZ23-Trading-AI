from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tradingagents.platform.api import (
    ApiSecuritySettings,
    AuthenticatedSession,
    IdentityPrincipal,
    create_platform_app,
)
from tradingagents.platform.api.identity import IdentityError
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
        self.failure_code: str | None = None

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
            "paused_portfolios": [],
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
                "mandate_profile": "moderate",
                "suitability_effective_profile": "moderate",
                "mode": "paper",
                "cash": Decimal("1000"),
                "reserved_cash": Decimal("0"),
                "available_cash": Decimal("1000"),
                "current_mandate_id": uuid4(),
                "mandate_status": "active",
                "pause_reason": None,
                "required_action": None,
                "circuit_breaker_open": False,
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
        if self.failure_code is not None:
            raise IdentityError(self.failure_code)
        return {
            "transaction_id": uuid4(),
            "mode": kwargs["mode"],
            "provenance": "user_declared",
            "verification_status": "unverified",
            "message": "Saldo não verificado.",
        }

    async def transition_mandate(self, **kwargs):
        self.calls.append(("transition", kwargs))
        if self.failure_code is not None:
            raise IdentityError(self.failure_code)
        return await self.activate_mandate(
            portfolio_id=kwargs["portfolio_id"],
            suitability_id=self.suitability_id,
            profile="moderate",
            mode="paper",
        )

    async def get_latest_snapshot(self, **kwargs):
        self.calls.append(("snapshot", kwargs))
        now = datetime.now(UTC)
        return {
            "portfolio_id": kwargs["portfolio_id"],
            "sequence": 1,
            "cash": Decimal("1000"),
            "reserved_cash": Decimal("0"),
            "market_value": Decimal("0"),
            "equity": Decimal("1000"),
            "nav_per_unit": Decimal("1"),
            "realized_pnl": Decimal("0"),
            "unrealized_pnl": Decimal("0"),
            "drawdown": Decimal("0"),
            "daily_drawdown": Decimal("0"),
            "unit_count": Decimal("1000"),
            "high_water_nav": Decimal("1"),
            "valuation_source": "market_data_pit",
            "valuation_observed_at": now,
            "valuation_available_at": now,
            "captured_at": now,
            "freshness": "fresh",
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


def test_untrusted_clients_cannot_create_sizing_or_valuation_inputs() -> None:
    client, portfolio, headers = _client()
    sizing = client.post(
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
    snapshot = client.post(
        f"/v1/portfolios/{portfolio.portfolio_id}/snapshots",
        headers=headers,
        json={"prices": {"BTC/USDT": "1"}, "captured_at": "2000-01-01T00:00:00Z"},
    )
    assert sizing.status_code == 404
    assert snapshot.status_code == 404


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


def test_read_only_snapshot_is_safe_and_public_funding_has_explicit_mode() -> None:
    client, portfolio, headers = _client()
    snapshot = client.get(f"/v1/portfolios/{portfolio.portfolio_id}/snapshot")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["portfolio_id"] == str(portfolio.portfolio_id)
    assert body["freshness"] == "fresh"
    assert "positions" not in body
    assert "prices" not in body
    assert "valuation_digest" not in body

    missing_mode = client.post(
        f"/v1/portfolios/{portfolio.portfolio_id}/funding",
        headers={**headers, "Idempotency-Key": "funding-api-missing-mode"},
        json={"amount": "100", "reference_id": "opening"},
    )
    assert missing_mode.status_code == 422
    assert missing_mode.json()["detail"]["code"] == "FUNDING_MODE_INVALID"
    backtest = client.post(
        f"/v1/portfolios/{portfolio.portfolio_id}/funding",
        headers={**headers, "Idempotency-Key": "funding-api-backtest-mode"},
        json={"amount": "100", "reference_id": "opening", "mode": "backtest"},
    )
    assert backtest.status_code == 422
    assert backtest.json()["detail"]["action"] == "select_shadow_or_paper_mode"
    paper = client.post(
        f"/v1/portfolios/{portfolio.portfolio_id}/funding",
        headers={**headers, "Idempotency-Key": "funding-api-paper-mode"},
        json={"amount": "100", "reference_id": "opening", "mode": "paper"},
    )
    assert paper.status_code == 201
    assert paper.json()["mode"] == "paper"
    assert paper.json()["verification_status"] == "unverified"


@pytest.mark.parametrize(
    ("code", "status", "message_fragment"),
    [
        ("SUITABILITY_STALE", 409, "mais recente"),
        ("SUITABILITY_REQUIRED", 409, "questionário"),
        ("PROFILE_EXCEEDS_SUITABILITY", 409, "mais conservador"),
        ("ACTIVE_MANDATE_REQUIRED", 409, "mandato"),
        ("BASELINE_SNAPSHOT_REQUIRED", 409, "fotografia de risco"),
        ("MANDATE_TRANSITION_INVALID", 409, "transição"),
        ("PAPER_CAPITAL_LOCKED_AFTER_TRADING", 409, "primeira posição"),
        ("FUNDING_MODE_MISMATCH", 409, "modo ativo"),
        ("AMOUNT_BELOW_UNIT_PRECISION", 422, "precisão contábil"),
        ("IDEMPOTENCY_KEY_INVALID", 422, "Idempotency-Key"),
        ("LEDGER_WRITE_CONFLICT", 409, "saldo mudou"),
        ("DATABASE_TIME_UNAVAILABLE", 503, "temporariamente"),
    ],
)
def test_portfolio_recoverable_errors_keep_stable_public_codes(
    code: str, status: int, message_fragment: str
) -> None:
    from fastapi import HTTPException

    from tradingagents.platform.api.app import _raise_portfolio_error
    from tradingagents.platform.api.identity import IdentityError

    with pytest.raises(HTTPException) as caught:
        _raise_portfolio_error(IdentityError(code))
    assert caught.value.status_code == status
    assert caught.value.detail["code"] == code
    assert message_fragment in caught.value.detail["message"]
    assert isinstance(caught.value.detail["retryable"], bool)


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        ("AMOUNT_BELOW_UNIT_PRECISION", 422, False),
        ("IDEMPOTENCY_KEY_INVALID", 422, False),
        ("LEDGER_WRITE_CONFLICT", 409, True),
        ("DATABASE_TIME_UNAVAILABLE", 503, True),
    ],
)
def test_portfolio_error_contract_is_preserved_through_http_route(
    code: str, status: int, retryable: bool
) -> None:
    client, portfolio, headers = _client()
    portfolio.failure_code = code
    response = client.post(
        f"/v1/portfolios/{portfolio.portfolio_id}/funding",
        headers={**headers, "Idempotency-Key": "route-error-contract-0001"},
        json={"amount": "1", "reference_id": "route-contract", "mode": "paper"},
    )
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["retryable"] is retryable
    assert detail["action"]


def test_breaker_resume_http_error_offers_only_an_existing_review_action() -> None:
    client, portfolio, headers = _client()
    portfolio.failure_code = "CIRCUIT_BREAKER_OPEN"
    response = client.patch(
        f"/v1/portfolios/{portfolio.portfolio_id}/mandate",
        headers=headers,
        json={"target_status": "active"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "CIRCUIT_BREAKER_OPEN",
        "message": (
            "A operação foi bloqueada pelo limite de risco da carteira. "
            "Revise a retenção de risco; a liberação ainda não está disponível neste marco."
        ),
        "retryable": False,
        "action": "review_risk_hold",
    }


def test_public_portfolio_error_table_covers_every_reachable_service_code() -> None:
    from tradingagents.platform.api.app import _PORTFOLIO_PUBLIC_ERRORS

    reachable = {
        "ACTIVE_MANDATE_REQUIRED",
        "AMOUNT_BELOW_UNIT_PRECISION",
        "AMOUNT_INVALID",
        "BASELINE_SNAPSHOT_REQUIRED",
        "CAPITAL_MODE_NOT_ENABLED",
        "CIRCUIT_BREAKER_OPEN",
        "CUSTOM_PROFILE_REQUIRES_REVIEW",
        "DATABASE_TIME_UNAVAILABLE",
        "FUNDING_MODE_MISMATCH",
        "FUNDING_REFERENCE_INVALID",
        "IDEMPOTENCY_KEY_INVALID",
        "IDEMPOTENCY_PAYLOAD_MISMATCH",
        "LEDGER_WRITE_CONFLICT",
        "MANDATE_TRANSITION_INVALID",
        "PAPER_CAPITAL_LOCKED_AFTER_TRADING",
        "POLICY_VERSION_MISMATCH",
        "PORTFOLIO_ALREADY_EXISTS",
        "PORTFOLIO_FORBIDDEN",
        "PORTFOLIO_INPUT_INVALID",
        "PORTFOLIO_NOT_ACTIVE",
        "PORTFOLIO_NOT_FOUND",
        "PROFILE_EXCEEDS_SUITABILITY",
        "RESERVATION_NOT_FOUND",
        "SNAPSHOT_NOT_FOUND",
        "SUITABILITY_ANSWERS_INVALID",
        "SUITABILITY_REQUIRED",
        "SUITABILITY_STALE",
        "SUITABILITY_VALIDITY_INVALID",
    }
    assert reachable <= _PORTFOLIO_PUBLIC_ERRORS.keys()
