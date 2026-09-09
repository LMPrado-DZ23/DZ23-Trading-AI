from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
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
    CashReservation,
    Portfolio,
    PortfolioError,
    PortfolioMaintenance,
    PortfolioService,
    PortfolioSnapshot,
    TrustedValuation,
)

APP_URL = os.environ.get("TRADINGAGENTS_TEST_POSTGRES_APP_URL")
WORKER_URL = os.environ.get("TRADINGAGENTS_TEST_POSTGRES_WORKER_URL")
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


def _primary_error(exc: DBAPIError) -> str:
    diagnostic = getattr(exc.orig, "diag", None)
    message = getattr(diagnostic, "message_primary", None)
    assert isinstance(message, str)
    return message


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)


def _mint_short_capability(
    service: PortfolioService,
    principal: IdentityPrincipal,
    *,
    audience: str,
    fields: tuple[str, ...],
    now: datetime,
    ttl_seconds: int,
) -> dict[str, object]:
    issued_at = int(now.timestamp())
    expires_at = issued_at + ttl_seconds
    nonce = uuid4().hex + uuid4().hex
    message = service._capability_message(
        audience,
        str(principal.session_id),
        principal.session_token_hash,
        *fields,
        str(issued_at),
        str(expires_at),
        nonce,
    )
    return {
        "cap_kid": "portfolio-capability-v1",
        "cap_session_id": str(principal.session_id),
        "cap_token_hash": principal.session_token_hash,
        "cap_issued_at": issued_at,
        "cap_expires_at": expires_at,
        "cap_nonce": nonce,
        "cap_mac": hmac.new(_CAPABILITY_KEY, message, hashlib.sha256).hexdigest(),
    }


def _latest_snapshot(
    service: PortfolioService,
    principal: IdentityPrincipal,
    portfolio_id,
) -> PortfolioSnapshot:
    with service._sessions() as db, db.begin():
        service._set_portfolio_scope(db, principal)
        snapshot = db.scalar(
            select(PortfolioSnapshot)
            .where(
                PortfolioSnapshot.tenant_id == principal.tenant_id,
                PortfolioSnapshot.portfolio_id == portfolio_id,
            )
            .order_by(PortfolioSnapshot.sequence.desc())
            .limit(1)
        )
        assert snapshot is not None
        db.expunge(snapshot)
        return snapshot


def _call_reserve_port(
    service: PortfolioService,
    principal: IdentityPrincipal,
    *,
    portfolio_id,
    amount,
    purpose_digest: str,
    key_hash: str,
    ttl_microseconds: int,
    snapshot: PortfolioSnapshot,
):
    with service._sessions() as db, db.begin():
        service._set_portfolio_scope(db, principal)
        now = service._database_now(db)
        amount_field = str(amount)
        capability = service._mint_capability(
            principal=principal,
            audience="portfolio.reserve.v1",
            fields=(
                str(portfolio_id),
                amount_field,
                purpose_digest,
                key_hash,
                str(ttl_microseconds),
                str(snapshot.id),
                snapshot.digest,
            ),
            now=now,
        )
        return db.execute(
            text(
                "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
                ":portfolio_id,CAST(:amount AS numeric),:purpose,:key_hash,"
                ":ttl_microseconds,:snapshot_id,:snapshot_digest,:cap_kid,"
                ":cap_session_id,:cap_token_hash,:cap_issued_at,:cap_expires_at,"
                ":cap_nonce,:cap_mac)"
            ),
            {
                "portfolio_id": str(portfolio_id),
                "amount": amount,
                "purpose": purpose_digest,
                "key_hash": key_hash,
                "ttl_microseconds": ttl_microseconds,
                "snapshot_id": str(snapshot.id),
                "snapshot_digest": snapshot.digest,
            }
            | capability,
        ).one()


@pytest.mark.skipif(
    not APP_URL or not WORKER_URL,
    reason="PostgreSQL app and worker test URLs are not configured",
)
def test_worker_executes_bounded_nonce_maintenance_with_its_real_role() -> None:
    worker_engine = create_engine(WORKER_URL, pool_pre_ping=True)
    worker_factory = sessionmaker(worker_engine, expire_on_commit=False)
    maintenance = PortfolioMaintenance(worker_factory, nonce_batch_size=17)

    with worker_engine.begin() as connection:
        assert connection.scalar(text("SELECT current_user")) == "tradingagents_worker"
        assert connection.scalar(
            text(
                "SELECT has_schema_privilege(current_user, "
                "'tradingagents_private', 'USAGE')"
            )
        )

    removed = maintenance.run_once()

    assert isinstance(removed, int)
    assert 0 <= removed <= 17
    worker_engine.dispose()


@pytest.mark.skipif(not APP_URL, reason="PostgreSQL app test URL is not configured")
def test_reserve_capability_is_rechecked_after_waiting_for_portfolio_lock() -> None:
    engine = create_engine(APP_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(factory, token_hasher=TokenHasher(_TOKEN_PEPPER))
    principal = _principal(identity, "LockExpiry")
    service = PortfolioService(
        factory,
        idempotency_digest_key=b"a" * 32,
        capability_signing_key=_CAPABILITY_KEY,
    )
    _suitability, portfolio = _ready(service, principal, "LockExpiry")
    snapshot = _latest_snapshot(service, principal, portfolio.id)
    purpose = "6" * 64
    key_hash = service._key_hash("pg-lock-expiry-reservation")
    fields = (
        str(portfolio.id),
        "1",
        purpose,
        key_hash,
        "30000000",
        str(snapshot.id),
        snapshot.digest,
    )

    holder = factory()
    holder.begin()
    service._set_portfolio_scope(holder, principal)
    holder.scalar(
        select(Portfolio)
        .where(Portfolio.id == portfolio.id, Portfolio.tenant_id == principal.tenant_id)
        .with_for_update()
    )
    with factory() as clock_db, clock_db.begin():
        service._set_portfolio_scope(clock_db, principal)
        capability_now = service._database_now(clock_db)
    capability = _mint_short_capability(
        service,
        principal,
        audience="portfolio.reserve.v1",
        fields=fields,
        now=capability_now,
        ttl_seconds=2,
    )
    sql = text(
        "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
        ":portfolio_id,:amount,:purpose,:key_hash,:ttl_microseconds,:snapshot_id,"
        ":snapshot_digest,:cap_kid,:cap_session_id,:cap_token_hash,:cap_issued_at,"
        ":cap_expires_at,:cap_nonce,:cap_mac)"
    )
    params = {
        "portfolio_id": str(portfolio.id),
        "amount": Decimal("1"),
        "purpose": purpose,
        "key_hash": key_hash,
        "ttl_microseconds": 30_000_000,
        "snapshot_id": str(snapshot.id),
        "snapshot_digest": snapshot.digest,
    } | capability
    entered = threading.Event()

    def invoke_blocked_port() -> str:
        try:
            with factory() as db, db.begin():
                service._set_portfolio_scope(db, principal)
                entered.set()
                db.execute(sql, params).one()
            return "unexpected-success"
        except DBAPIError as exc:
            return _primary_error(exc)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(invoke_blocked_port)
            assert entered.wait(timeout=2)
            time.sleep(0.5)
            assert not future.done(), "reserve port did not block on the portfolio row"
            time.sleep(2.5)
            holder.commit()
            assert future.result(timeout=5) == "PORTFOLIO_CAPABILITY_INVALID"
    finally:
        if holder.in_transaction():
            holder.rollback()
        holder.close()
        engine.dispose()


@pytest.mark.skipif(not APP_URL, reason="PostgreSQL app test URL is not configured")
@pytest.mark.parametrize(
    ("initial_ttl", "replay_ttl"),
    [
        (timedelta(seconds=30), timedelta(hours=1)),
        (timedelta(hours=1), timedelta(seconds=30)),
    ],
)
def test_postgres_reservation_replay_binds_ttl(initial_ttl, replay_ttl) -> None:
    engine = create_engine(APP_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(factory, token_hasher=TokenHasher(_TOKEN_PEPPER))
    principal = _principal(identity, f"Ttl{int(initial_ttl.total_seconds())}")
    service = PortfolioService(
        factory,
        idempotency_digest_key=b"a" * 32,
        capability_signing_key=_CAPABILITY_KEY,
    )
    _suitability, portfolio = _ready(service, principal, "TtlReplay")
    key = f"pg-ttl-replay-{int(initial_ttl.total_seconds())}"
    first = service.reserve_cash(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest="7" * 64,
        idempotency_key=key,
        ttl=initial_ttl,
    )

    with pytest.raises(PortfolioError, match="IDEMPOTENCY_PAYLOAD_MISMATCH"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("1"),
            purpose_digest="7" * 64,
            idempotency_key=key,
            ttl=replay_ttl,
        )
    assert first.expires_at - first.created_at == initial_ttl
    engine.dispose()


@pytest.mark.skipif(not APP_URL, reason="PostgreSQL app test URL is not configured")
def test_postgres_rpc_ttl_mismatch_precedes_finalized_status() -> None:
    engine = create_engine(APP_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(factory, token_hasher=TokenHasher(_TOKEN_PEPPER))
    principal = _principal(identity, "TtlFinalized")
    service = PortfolioService(
        factory,
        idempotency_digest_key=b"a" * 32,
        capability_signing_key=_CAPABILITY_KEY,
    )
    _suitability, portfolio = _ready(service, principal, "TtlFinalized")
    purpose = "9" * 64
    key = "pg-ttl-finalized-replay"
    reservation = service.reserve_cash(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest=purpose,
        idempotency_key=key,
        ttl=timedelta(seconds=30),
    )
    assert (
        service.release_reservation(principal=principal, reservation_id=reservation.id)
        == "released"
    )
    snapshot = _latest_snapshot(service, principal, portfolio.id)

    with pytest.raises(DBAPIError) as captured:
        _call_reserve_port(
            service,
            principal,
            portfolio_id=portfolio.id,
            amount=Decimal("1"),
            purpose_digest=purpose,
            key_hash=service._key_hash(key),
            ttl_microseconds=3_600_000_000,
            snapshot=snapshot,
        )
    assert _primary_error(captured.value) == "IDEMPOTENCY_PAYLOAD_MISMATCH"
    engine.dispose()


@pytest.mark.skipif(not APP_URL, reason="PostgreSQL app test URL is not configured")
def test_postgres_rpc_serializes_ttl_precheck_before_concurrent_finalization() -> None:
    engine = create_engine(APP_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(factory, token_hasher=TokenHasher(_TOKEN_PEPPER))
    principal = _principal(identity, "TtlRace")
    service = PortfolioService(
        factory,
        idempotency_digest_key=b"a" * 32,
        capability_signing_key=_CAPABILITY_KEY,
    )
    _suitability, portfolio = _ready(service, principal, "TtlRace")
    snapshot = _latest_snapshot(service, principal, portfolio.id)
    purpose = "4" * 64
    key_hash = service._key_hash("pg-ttl-race-replay")
    reserve_sql = text(
        "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
        ":portfolio_id,:amount,:purpose,:key_hash,:ttl_microseconds,:snapshot_id,"
        ":snapshot_digest,:cap_kid,:cap_session_id,:cap_token_hash,:cap_issued_at,"
        ":cap_expires_at,:cap_nonce,:cap_mac)"
    )
    with factory() as clock_db, clock_db.begin():
        service._set_portfolio_scope(clock_db, principal)
        capability_now = service._database_now(clock_db)

    def reserve_params(ttl_microseconds: int) -> dict[str, object]:
        capability = service._mint_capability(
            principal=principal,
            audience="portfolio.reserve.v1",
            fields=(
                str(portfolio.id),
                "1",
                purpose,
                key_hash,
                str(ttl_microseconds),
                str(snapshot.id),
                snapshot.digest,
            ),
            now=capability_now,
        )
        return {
            "portfolio_id": str(portfolio.id),
            "amount": Decimal("1"),
            "purpose": purpose,
            "key_hash": key_hash,
            "ttl_microseconds": ttl_microseconds,
            "snapshot_id": str(snapshot.id),
            "snapshot_digest": snapshot.digest,
        } | capability

    waiting_params = reserve_params(3_600_000_000)
    winning_params = reserve_params(30_000_000)
    holder = factory()
    holder.begin()
    service._set_portfolio_scope(holder, principal)
    holder.scalar(
        select(Portfolio)
        .where(Portfolio.id == portfolio.id, Portfolio.tenant_id == principal.tenant_id)
        .with_for_update()
    )
    entered = threading.Event()

    def invoke_waiting_replay() -> str:
        try:
            with factory() as db, db.begin():
                service._set_portfolio_scope(db, principal)
                entered.set()
                db.execute(reserve_sql, waiting_params).one()
            return "unexpected-success"
        except DBAPIError as exc:
            return _primary_error(exc)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(invoke_waiting_replay)
            assert entered.wait(timeout=2)
            time.sleep(0.5)
            assert not future.done(), "conflicting replay did not wait for portfolio lock"
            created = holder.execute(reserve_sql, winning_params).one()
            release_now = service._database_now(holder)
            release_capability = service._mint_capability(
                principal=principal,
                audience="portfolio.release.v1",
                fields=(str(created.id),),
                now=release_now,
            )
            released = holder.scalar(
                text(
                    "SELECT tradingagents_private.portfolio_release_cash_v1("
                    ":reservation_id,:cap_kid,:cap_session_id,:cap_token_hash,"
                    ":cap_issued_at,:cap_expires_at,:cap_nonce,:cap_mac)"
                ),
                {"reservation_id": str(created.id)} | release_capability,
            )
            assert released == "released"
            holder.commit()
            assert future.result(timeout=5) == "IDEMPOTENCY_PAYLOAD_MISMATCH"
        with factory() as db, db.begin():
            service._set_portfolio_scope(db, principal)
            assert db.get(CashReservation, created.id).status == "released"
    finally:
        if holder.in_transaction():
            holder.rollback()
        holder.close()
        engine.dispose()


@pytest.mark.skipif(not APP_URL, reason="PostgreSQL app test URL is not configured")
def test_release_capability_is_rechecked_after_waiting_for_portfolio_lock() -> None:
    engine = create_engine(APP_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(factory, token_hasher=TokenHasher(_TOKEN_PEPPER))
    principal = _principal(identity, "ReleaseLockExpiry")
    service = PortfolioService(
        factory,
        idempotency_digest_key=b"a" * 32,
        capability_signing_key=_CAPABILITY_KEY,
    )
    _suitability, portfolio = _ready(service, principal, "ReleaseLockExpiry")
    reservation = service.reserve_cash(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest="8" * 64,
        idempotency_key="pg-release-lock-expiry",
    )

    holder = factory()
    holder.begin()
    service._set_portfolio_scope(holder, principal)
    holder.scalar(
        select(Portfolio)
        .where(Portfolio.id == portfolio.id, Portfolio.tenant_id == principal.tenant_id)
        .with_for_update()
    )
    with factory() as clock_db, clock_db.begin():
        service._set_portfolio_scope(clock_db, principal)
        capability_now = service._database_now(clock_db)
    capability = _mint_short_capability(
        service,
        principal,
        audience="portfolio.release.v1",
        fields=(str(reservation.id),),
        now=capability_now,
        ttl_seconds=2,
    )
    sql = text(
        "SELECT tradingagents_private.portfolio_release_cash_v1("
        ":reservation_id,:cap_kid,:cap_session_id,:cap_token_hash,:cap_issued_at,"
        ":cap_expires_at,:cap_nonce,:cap_mac)"
    )
    params = {"reservation_id": str(reservation.id)} | capability
    entered = threading.Event()

    def invoke_blocked_port() -> str:
        try:
            with factory() as db, db.begin():
                service._set_portfolio_scope(db, principal)
                entered.set()
                return str(db.scalar(sql, params))
        except DBAPIError as exc:
            return _primary_error(exc)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(invoke_blocked_port)
            assert entered.wait(timeout=2)
            time.sleep(0.5)
            assert not future.done(), "release port did not block on the portfolio row"
            time.sleep(2.5)
            holder.commit()
            assert future.result(timeout=5) == "PORTFOLIO_CAPABILITY_INVALID"
        with factory() as db, db.begin():
            service._set_portfolio_scope(db, principal)
            assert db.get(CashReservation, reservation.id).status == "active"
    finally:
        if holder.in_transaction():
            holder.rollback()
        holder.close()
        engine.dispose()


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
    assert sorted(outcomes) == ["ACTIVE_RESERVATION_EXISTS", "reserved"]
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        race_reservation_id = db.scalar(
            select(CashReservation.id).where(
                CashReservation.portfolio_id == portfolio.id,
                CashReservation.status == "active",
            )
        )
    assert race_reservation_id is not None
    assert first_service.release_reservation(
        principal=first, reservation_id=race_reservation_id
    ) == "released"
    expire_guard = first_service.reserve_cash(
        principal=first,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest="3" * 64,
        idempotency_key="pg-expire-guard-0001",
    )
    assert first_service.release_reservation(
        principal=first, reservation_id=expire_guard.id
    ) == "released"
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
    assert first_service.release_reservation(
        principal=first, reservation_id=fractional_ttl.id
    ) == "released"
    stale_replay_guard = first_service.reserve_cash(
        principal=first,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest="2" * 64,
        idempotency_key="pg-stale-replay-guard-0001",
    )

    # Operation capabilities bind every capital parameter and are one-use.
    cap_key_hash = first_service._key_hash("pg-stale-replay-guard-0001")
    cap_now = datetime.now(UTC)
    baseline_snapshot = _latest_snapshot(first_service, first, portfolio.id)
    replay_ttl_microseconds = first_service._ttl_microseconds(
        stale_replay_guard.expires_at - stale_replay_guard.created_at
    )
    reserve_capability = first_service._mint_capability(
        principal=first,
        audience="portfolio.reserve.v1",
        fields=(
            str(portfolio.id),
            "1",
            "2" * 64,
            cap_key_hash,
            str(replay_ttl_microseconds),
            str(baseline_snapshot.id),
            baseline_snapshot.digest,
        ),
        now=cap_now,
    )
    capability_sql = text(
        "SELECT * FROM tradingagents_private.portfolio_reserve_cash_v1("
        ":portfolio_id,:amount,:purpose,:key_hash,:ttl_microseconds,:snapshot_id,"
        ":snapshot_digest,:cap_kid,:cap_session_id,:cap_token_hash,:cap_issued_at,:cap_expires_at,"
        ":cap_nonce,:cap_mac)"
    )
    capability_params = {
        "portfolio_id": str(portfolio.id),
        "amount": Decimal("1"),
        "purpose": "2" * 64,
        "key_hash": cap_key_hash,
        "ttl_microseconds": replay_ttl_microseconds,
        "snapshot_id": str(baseline_snapshot.id),
        "snapshot_digest": baseline_snapshot.digest,
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
    _guard_suitability, guard_portfolio = _ready(first_service, first, "Guards")
    guard_snapshot = _latest_snapshot(first_service, first, guard_portfolio.id)
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
        for table in (
            "portfolio_positions",
            "portfolio_snapshots",
            "portfolio_circuit_breakers",
        ):
            for privilege in (
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert not connection.scalar(
                    text(
                        "SELECT has_table_privilege(current_user, "
                        ":table, :privilege)"
                    ),
                    {"table": f"public.{table}", "privilege": privilege},
                )
        assert connection.scalar(
            text(
                "SELECT has_function_privilege(current_user, "
                "'tradingagents_private.portfolio_reserve_cash_v1"
                "(uuid,numeric,text,text,bigint,uuid,text,text,uuid,text,bigint,bigint,text,text)', "
                "'EXECUTE')"
            )
        )
        reserve_impl = (
            "tradingagents_private.portfolio_reserve_cash_v1_0022_impl"
            "(uuid,numeric,text,text,bigint,uuid,text,text,uuid,text,bigint,bigint,text,text)"
        )
        assert not connection.scalar(
            text(
                "SELECT has_function_privilege(current_user, :function, 'EXECUTE')"
            ),
            {"function": reserve_impl},
        )
        assert not connection.scalar(
            text(
                "SELECT has_function_privilege('tradingagents_worker', :function, "
                "'EXECUTE')"
            ),
            {"function": reserve_impl},
        )
        assert connection.scalar(
            text(
                "SELECT has_function_privilege(current_user, "
                "'tradingagents_private.portfolio_capture_snapshot_v1"
                "(uuid,text,bigint,bigint,text,text,text,uuid,text,bigint,bigint,text,text)', "
                "'EXECUTE')"
            )
        )
        assert not connection.scalar(
            text(
                "SELECT has_function_privilege(current_user, "
                "'tradingagents_private.portfolio_prune_capability_nonces_v1(integer)', "
                "'EXECUTE')"
            )
        )
        assert connection.scalar(
            text(
                "SELECT has_function_privilege('tradingagents_worker', "
                "'tradingagents_private.portfolio_prune_capability_nonces_v1(integer)', "
                "'EXECUTE')"
            )
        )
        assert connection.scalar(
            text(
                "SELECT to_regprocedure('tradingagents_private.portfolio_reserve_cash_v1"
                "(uuid,numeric,text,text,bigint,text,uuid,text,bigint,bigint,text,text)') "
                "IS NULL"
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
                "SELECT count(*) = 3 AND bool_and(p.prosecdef) AND "
                "bool_and(pg_get_userbyid(p.proowner) = "
                "'tradingagents_reservation_api') AND "
                "bool_and(array_to_string(p.proconfig, ',') LIKE "
                "'%search_path=pg_catalog%') AND "
                "bool_and(array_to_string(p.proconfig, ',') LIKE '%row_security=on%') "
                "FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid = p.pronamespace WHERE n.nspname = 'tradingagents_private' "
                "AND p.proname IN "
                "('portfolio_reserve_cash_v1','portfolio_release_cash_v1',"
                "'portfolio_capture_snapshot_v1')"
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

    # ACLs are enforced dynamically too: even a valid authenticated session
    # cannot manufacture or mutate derived portfolio state outside the ports.
    for statement in (
        "INSERT INTO portfolio_positions DEFAULT VALUES",
        "UPDATE portfolio_positions SET version=version+1 WHERE false",
        "DELETE FROM portfolio_positions WHERE false",
        "TRUNCATE portfolio_positions",
        "INSERT INTO portfolio_snapshots DEFAULT VALUES",
        "UPDATE portfolio_snapshots SET sequence=sequence+1 WHERE false",
        "DELETE FROM portfolio_snapshots WHERE false",
        "TRUNCATE portfolio_snapshots",
        "INSERT INTO portfolio_circuit_breakers DEFAULT VALUES",
        "UPDATE portfolio_circuit_breakers SET status='reset' WHERE false",
        "DELETE FROM portfolio_circuit_breakers WHERE false",
        "TRUNCATE portfolio_circuit_breakers",
    ):
        with pytest.raises(DBAPIError) as captured, factory() as db, db.begin():
            first_service._set_portfolio_scope(db, first)
            db.execute(text(statement))
        assert _sqlstate(captured.value) == "42501"

    # A new mandate row cannot carry user-chosen limits while retaining a
    # canonical profile name/digest. The trigger is the database source of
    # truth, not the Python policy object.
    with pytest.raises(DBAPIError) as captured, factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        db.execute(
            text(
                "INSERT INTO portfolio_mandates "
                "(id,tenant_id,portfolio_id,suitability_id,version,profile,mode,status,"
                "pause_reason,policy_version,policy_digest,digest,limits,"
                "created_by_user_id,effective_at,ended_at,created_at) "
                "SELECT gen_random_uuid(),tenant_id,portfolio_id,suitability_id,"
                "version+100,profile,mode,'active',NULL,policy_version,policy_digest,"
                "digest,jsonb_set(limits::jsonb,'{max_position_weight}','\"0.99\"'),"
                "created_by_user_id,clock_timestamp(),NULL,clock_timestamp() "
                "FROM portfolio_mandates WHERE id=:mandate"
            ),
            {"mandate": str(stale_replay_guard.mandate_id)},
        )
    assert _primary_error(captured.value) == "POLICY_VERSION_MISMATCH"

    # Snapshot capabilities bind the exact canonical price payload. Tampering
    # fails before valuation logic, the untampered capability succeeds once,
    # and replay is rejected in a later transaction.
    snapshot_prices = "{}"
    snapshot_evidence = "3" * 64
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        snapshot_now = first_service._database_now(db)
    snapshot_observed = first_service._epoch_microseconds(snapshot_now)
    snapshot_params = {
        "portfolio_id": str(guard_portfolio.id),
        "valuation_source": "market_data_pit",
        "observed_microseconds": snapshot_observed,
        "available_microseconds": snapshot_observed,
        "evidence_digest": snapshot_evidence,
        "prices_text": snapshot_prices,
    }
    snapshot_capability = first_service._mint_capability(
        principal=first,
        audience="portfolio.snapshot.v1",
        fields=(
            str(guard_portfolio.id),
            "market_data_pit",
            str(snapshot_observed),
            str(snapshot_observed),
            snapshot_evidence,
            hashlib.sha256(snapshot_prices.encode()).hexdigest(),
        ),
        now=snapshot_now,
    )
    snapshot_sql = text(
        "SELECT * FROM tradingagents_private.portfolio_capture_snapshot_v1("
        ":portfolio_id,:valuation_source,:observed_microseconds,"
        ":available_microseconds,:evidence_digest,:prices_text,:cap_kid,"
        ":cap_session_id,:cap_token_hash,:cap_issued_at,:cap_expires_at,"
        ":cap_nonce,:cap_mac)"
    )
    with pytest.raises(DBAPIError) as captured, factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        db.execute(
            snapshot_sql,
            snapshot_params | snapshot_capability | {"prices_text": '{"BTC":"1"}'},
        )
    assert _primary_error(captured.value) == "PORTFOLIO_CAPABILITY_INVALID"
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        db.execute(snapshot_sql, snapshot_params | snapshot_capability).one()
    with pytest.raises(DBAPIError) as captured, factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        db.execute(snapshot_sql, snapshot_params | snapshot_capability)
    assert _primary_error(captured.value) == "PORTFOLIO_CAPABILITY_REPLAY"
    guard_snapshot = _latest_snapshot(first_service, first, guard_portfolio.id)

    huge_prices = '{"BTC":"' + ("9" * 200_000) + '"}'
    huge_digest = hashlib.sha256(huge_prices.encode()).hexdigest()
    huge_capability = first_service._mint_capability(
        principal=first,
        audience="portfolio.snapshot.v1",
        fields=(
            str(guard_portfolio.id),
            "market_data_pit",
            str(snapshot_observed),
            str(snapshot_observed),
            snapshot_evidence,
            huge_digest,
        ),
        now=snapshot_now,
    )
    with pytest.raises(DBAPIError) as captured, factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        db.execute(
            snapshot_sql,
            snapshot_params
            | huge_capability
            | {"prices_text": huge_prices},
        )
    assert _primary_error(captured.value) == "VALUATION_PRICES_INVALID"

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
        with pytest.raises(DBAPIError) as captured:
            _call_reserve_port(
                first_service,
                first,
                portfolio_id=guard_portfolio.id,
                amount=invalid_amount,
                purpose_digest="9" * 64,
                key_hash=invalid_amount.encode().hex().ljust(64, "0")[:64],
                ttl_microseconds=30_000_000,
                snapshot=guard_snapshot,
            )
        assert _primary_error(captured.value) == "RESERVATION_AMOUNT_INVALID"
    with pytest.raises(DBAPIError) as captured:
        _call_reserve_port(
            first_service,
            first,
            portfolio_id=guard_portfolio.id,
            amount=Decimal("1"),
            purpose_digest="8" * 64,
            key_hash="7" * 64,
            ttl_microseconds=29_999_999,
            snapshot=guard_snapshot,
        )
    assert _primary_error(captured.value) == "RESERVATION_TTL_INVALID"
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
            {"id": str(stale_replay_guard.id)},
        )

    # The narrow RPC independently rejects an oversized reserve and a tenant
    # scope that does not match the authenticated session capability.
    with pytest.raises(DBAPIError) as captured:
        _call_reserve_port(
            first_service,
            first,
            portfolio_id=guard_portfolio.id,
            amount=Decimal("999999999"),
            purpose_digest="0" * 64,
            key_hash="0" * 64,
            ttl_microseconds=3_600_000_000,
            snapshot=guard_snapshot,
        )
    assert _primary_error(captured.value) == "INSUFFICIENT_AVAILABLE_CASH"
    with pytest.raises(DBAPIError) as captured:
        _call_reserve_port(
            first_service,
            first,
            portfolio_id=other_portfolio.id,
            amount=Decimal("1"),
            purpose_digest="b" * 64,
            key_hash="a" * 64,
            ttl_microseconds=60_000_000,
            snapshot=guard_snapshot,
        )
    assert _primary_error(captured.value) == "PORTFOLIO_NOT_ACTIVE"

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
            {"id": str(stale_replay_guard.id)},
        )
    with pytest.raises(DBAPIError) as captured:
        _call_reserve_port(
            first_service,
            first,
            portfolio_id=portfolio.id,
            amount=Decimal("1"),
            purpose_digest="6" * 64,
            key_hash="5" * 64,
            ttl_microseconds=60_000_000,
            snapshot=baseline_snapshot,
        )
    assert _primary_error(captured.value) == "ACTIVE_MANDATE_REQUIRED"
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        version_before_release = db.scalar(
            select(Portfolio.balance_version).where(Portfolio.id == portfolio.id)
        )
    assert (
        first_service.release_reservation(
            principal=first,
            reservation_id=stale_replay_guard.id,
        )
        == "released"
    )
    assert (
        first_service.release_reservation(
            principal=first,
            reservation_id=stale_replay_guard.id,
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

    # A capability-bound snapshot is not reusable after the ledger changes.
    # The old row remains immutable, but its source-state digest no longer
    # matches the database state from which a reservation would be made.
    stale_source_snapshot = _latest_snapshot(
        first_service, first, guard_portfolio.id
    )
    first_service._record_cash_transaction(
        principal=first,
        portfolio_id=guard_portfolio.id,
        amount=Decimal("1"),
        idempotency_key="pg-source-state-fee-0001",
        event_type="fee",
        reference_type="test",
        reference_id="invalidate-snapshot-source",
        cash_direction=-1,
    )
    with pytest.raises(DBAPIError) as captured:
        _call_reserve_port(
            first_service,
            first,
            portfolio_id=guard_portfolio.id,
            amount=Decimal("1"),
            purpose_digest="4" * 64,
            key_hash="3" * 64,
            ttl_microseconds=60_000_000,
            snapshot=stale_source_snapshot,
        )
    assert _primary_error(captured.value) == "ALLOCATION_BASELINE_STALE"

    # A direct caller of the narrow RPC cannot reserve while the deterministic
    # circuit breaker is open, even with an otherwise valid session scope.
    first_service._record_cash_transaction(
        principal=first,
        portfolio_id=portfolio.id,
        amount=Decimal("30"),
        idempotency_key="pg-drawdown-fee-0001",
        event_type="fee",
        reference_type="test",
        reference_id="open-breaker",
        cash_direction=-1,
    )
    with first_service._sessions() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        breaker_observed = first_service._database_now(db)
    first_service.capture_trusted_snapshot(
        principal=first,
        portfolio_id=portfolio.id,
        valuation=TrustedValuation(
            prices={},
            source="market_data_pit",
            observed_at=breaker_observed,
            available_at=breaker_observed,
            evidence_digest="c" * 64,
        ),
    )
    with factory() as db, db.begin():
        first_service._set_portfolio_scope(db, first)
        breaker_id = db.scalar(
            text(
                "SELECT id FROM portfolio_circuit_breakers "
                "WHERE portfolio_id=:portfolio AND status='open'"
            ),
            {"portfolio": str(portfolio.id)},
        )
    assert breaker_id is not None
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
    breaker_snapshot = _latest_snapshot(first_service, first, portfolio.id)
    with pytest.raises(DBAPIError) as captured:
        _call_reserve_port(
            first_service,
            first,
            portfolio_id=portfolio.id,
            amount=Decimal("1"),
            purpose_digest="d" * 64,
            key_hash="e" * 64,
            ttl_microseconds=60_000_000,
            snapshot=breaker_snapshot,
        )
    assert _primary_error(captured.value) == "CIRCUIT_BREAKER_OPEN"
    engine.dispose()
