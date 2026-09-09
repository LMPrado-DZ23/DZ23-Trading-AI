from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
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
from tradingagents.platform.portfolio import (
    Portfolio,
    PortfolioError,
    PortfolioService,
    TrustedValuation,
)

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
    observed = datetime.now(UTC)
    service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=TrustedValuation(
            prices={},
            source="market_data_pit",
            observed_at=observed,
            available_at=observed,
            evidence_digest="7" * 64,
        ),
    )
    return suitability, portfolio


@pytest.mark.skipif(not APP_URL, reason="PostgreSQL app test URL is not configured")
def test_portfolio_rls_append_only_balancing_and_reservation_race(monkeypatch) -> None:
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

    # Database policy is stricter than the enum: M4 cannot persist a live mandate.
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        live_mandate_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO portfolio_mandates "
                "(id, tenant_id, portfolio_id, suitability_id, version, profile, mode, "
                "status, policy_version, policy_digest, digest, limits, "
                "created_by_user_id, effective_at, created_at) "
                "SELECT :id, tenant_id, portfolio_id, suitability_id, version + 1, "
                "profile, 'live_automated', 'active', policy_version, policy_digest, "
                ":digest, limits, created_by_user_id, clock_timestamp(), clock_timestamp() "
                "FROM portfolio_mandates WHERE id = "
                "(SELECT current_mandate_id FROM portfolios WHERE id = :portfolio)"
            ),
            {
                "id": str(live_mandate_id),
                "portfolio": str(portfolio.id),
                "digest": "8" * 64,
            },
        )
        connection.execute(
            text("UPDATE portfolios SET current_mandate_id = :id WHERE id = :portfolio"),
            {"id": str(live_mandate_id), "portfolio": str(portfolio.id)},
        )

    # A direct newer suitability insert cannot leave the previous mandate active.
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "INSERT INTO portfolio_suitability_assessments "
                "(id, tenant_id, user_id, version, requested_profile, maximum_profile, "
                "effective_profile, status, policy_version, answers, digest, "
                "assessed_at, expires_at) "
                "SELECT :id, :tenant, :actor, max(version) + 1, 'preservation', "
                "'preservation', 'preservation', 'valid', 1, CAST('{}' AS json), "
                ":digest, clock_timestamp(), clock_timestamp() + interval '30 days' "
                "FROM portfolio_suitability_assessments WHERE tenant_id = :tenant "
                "AND user_id = :actor"
            ),
            {
                "id": str(uuid4()),
                "tenant": str(first.tenant_id),
                "actor": str(first.user_id),
                "digest": "9" * 64,
            },
        )

    # Capital decisions use the database clock. A backward host-clock adjustment
    # must not make the freshly committed mandate appear to be future-dated.
    monkeypatch.setattr(
        PortfolioService,
        "_now",
        staticmethod(lambda: datetime.now(UTC) - timedelta(days=1)),
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
    consume_guard = first_service.reserve_cash(
        principal=first,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest="4" * 64,
        idempotency_key="pg-consume-guard-0001",
    )
    expire_guard = first_service.reserve_cash(
        principal=first,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest="3" * 64,
        idempotency_key="pg-expire-guard-0001",
    )
    stale_replay_guard = first_service.reserve_cash(
        principal=first,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest="2" * 64,
        idempotency_key="pg-stale-replay-guard-0001",
    )
    expired_replay_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "INSERT INTO portfolio_cash_reservations "
                "(id, tenant_id, portfolio_id, mandate_id, idempotency_key_hash, "
                "currency, amount, status, purpose_digest, expires_at, created_at) "
                "VALUES (:id, :tenant, :portfolio, :mandate, :key_hash, 'BRL', 1, "
                "'active', :purpose, clock_timestamp() - interval '1 second', "
                "clock_timestamp() - interval '1 minute')"
            ),
            {
                "id": str(expired_replay_id),
                "tenant": str(first.tenant_id),
                "portfolio": str(portfolio.id),
                "mandate": str(stale_replay_guard.mandate_id),
                "key_hash": first_service._key_hash("pg-expired-replay-0001"),
                "purpose": "1" * 64,
            },
        )
    with pytest.raises(PortfolioError, match="IDEMPOTENCY_RESERVATION_FINALIZED"):
        first_service.reserve_cash(
            principal=first,
            portfolio_id=portfolio.id,
            amount=Decimal("1"),
            purpose_digest="1" * 64,
            idempotency_key="pg-expired-replay-0001",
            ttl=timedelta(seconds=30),
        )

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
                "provenance, verification_status, unit_delta, nav_per_unit, "
                "created_by_user_id, occurred_at) VALUES "
                "(:id, :tenant, :portfolio, :idem, 'funding', 'test', 'unbalanced', "
                "'BRL', 1, :digest, 'internal', 'internal', 0, 1, "
                ":actor, clock_timestamp())"
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

    # A transaction without postings is invalid even when its numeric sum would be zero.
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "INSERT INTO portfolio_ledger_transactions "
                "(id, tenant_id, portfolio_id, idempotency_key_hash, event_type, "
                "reference_type, reference_id, currency, amount, digest, "
                "provenance, verification_status, unit_delta, nav_per_unit, "
                "created_by_user_id, occurred_at) VALUES "
                "(:id, :tenant, :portfolio, :idem, 'fee', 'test', 'empty', "
                "'BRL', 1, :digest, 'internal', 'internal', 0, 1, "
                ":actor, clock_timestamp())"
            ),
            {
                "id": str(uuid4()),
                "tenant": str(first.tenant_id),
                "portfolio": str(portfolio.id),
                "idem": "d" * 64,
                "digest": "c" * 64,
                "actor": str(first.user_id),
            },
        )

    # Postings cannot be redirected into another portfolio owned by the same tenant.
    other_same_tenant = first_service.create_portfolio(
        principal=first, name="Carteira Cross Posting", base_currency="BRL"
    )
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
                "provenance, verification_status, unit_delta, nav_per_unit, "
                "created_by_user_id, occurred_at) VALUES "
                "(:id, :tenant, :portfolio, :idem, 'fee', 'test', 'cross-portfolio', "
                "'BRL', 1, :digest, 'internal', 'internal', 0, 1, "
                ":actor, clock_timestamp())"
            ),
            {
                "id": str(transaction_id),
                "tenant": str(first.tenant_id),
                "portfolio": str(portfolio.id),
                "idem": "b" * 64,
                "digest": "a" * 64,
                "actor": str(first.user_id),
            },
        )
        for account, quantity in (("cash", 1), ("external", -1)):
            connection.execute(
                text(
                    "INSERT INTO portfolio_ledger_postings "
                    "(id, tenant_id, transaction_id, portfolio_id, account, asset, quantity) "
                    "VALUES (:id, :tenant, :tx, :portfolio, :account, 'BRL', :quantity)"
                ),
                {
                    "id": str(uuid4()),
                    "tenant": str(first.tenant_id),
                    "tx": str(transaction_id),
                    "portfolio": str(other_same_tenant.id),
                    "account": account,
                    "quantity": quantity,
                },
            )

    # The mutable pointer cannot be rolled back to an older active history event.
    with factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        active_id = db.scalar(
            select(Portfolio.current_mandate_id).where(Portfolio.id == portfolio.id)
        )
    first_service.transition_mandate(
        principal=first, portfolio_id=portfolio.id, target_status="paused"
    )
    # Consuming is a capital action and remains forbidden after the mandate is
    # paused. Released/expired are fail-safe unwind transitions and remain legal.
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "UPDATE portfolio_cash_reservations SET status = 'consumed', "
                "consumed_at = clock_timestamp() WHERE id = :id"
            ),
            {"id": str(consume_guard.id)},
        )
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "UPDATE portfolio_cash_reservations SET status = 'released', "
                "released_at = clock_timestamp() WHERE id = :released_id"
            ),
            {"released_id": str(consume_guard.id)},
        )
        connection.execute(
            text(
                "UPDATE portfolio_cash_reservations SET status = 'expired' "
                "WHERE id = :expired_id"
            ),
            {"expired_id": str(expire_guard.id)},
        )
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text("UPDATE portfolios SET current_mandate_id = :old WHERE id = :portfolio"),
            {"old": str(active_id), "portfolio": str(portfolio.id)},
        )
    replacement = first_service.transition_mandate(
        principal=first,
        portfolio_id=portfolio.id,
        target_status="active",
    )
    assert replacement.id != stale_replay_guard.mandate_id
    with pytest.raises(PortfolioError, match="IDEMPOTENCY_RESERVATION_FINALIZED"):
        first_service.reserve_cash(
            principal=first,
            portfolio_id=portfolio.id,
            amount=stale_replay_guard.amount,
            purpose_digest=stale_replay_guard.purpose_digest,
            idempotency_key="pg-stale-replay-guard-0001",
        )

    # A semantically inverted funding event is rejected even if its two postings balance.
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
                "reference_type, reference_id, currency, amount, digest, provenance, "
                "verification_status, unit_delta, nav_per_unit, created_by_user_id, "
                "occurred_at) VALUES (:id, :tenant, :portfolio, :idem, 'paper_funding', "
                "'user_declared_paper', 'semantic-bypass', 'BRL', 100, :digest, "
                "'user_declared', 'unverified', -100, 1, :actor, clock_timestamp())"
            ),
            {
                "id": str(transaction_id),
                "tenant": str(first.tenant_id),
                "portfolio": str(portfolio.id),
                "idem": "6" * 64,
                "digest": "5" * 64,
                "actor": str(first.user_id),
            },
        )
        for account, quantity in (("cash", 100), ("external", -100)):
            connection.execute(
                text(
                    "INSERT INTO portfolio_ledger_postings "
                    "(id, tenant_id, transaction_id, portfolio_id, account, asset, quantity) "
                    "VALUES (:id, :tenant, :tx, :portfolio, :account, 'BRL', :quantity)"
                ),
                {
                    "id": str(uuid4()),
                    "tenant": str(first.tenant_id),
                    "tx": str(transaction_id),
                    "portfolio": str(portfolio.id),
                    "account": account,
                    "quantity": quantity,
                },
            )
        connection.execute(
            text("UPDATE portfolios SET unit_count = unit_count - 100 WHERE id = :portfolio"),
            {"portfolio": str(portfolio.id)},
        )

    # Derived portfolio caches cannot be changed independently of ledger/snapshot sources.
    for assignment in ("unit_count = unit_count + 1", "last_nav_per_unit = 42"):
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(first.tenant_id)},
            )
            connection.execute(
                text(f"UPDATE portfolios SET {assignment} WHERE id = :portfolio"),
                {"portfolio": str(portfolio.id)},
            )
    engine.dispose()
