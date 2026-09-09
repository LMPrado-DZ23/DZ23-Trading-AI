from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tradingagents.platform.contracts import RiskProfile, RunMode
from tradingagents.platform.identity import (
    IdentityPrincipal,
    IdentityService,
    MembershipRole,
    TokenHasher,
)
from tradingagents.platform.portfolio import Portfolio, PortfolioError, PortfolioService

APP_URL = os.environ.get("TRADINGAGENTS_TEST_POSTGRES_APP_URL")


def _principal(identity: IdentityService, label: str) -> IdentityPrincipal:
    suffix = uuid4().hex[:10]
    row = identity.register_tenant(
        tenant_name=f"Portfolio {label}",
        tenant_slug=f"portfolio-{label.lower()}-{suffix}",
        owner_email=f"portfolio-{label.lower()}-{suffix}@example.test",
        display_name=f"Owner {label}",
        password="correct horse battery staple",
    )
    return IdentityPrincipal(
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        membership_id=row.membership_id,
        session_id=uuid4(),
        role=MembershipRole.OWNER,
    )


def _ready(service: PortfolioService, principal: IdentityPrincipal, label: str):
    suitability = service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.MODERATE,
        answers={
            "horizon_years": 10,
            "maximum_loss_percent": 30,
            "emergency_fund_months": 12,
            "income_stability": "stable",
            "experience": "experienced",
        },
    )
    portfolio = service.create_portfolio(
        principal=principal, name=f"Carteira {label}", base_currency="BRL"
    )
    service.activate_mandate(
        principal=principal,
        portfolio_id=portfolio.id,
        suitability_id=suitability.id,
        profile=RiskProfile.MODERATE,
        mode=RunMode.PAPER,
    )
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("100"),
        idempotency_key=f"pg-funding-{label}-0001",
        reference_id=f"deposit-{label}",
    )
    return suitability, portfolio


@pytest.mark.skipif(not APP_URL, reason="PostgreSQL app test URL is not configured")
def test_portfolio_rls_append_only_balancing_and_reservation_race() -> None:
    engine = create_engine(APP_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(factory, token_hasher=TokenHasher(b"p" * 32))
    first = _principal(identity, "A")
    second = _principal(identity, "B")
    first_service = PortfolioService(factory, idempotency_digest_key=b"a" * 32)
    second_service = PortfolioService(factory, idempotency_digest_key=b"a" * 32)
    suitability, portfolio = _ready(first_service, first, "A")
    _other_suitability, other_portfolio = _ready(second_service, second, "B")

    with factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        assert db.scalar(select(Portfolio).where(Portfolio.id == other_portfolio.id)) is None
        assert db.scalar(select(Portfolio).where(Portfolio.id == portfolio.id)) is not None

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text("UPDATE portfolio_suitability_assessments SET status = 'revoked' WHERE id = :id"),
            {"id": str(suitability.id)},
        )

    barrier = threading.Barrier(2)

    def reserve(index: int) -> str:
        local = PortfolioService(factory, idempotency_digest_key=b"a" * 32)
        barrier.wait()
        try:
            local.reserve_cash(
                principal=first,
                portfolio_id=portfolio.id,
                amount=Decimal("60"),
                purpose_digest=str(index) * 64,
                idempotency_key=f"pg-reservation-race-{index:02d}",
            )
            return "reserved"
        except PortfolioError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(reserve, (1, 2)))
    assert sorted(outcomes) == ["INSUFFICIENT_AVAILABLE_CASH", "reserved"]

    # The database itself rejects a partially written double-entry event at commit.
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        transaction_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO portfolio_ledger_transactions "
                "(id, tenant_id, portfolio_id, idempotency_key_hash, event_type, "
                "reference_type, reference_id, currency, amount, digest, "
                "created_by_user_id, occurred_at) VALUES "
                "(:id, :tenant, :portfolio, :idem, 'funding', 'test', 'unbalanced', "
                "'BRL', 1, :digest, :actor, clock_timestamp())"
            ),
            {
                "id": str(transaction_id),
                "tenant": str(first.tenant_id),
                "portfolio": str(portfolio.id),
                "idem": "f" * 64,
                "digest": "e" * 64,
                "actor": str(first.user_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO portfolio_ledger_postings "
                "(id, tenant_id, transaction_id, portfolio_id, account, asset, quantity) "
                "VALUES (:id, :tenant, :tx, :portfolio, 'cash', 'BRL', 1)"
            ),
            {
                "id": str(uuid4()),
                "tenant": str(first.tenant_id),
                "tx": str(transaction_id),
                "portfolio": str(portfolio.id),
            },
        )
    engine.dispose()
