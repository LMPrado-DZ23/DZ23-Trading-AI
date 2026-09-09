from __future__ import annotations

import hashlib
import hmac
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
_TOKEN_PEPPER = b"p" * 32
_CAPABILITY_MASTER = b"test-session-token-pepper-32-bytes-minimum-2026"
_CAPABILITY_KEY = hmac.new(
    _CAPABILITY_MASTER, b"portfolio-capability-v1", hashlib.sha256
).digest()


def _principal(identity: IdentityService, label: str) -> IdentityPrincipal:
    suffix = uuid4().hex[:10]
    email = f"portfolio-{label.lower()}-{suffix}@example.test"
    password = "correct horse battery staple"
    row = identity.register_tenant(
        tenant_name=f"Portfolio {label}",
        tenant_slug=f"portfolio-{label.lower()}-{suffix}",
        owner_email=email,
        display_name=f"Owner {label}",
        password=password,
    )
    session = identity.authenticate(
        tenant_id=row.tenant_id,
        email=email,
        password=password,
    )
    return IdentityPrincipal(
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        membership_id=row.membership_id,
        session_id=session.session_id,
        role=MembershipRole.OWNER,
        session_token_hash=TokenHasher(_TOKEN_PEPPER).digest(
            session.token, purpose=f"session:{row.tenant_id}"
        ),
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
    # Keep trusted valuation timestamps on the same clock used by the
    # PostgreSQL timeline gate. Host/container clocks can differ slightly.
    with service._sessions() as db, db.begin():
        service._set_portfolio_scope(db, principal)
        observed = service._database_now(db)
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
    identity = IdentityService(factory, token_hasher=TokenHasher(_TOKEN_PEPPER))
    first = _principal(identity, "A")
    second = _principal(identity, "B")
    first_service = PortfolioService(
        factory,
        idempotency_digest_key=b"a" * 32,
        capability_signing_key=_CAPABILITY_KEY,
    )
    second_service = PortfolioService(
        factory,
        idempotency_digest_key=b"a" * 32,
        capability_signing_key=_CAPABILITY_KEY,
    )
    suitability, portfolio = _ready(first_service, first, "A")
    _other_suitability, other_portfolio = _ready(second_service, second, "B")

    # A caller-controlled tenant GUC is not authentication. Without a signed,
    # transaction-bound session context, even same-tenant rows fail closed.
    with pytest.raises(DBAPIError), factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        db.scalar(select(Portfolio).where(Portfolio.id == portfolio.id))
    assert {item.id for item in first_service.list_portfolios(principal=first)} == {
        portfolio.id
    }
    assert {item.id for item in second_service.list_portfolios(principal=second)} == {
        other_portfolio.id
    }

    # Reproduce the original cross-tenant finding: the app role can discover a
    # victim session through the legacy identity RLS, but that session id plus a
    # forged tenant GUC is still insufficient to cross the portfolio boundary.
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(second.tenant_id)},
        )
        discovered = connection.scalar(
            text("SELECT id FROM identity_sessions WHERE id = :session_id"),
            {"session_id": str(second.session_id)},
        )
        assert discovered == second.session_id
        connection.execute(
            text("SELECT set_config('app.portfolio_session_id', :session_id, true)"),
            {"session_id": str(second.session_id)},
        )
        connection.scalar(
            text("SELECT count(*) FROM portfolios WHERE id = :portfolio_id"),
            {"portfolio_id": str(other_portfolio.id)},
        )

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
        local = PortfolioService(
            factory,
            idempotency_digest_key=b"a" * 32,
            capability_signing_key=_CAPABILITY_KEY,
        )
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
    assert consume_guard.currency == "BRL"
    assert timedelta(seconds=30) <= (
        consume_guard.expires_at - consume_guard.created_at
    ) <= timedelta(hours=1)
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
    fractional_ttl = first_service.reserve_cash(
        principal=first,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest="a" * 64,
        idempotency_key="pg-fractional-ttl-0001",
        ttl=timedelta(seconds=30, microseconds=1),
    )
    assert fractional_ttl.expires_at - fractional_ttl.created_at == timedelta(
        seconds=30, microseconds=1
    )

    # Operation capabilities bind every capital parameter and are one-use.
    cap_key_hash = first_service._key_hash("pg-capability-replay-0001")
    cap_now = datetime.now(UTC)
    reserve_capability = first_service._mint_capability(
        principal=first,
        audience="portfolio.reserve.v1",
        fields=(
            str(portfolio.id),
            "1",
            "b" * 64,
            cap_key_hash,
            "30000000",
        ),
        now=cap_now,
    )
    capability_sql = text(
        "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
        ":portfolio_id,:amount,:purpose,:key_hash,:ttl_microseconds,:cap_kid,"
        ":cap_session_id,:cap_token_hash,:cap_issued_at,:cap_expires_at,"
        ":cap_nonce,:cap_mac)"
    )
    capability_params = {
        "portfolio_id": str(portfolio.id),
        "amount": Decimal("1"),
        "purpose": "b" * 64,
        "key_hash": cap_key_hash,
        "ttl_microseconds": 30_000_000,
    } | reserve_capability
    with pytest.raises(DBAPIError), factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        db.execute(capability_sql, capability_params | {"amount": Decimal("2")})
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        db.execute(capability_sql, capability_params).one()
    with pytest.raises(DBAPIError), factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        db.execute(capability_sql, capability_params)
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege(current_user, "
                "'public.portfolio_cash_reservations', 'INSERT')"
            )
        )
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege(current_user, "
                "'public.portfolio_cash_reservations', 'UPDATE')"
            )
        )
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege(current_user, "
                "'public.portfolio_cash_reservations', 'DELETE')"
            )
        )
        assert connection.scalar(
            text(
                "SELECT has_function_privilege(current_user, "
                "'tradingagents_private.portfolio_reserve_cash_v1"
                "(uuid,numeric,text,text,bigint,text,uuid,text,bigint,bigint,text,text)', "
                "'EXECUTE')"
            )
        )
        assert connection.scalar(
            text(
                "SELECT to_regprocedure("
                "'tradingagents_private.portfolio_consume_cash_v1(uuid)') IS NULL"
            )
        )
        assert connection.scalar(
            text(
                "SELECT count(*) = 2 AND bool_and(p.prosecdef) AND "
                "bool_and(pg_get_userbyid(p.proowner) = "
                "'tradingagents_reservation_api') AND "
                "bool_and(array_to_string(p.proconfig, ',') LIKE "
                "'%search_path=pg_catalog%') AND "
                "bool_and(array_to_string(p.proconfig, ',') LIKE '%row_security=on%') "
                "FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid = p.pronamespace WHERE n.nspname = 'tradingagents_private' "
                "AND p.proname IN "
                "('portfolio_reserve_cash_v1','portfolio_release_cash_v1')"
            )
        )
        assert connection.scalar(
            text(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid = p.pronamespace CROSS JOIN LATERAL "
                "aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl "
                "WHERE n.nspname = 'tradingagents_private' AND acl.grantee = 0 "
                "AND acl.privilege_type = 'EXECUTE')"
            )
        )
        role_state = connection.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit, "
                "rolreplication, rolbypassrls FROM pg_roles "
                "WHERE rolname = 'tradingagents_reservation_api'"
            )
        ).one()
        assert not any(role_state)
        assert connection.scalar(
            text(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_auth_members membership "
                "JOIN pg_roles guard ON guard.rolname = "
                "'tradingagents_reservation_api' WHERE membership.roleid = guard.oid "
                "OR membership.member = guard.oid)"
            )
        )

    # The web role cannot manufacture reservation rows or status transitions.
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "INSERT INTO portfolio_cash_reservations "
                "(id, tenant_id, portfolio_id, mandate_id, idempotency_key_hash, "
                "currency, amount, status, purpose_digest, expires_at, created_at) "
                "VALUES (:id, :tenant, :portfolio, :mandate, :key_hash, 'USD', 999999999, "
                "'active', :purpose, clock_timestamp() + interval '100 years', "
                "clock_timestamp())"
            ),
            {
                "id": str(uuid4()),
                "tenant": str(first.tenant_id),
                "portfolio": str(portfolio.id),
                "mandate": str(stale_replay_guard.mandate_id),
                "key_hash": "1" * 64,
                "purpose": "1" * 64,
            },
        )
    for invalid_amount in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(first.tenant_id)},
            )
            connection.execute(
                text(
                    "SELECT set_config('app.portfolio_session_id', :session_id, true)"
                ),
                {"session_id": str(first.session_id)},
            )
            connection.execute(
                text(
                    "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
                    ":portfolio, CAST(:amount AS numeric), :purpose, :key_hash, 60)"
                ),
                {
                    "portfolio": str(portfolio.id),
                    "amount": invalid_amount,
                    "purpose": "9" * 64,
                    "key_hash": invalid_amount.encode().hex().ljust(64, "0")[:64],
                },
            )
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "SELECT set_config('app.portfolio_session_id', :session_id, true)"
            ),
            {"session_id": str(first.session_id)},
        )
        connection.execute(
            text(
                "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
                ":portfolio, 1, :purpose, :key_hash, 29)"
            ),
            {
                "portfolio": str(portfolio.id),
                "purpose": "8" * 64,
                "key_hash": "7" * 64,
            },
        )
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

    # The narrow RPC independently rejects an oversized reserve and a tenant
    # scope that does not match the authenticated session capability.
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "SELECT set_config('app.portfolio_session_id', :session_id, true)"
            ),
            {"session_id": str(first.session_id)},
        )
        connection.execute(
            text(
                "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
                ":portfolio, 999999999, :purpose, :key_hash, 3600)"
            ),
            {
                "portfolio": str(portfolio.id),
                "key_hash": "0" * 64,
                "purpose": "0" * 64,
            },
        )
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(second.tenant_id)},
        )
        connection.execute(
            text(
                "SELECT set_config('app.portfolio_session_id', :session_id, true)"
            ),
            {"session_id": str(first.session_id)},
        )
        connection.execute(
            text(
                "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
                ":portfolio, 1, :purpose, :key_hash, 60)"
            ),
            {
                "portfolio": str(other_portfolio.id),
                "key_hash": "a" * 64,
                "purpose": "b" * 64,
            },
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
    breaker_id = uuid4()
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        active_id = db.scalar(
            select(Portfolio.current_mandate_id).where(Portfolio.id == portfolio.id)
        )
    first_service.transition_mandate(
        principal=first, portfolio_id=portfolio.id, target_status="paused"
    )
    # Consuming is unavailable in M4. Release stays available through the
    # narrow risk-reducing RPC after a mandate pause; direct DML remains denied.
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
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "SELECT set_config('app.portfolio_session_id', :session_id, true)"
            ),
            {"session_id": str(first.session_id)},
        )
        connection.execute(
            text(
                "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
                ":portfolio, 1, :purpose, :key_hash, 60)"
            ),
            {
                "portfolio": str(portfolio.id),
                "purpose": "6" * 64,
                "key_hash": "5" * 64,
            },
        )
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        version_before_release = db.scalar(
            select(Portfolio.balance_version).where(Portfolio.id == portfolio.id)
        )
    assert (
        first_service.release_reservation(
            principal=first,
            reservation_id=consume_guard.id,
        )
        == "released"
    )
    assert (
        first_service.release_reservation(
            principal=first,
            reservation_id=consume_guard.id,
        )
        == "released"
    )
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        assert db.scalar(
            select(Portfolio.balance_version).where(Portfolio.id == portfolio.id)
        ) == version_before_release + 1
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
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

    # A direct caller of the narrow RPC cannot reserve while the deterministic
    # circuit breaker is open, even with an otherwise valid session scope.
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        db.execute(
            text(
                "INSERT INTO portfolio_circuit_breakers "
                "(id, tenant_id, portfolio_id, mandate_id, kind, status, "
                "observed_value, threshold_value, evidence_digest, triggered_at) "
                "VALUES (:id, :tenant, :portfolio, :mandate, 'maximum_drawdown', "
                "'open', 0.30, 0.20, :digest, clock_timestamp())"
            ),
            {
                "id": str(breaker_id),
                "tenant": str(first.tenant_id),
                "portfolio": str(portfolio.id),
                "mandate": str(replacement.id),
                "digest": "c" * 64,
            },
        )
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "UPDATE portfolio_circuit_breakers SET status='reset', "
                "reset_at=clock_timestamp(), reset_by_user_id=:user_id WHERE id=:id"
            ),
            {"id": str(breaker_id), "user_id": str(first.user_id)},
        )
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        connection.execute(
            text(
                "SELECT set_config('app.portfolio_session_id', :session_id, true)"
            ),
            {"session_id": str(first.session_id)},
        )
        connection.execute(
            text(
                "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
                ":portfolio, 1, :purpose, :key_hash, 60)"
            ),
            {
                "portfolio": str(portfolio.id),
                "purpose": "d" * 64,
                "key_hash": "e" * 64,
            },
        )
    engine.dispose()
