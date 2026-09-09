from __future__ import annotations

import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.contracts import RiskProfile, RunMode
from tradingagents.platform.identity import (
    IdentityPrincipal,
    IdentityService,
    MembershipRole,
    TokenHasher,
)
from tradingagents.platform.identity.models import Base
from tradingagents.platform.portfolio import (
    CashReservation,
    CircuitBreaker,
    Portfolio,
    PortfolioError,
    PortfolioMandate,
    PortfolioPosition,
    PortfolioService,
    TrustedAllocationRequest,
    TrustedAssetAllocation,
    TrustedValuation,
    cap_target_weights,
    policy_for,
)


@pytest.fixture
def audited_portfolio_runtime():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(factory, token_hasher=TokenHasher(b"p" * 32))
    registration = identity.register_tenant(
        tenant_name="Audit Portfolio",
        tenant_slug=f"audit-portfolio-{uuid4().hex[:12]}",
        owner_email=f"audit-{uuid4().hex[:12]}@example.test",
        display_name="Audit Owner",
        password="correct horse battery staple",
    )
    principal = IdentityPrincipal(
        tenant_id=registration.tenant_id,
        user_id=registration.user_id,
        membership_id=registration.membership_id,
        session_id=uuid4(),
        role=MembershipRole.OWNER,
    )
    yield PortfolioService(factory, idempotency_digest_key=b"i" * 32), principal, factory
    engine.dispose()


def _answers(*, conservative: bool = False) -> dict[str, object]:
    return {
        "horizon_years": 1 if conservative else 10,
        "maximum_loss_percent": 5 if conservative else 40,
        "emergency_fund_months": 2 if conservative else 12,
        "income_stability": "unstable" if conservative else "stable",
        "experience": "none" if conservative else "experienced",
    }


def _active(runtime, profile: RiskProfile = RiskProfile.PRESERVATION):
    service, principal, _factory = runtime
    suitability = service.assess_suitability(
        principal=principal,
        requested_profile=profile,
        answers=_answers(conservative=profile is RiskProfile.PRESERVATION),
    )
    portfolio = service.create_portfolio(
        principal=principal, name=f"Portfolio {uuid4().hex[:8]}", base_currency="BRL"
    )
    mandate = service.activate_mandate(
        principal=principal,
        portfolio_id=portfolio.id,
        suitability_id=suitability.id,
        profile=profile,
        mode=RunMode.PAPER,
    )
    return portfolio, mandate


def _valuation(service, *, available_offset: timedelta = timedelta()) -> TrustedValuation:
    observed = service._now()
    return TrustedValuation(
        prices={},
        source="market_data_pit",
        observed_at=observed,
        available_at=observed + available_offset,
        evidence_digest="b" * 64,
    )


def _single_asset_proposal(service: PortfolioService) -> TrustedAllocationRequest:
    observed = service._now()
    return TrustedAllocationRequest(
        raw_target_weights={"BTC/BRL": Decimal("0.05")},
        correlations={},
        assets={
            "BTC/BRL": TrustedAssetAllocation(
                symbol="BTC/BRL",
                asset_class="crypto",
                price=Decimal("500000"),
                conviction=Decimal("1"),
                realized_annual_volatility=Decimal("0.10"),
                average_daily_notional=Decimal("1000000"),
            )
        },
        source="analysis_pit",
        observed_at=observed,
        available_at=observed,
        evidence_digest="9" * 64,
    )


def _fund_snapshot_and_reserve(
    runtime,
    *,
    label: str,
    amount: Decimal = Decimal("10"),
    ttl: timedelta = timedelta(minutes=10),
) -> tuple[Portfolio, CashReservation]:
    service, principal, _factory = runtime
    portfolio, _mandate = _active(runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key=f"{label}-funding-0001",
        reference_id=f"{label}-funding",
    )
    service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    reservation = service.reserve_cash(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=amount,
        purpose_digest="a" * 64,
        idempotency_key=f"{label}-reservation-0001",
        ttl=ttl,
    )
    return portfolio, reservation


def test_reservation_replay_is_valid_only_while_active_and_current(
    audited_portfolio_runtime,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, reservation = _fund_snapshot_and_reserve(
        audited_portfolio_runtime,
        label="active-replay",
        amount=Decimal("800"),
    )

    replay = service.reserve_cash(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("800"),
        purpose_digest="a" * 64,
        idempotency_key="active-replay-reservation-0001",
    )

    assert replay.id == reservation.id
    assert replay.status == "active"


@pytest.mark.parametrize(
    ("initial_ttl", "replay_ttl"),
    [
        (timedelta(seconds=30), timedelta(hours=1)),
        (timedelta(hours=1), timedelta(seconds=30)),
    ],
)
def test_reservation_replay_binds_requested_ttl(
    audited_portfolio_runtime,
    initial_ttl,
    replay_ttl,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, reservation = _fund_snapshot_and_reserve(
        audited_portfolio_runtime,
        label=f"ttl-replay-{int(initial_ttl.total_seconds())}",
        ttl=initial_ttl,
    )

    with pytest.raises(PortfolioError, match="IDEMPOTENCY_PAYLOAD_MISMATCH"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=reservation.amount,
            purpose_digest=reservation.purpose_digest,
            idempotency_key=(
                f"ttl-replay-{int(initial_ttl.total_seconds())}-reservation-0001"
            ),
            ttl=replay_ttl,
        )


def test_expired_reservation_replay_fails_closed(
    audited_portfolio_runtime, monkeypatch
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, reservation = _fund_snapshot_and_reserve(
        audited_portfolio_runtime,
        label="expired-replay",
        ttl=timedelta(seconds=30),
    )
    monkeypatch.setattr(
        service, "_now", lambda: reservation.expires_at + timedelta(seconds=1)
    )

    with pytest.raises(PortfolioError, match="IDEMPOTENCY_RESERVATION_FINALIZED"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=reservation.amount,
            purpose_digest=reservation.purpose_digest,
            idempotency_key="expired-replay-reservation-0001",
            ttl=timedelta(seconds=30),
        )


def test_released_reservation_replay_fails_closed(audited_portfolio_runtime) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, reservation = _fund_snapshot_and_reserve(
        audited_portfolio_runtime,
        label="released-replay",
    )
    with factory() as db:
        version_before_release = db.get(Portfolio, portfolio.id).balance_version
    assert (
        service.release_reservation(principal=principal, reservation_id=reservation.id)
        == "released"
    )
    with factory() as db:
        assert db.get(Portfolio, portfolio.id).balance_version == version_before_release + 1
    assert (
        service.release_reservation(principal=principal, reservation_id=reservation.id)
        == "released"
    )
    with factory() as db:
        assert db.get(Portfolio, portfolio.id).balance_version == version_before_release + 1

    with pytest.raises(PortfolioError, match="IDEMPOTENCY_RESERVATION_FINALIZED"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=reservation.amount,
            purpose_digest=reservation.purpose_digest,
            idempotency_key="released-replay-reservation-0001",
        )


def test_stale_mandate_reservation_replay_fails_closed(
    audited_portfolio_runtime,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, reservation = _fund_snapshot_and_reserve(
        audited_portfolio_runtime,
        label="stale-mandate-replay",
    )
    latest = service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.PRESERVATION,
        answers=_answers(conservative=True),
    )
    replacement = service.activate_mandate(
        principal=principal,
        portfolio_id=portfolio.id,
        suitability_id=latest.id,
        profile=RiskProfile.PRESERVATION,
        mode=RunMode.PAPER,
    )
    assert replacement.id != reservation.mandate_id

    with pytest.raises(PortfolioError, match="IDEMPOTENCY_RESERVATION_FINALIZED"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=reservation.amount,
            purpose_digest=reservation.purpose_digest,
            idempotency_key="stale-mandate-replay-reservation-0001",
        )


def test_reservation_replay_rechecks_circuit_breaker(audited_portfolio_runtime) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, reservation = _fund_snapshot_and_reserve(
        audited_portfolio_runtime,
        label="breaker-replay",
    )
    with factory() as db, db.begin():
        db.add(
            CircuitBreaker(
                tenant_id=principal.tenant_id,
                portfolio_id=portfolio.id,
                mandate_id=reservation.mandate_id,
                kind="maximum_drawdown",
                status="open",
                observed_value=Decimal("0.30"),
                threshold_value=Decimal("0.25"),
                evidence_digest="d" * 64,
                triggered_at=service._now(),
            )
        )

    with pytest.raises(PortfolioError, match="CIRCUIT_BREAKER_OPEN"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=reservation.amount,
            purpose_digest=reservation.purpose_digest,
            idempotency_key="breaker-replay-reservation-0001",
        )


def test_only_reservation_idempotency_constraint_is_treated_as_replay() -> None:
    unrelated = IntegrityError("INSERT", {}, RuntimeError("check constraint failed"))
    assert PortfolioService._is_reservation_idempotency_conflict(unrelated) is False
    postgres_unique = RuntimeError("duplicate key")
    postgres_unique.diag = SimpleNamespace(  # type: ignore[attr-defined]
        constraint_name="uq_reservation_idempotency"
    )
    assert (
        PortfolioService._is_reservation_idempotency_conflict(
            IntegrityError("INSERT", {}, postgres_unique)
        )
        is True
    )
    sqlite_unique = RuntimeError(
        "UNIQUE constraint failed: portfolio_cash_reservations.tenant_id, "
        "portfolio_cash_reservations.portfolio_id, "
        "portfolio_cash_reservations.idempotency_key_hash"
    )
    assert (
        PortfolioService._is_reservation_idempotency_conflict(
            IntegrityError("INSERT", {}, sqlite_unique)
        )
        is True
    )


def test_mandate_rejects_a_stale_more_permissive_suitability(audited_portfolio_runtime) -> None:
    service, principal, _factory = audited_portfolio_runtime
    stale = service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.AGGRESSIVE,
        answers=_answers(),
    )
    portfolio = service.create_portfolio(
        principal=principal, name="Stale suitability", base_currency="BRL"
    )
    service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.PRESERVATION,
        answers=_answers(conservative=True),
    )

    with pytest.raises(PortfolioError, match="SUITABILITY_STALE"):
        service.activate_mandate(
            principal=principal,
            portfolio_id=portfolio.id,
            suitability_id=stale.id,
            profile=RiskProfile.AGGRESSIVE,
            mode=RunMode.PAPER,
        )


def test_new_suitability_pauses_an_active_mandate_and_blocks_unsafe_resume(
    audited_portfolio_runtime,
) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, active = _active(audited_portfolio_runtime, RiskProfile.AGGRESSIVE)

    latest = service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.PRESERVATION,
        answers=_answers(conservative=True),
    )

    with factory() as db:
        persisted = db.scalar(select(Portfolio).where(Portfolio.id == portfolio.id))
        paused = db.get(PortfolioMandate, persisted.current_mandate_id)
    assert paused is not None
    assert paused.id != active.id
    assert paused.status == "paused"
    assert paused.suitability_id == latest.id
    with pytest.raises(PortfolioError, match="PROFILE_EXCEEDS_SUITABILITY"):
        service.transition_mandate(
            principal=principal,
            portfolio_id=portfolio.id,
            target_status="active",
        )


def test_snapshot_requires_an_active_mandate_and_trusted_provenance(
    audited_portfolio_runtime,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio = service.create_portfolio(
        principal=principal, name="No time oracle", base_currency="BRL"
    )

    observed = service._now()
    with pytest.raises(PortfolioError, match="ACTIVE_MANDATE_REQUIRED"):
        service.capture_trusted_snapshot(
            principal=principal,
            portfolio_id=portfolio.id,
            valuation=TrustedValuation(
                prices={},
                source="market_data_pit",
                observed_at=observed,
                available_at=observed,
                evidence_digest="a" * 64,
            ),
        )


def test_crypto_cap_is_aggregate_not_per_position() -> None:
    policy = policy_for(RiskProfile.MODERATE)
    weights = cap_target_weights(
        policy,
        {
            "BTC/USDT": Decimal("0.12"),
            "ETH/USDT": Decimal("0.12"),
            "SOL/USDT": Decimal("0.12"),
            "ADA/USDT": Decimal("0.12"),
        },
        {},
        asset_classes={
            "BTC/USDT": "crypto",
            "ETH/USDT": "crypto",
            "SOL/USDT": "crypto",
            "ADA/USDT": "crypto",
        },
    )
    assert sum(weights.values(), Decimal(0)) <= policy.max_crypto_weight


@pytest.mark.parametrize("threshold", [Decimal("NaN"), Decimal("-1.01"), Decimal("1.01")])
def test_correlation_threshold_cannot_disable_the_bucket_cap(threshold: Decimal) -> None:
    with pytest.raises(ValueError, match="correlation threshold"):
        cap_target_weights(
            policy_for(RiskProfile.MODERATE),
            {"BTC/BRL": Decimal("0.1")},
            {},
            asset_classes={"BTC/BRL": "crypto"},
            correlated_threshold=threshold,
        )


def test_non_tuple_correlation_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="correlation references"):
        cap_target_weights(
            policy_for(RiskProfile.MODERATE),
            {"A": Decimal("0.1"), "B": Decimal("0.1")},
            {"AB": Decimal("0.9")},  # type: ignore[dict-item]
            asset_classes={"A": "stock", "B": "stock"},
        )


def test_database_schema_refuses_live_mandate_even_if_service_is_bypassed(
    audited_portfolio_runtime,
) -> None:
    _service, _principal, factory = audited_portfolio_runtime
    _portfolio, mandate = _active(audited_portfolio_runtime)
    with pytest.raises(IntegrityError), factory() as db, db.begin():
        db.add(
            PortfolioMandate(
                tenant_id=mandate.tenant_id,
                portfolio_id=mandate.portfolio_id,
                suitability_id=mandate.suitability_id,
                version=mandate.version + 1,
                profile=mandate.profile,
                mode="live_automated",
                status="active",
                policy_version=mandate.policy_version,
                policy_digest=mandate.policy_digest,
                digest="7" * 64,
                limits=mandate.limits,
                created_by_user_id=mandate.created_by_user_id,
                effective_at=mandate.effective_at,
            )
        )
        db.flush()


def test_migration_restores_append_only_guard_after_ledger_backfill() -> None:
    migration = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260908_0020_portfolio_audit_hardening.py"
    ).read_text(encoding="utf-8")
    drop_at = migration.index(
        "DROP TRIGGER IF EXISTS portfolio_ledger_transactions_append_only"
    )
    backfill_at = migration.index("UPDATE portfolio_ledger_transactions SET")
    restore_at = migration.index(
        "CREATE TRIGGER portfolio_ledger_transactions_append_only"
    )
    assert drop_at < backfill_at < restore_at


def test_current_mandate_is_bound_by_pointer_not_historical_active_status(
    audited_portfolio_runtime,
) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, active = _active(audited_portfolio_runtime)
    paused = service.transition_mandate(
        principal=principal, portfolio_id=portfolio.id, target_status="paused"
    )
    with factory() as db:
        persisted = db.scalar(select(Portfolio).where(Portfolio.id == portfolio.id))
        historical_active = db.scalar(
            select(func.count())
            .select_from(PortfolioMandate)
            .where(
                PortfolioMandate.portfolio_id == portfolio.id,
                PortfolioMandate.status == "active",
            )
        )
    assert active.id != paused.id
    assert persisted is not None and persisted.current_mandate_id == paused.id
    assert historical_active == 1
    with factory() as db:
        assert service._active_mandate(db, principal.tenant_id, portfolio.id, service._now()) is None


def test_valuation_future_availability_is_rejected(audited_portfolio_runtime) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="audit-funding-future-0001",
        reference_id="paper-balance-future",
    )
    with pytest.raises(PortfolioError, match="VALUATION_TIMELINE_INVALID"):
        service.capture_trusted_snapshot(
            principal=principal,
            portfolio_id=portfolio.id,
            valuation=_valuation(service, available_offset=timedelta(days=1)),
        )


def test_external_capital_flows_do_not_create_false_performance(
    audited_portfolio_runtime,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="audit-funding-nav-0001",
        reference_id="paper-balance-one",
    )
    first = service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("500"),
        idempotency_key="audit-funding-nav-0002",
        reference_id="paper-balance-two",
    )
    second = service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    assert first.nav_per_unit == second.nav_per_unit == Decimal("1.000000000000")
    assert second.drawdown == 0


def test_user_declared_paper_funding_is_locked_after_a_position_exists(
    audited_portfolio_runtime,
) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    original = service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="audit-funding-before-trading-0001",
        reference_id="paper-opening-balance",
    )
    with factory() as db, db.begin():
        db.add(
            PortfolioPosition(
                tenant_id=principal.tenant_id,
                portfolio_id=portfolio.id,
                symbol="BTC/BRL",
                asset_class="crypto",
                quantity=Decimal("0.001"),
                average_cost=Decimal("1000"),
                realized_pnl=Decimal(0),
            )
        )

    replay = service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="audit-funding-before-trading-0001",
        reference_id="paper-opening-balance",
    )
    assert replay.id == original.id

    with pytest.raises(PortfolioError, match="PAPER_CAPITAL_LOCKED_AFTER_TRADING"):
        service.record_funding(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("100"),
            idempotency_key="audit-funding-after-trading-0001",
            reference_id="paper-late-balance",
        )


def test_daily_breaker_uses_day_baseline_not_only_previous_step(
    audited_portfolio_runtime,
) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="audit-funding-daily-0001",
        reference_id="paper-balance-daily",
    )
    service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    for index in range(2):
        service._record_cash_transaction(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("6"),
            idempotency_key=f"audit-internal-fee-{index:04d}",
            event_type="fee",
            reference_type="internal",
            reference_id=f"fee-{index}",
            cash_direction=-1,
        )
        service.capture_trusted_snapshot(
            principal=principal,
            portfolio_id=portfolio.id,
            valuation=_valuation(service),
        )
    with factory() as db:
        breaker = db.scalar(
            select(CircuitBreaker).where(CircuitBreaker.portfolio_id == portfolio.id)
        )
    assert breaker is not None
    assert breaker.kind == "daily_drawdown"
    assert breaker.observed_value >= Decimal("0.01")


def test_expired_suitability_blocks_new_risk_even_without_state_transition(
    audited_portfolio_runtime, monkeypatch
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    suitability = service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.PRESERVATION,
        answers=_answers(conservative=True),
        valid_for=timedelta(days=1),
    )
    portfolio = service.create_portfolio(
        principal=principal, name="Expiry gate", base_currency="BRL"
    )
    service.activate_mandate(
        principal=principal,
        portfolio_id=portfolio.id,
        suitability_id=suitability.id,
        profile=RiskProfile.PRESERVATION,
        mode=RunMode.PAPER,
    )
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="expiry-gate-funding-0001",
        reference_id="expiry-gate-balance",
    )
    after_expiry = suitability.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(service, "_now", lambda: after_expiry)

    with pytest.raises(PortfolioError, match="SUITABILITY_REQUIRED"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("1"),
            purpose_digest="f" * 64,
            idempotency_key="expiry-gate-reservation-0001",
        )
    with pytest.raises(PortfolioError, match="SUITABILITY_REQUIRED"):
        service.capture_trusted_snapshot(
            principal=principal,
            portfolio_id=portfolio.id,
            valuation=TrustedValuation(
                prices={},
                source="market_data_pit",
                observed_at=after_expiry,
                available_at=after_expiry,
                evidence_digest="f" * 64,
            ),
        )


def test_first_snapshot_preserves_pre_snapshot_loss_and_opens_breaker(
    audited_portfolio_runtime,
) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime, RiskProfile.PRESERVATION)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="first-loss-funding-0001",
        reference_id="first-loss-balance",
    )
    service._record_cash_transaction(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("100"),
        idempotency_key="first-loss-fee-0001",
        event_type="fee",
        reference_type="internal",
        reference_id="first-loss-fee",
        cash_direction=-1,
    )
    snapshot = service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    assert snapshot.nav_per_unit == Decimal("0.900000000000")
    assert snapshot.high_water_nav == Decimal("1.000000000000")
    assert snapshot.drawdown == Decimal("0.1")
    assert snapshot.daily_drawdown == Decimal("0.1")
    with factory() as db:
        breaker = db.scalar(
            select(CircuitBreaker).where(CircuitBreaker.portfolio_id == portfolio.id)
        )
    assert breaker is not None
    assert breaker.kind == "daily_drawdown"


def test_reservation_requires_trusted_risk_baseline(audited_portfolio_runtime) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("100"),
        idempotency_key="baseline-funding-0001",
        reference_id="baseline-balance",
    )
    with pytest.raises(PortfolioError, match="BASELINE_SNAPSHOT_REQUIRED"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("1"),
            purpose_digest="e" * 64,
            idempotency_key="baseline-reservation-0001",
        )
    service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    reservation = service.reserve_cash(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1"),
        purpose_digest="e" * 64,
        idempotency_key="baseline-reservation-0001",
    )
    assert reservation.status == "active"


def test_reassessment_reports_paused_portfolio_and_required_action(
    audited_portfolio_runtime,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime, RiskProfile.AGGRESSIVE)
    result = service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.PRESERVATION,
        answers=_answers(conservative=True),
    )
    assert len(result.paused_portfolios) == 1
    assert result.paused_portfolios[0].portfolio_id == portfolio.id
    assert result.paused_portfolios[0].required_action == "create_compatible_mandate"
    assert result.paused_portfolios[0].pause_reason == "suitability_reassessment"
    overview = service.list_portfolios(principal=principal)[0]
    assert overview.mandate_status == "paused"
    assert overview.mandate_profile == RiskProfile.AGGRESSIVE.value
    assert overview.suitability_effective_profile == RiskProfile.PRESERVATION.value
    assert overview.pause_reason == "suitability_reassessment"
    assert overview.required_action == "create_compatible_mandate"


def test_funding_mode_is_explicit_and_backtest_user_funding_is_refused(
    audited_portfolio_runtime,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    suitability = service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.MODERATE,
        answers=_answers(),
    )
    portfolio = service.create_portfolio(
        principal=principal, name="Shadow funding", base_currency="BRL"
    )
    service.activate_mandate(
        principal=principal,
        portfolio_id=portfolio.id,
        suitability_id=suitability.id,
        profile=RiskProfile.MODERATE,
        mode=RunMode.SHADOW,
    )
    row = service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("100"),
        idempotency_key="shadow-funding-mode-0001",
        reference_id="shadow-opening-balance",
        mode=RunMode.SHADOW,
    )
    assert row.event_type == "shadow_funding"
    with pytest.raises(PortfolioError, match="FUNDING_MODE_MISMATCH"):
        service.record_funding(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("100"),
            idempotency_key="paper-on-shadow-mode-0001",
            reference_id="wrong-mode",
            mode=RunMode.PAPER,
        )
    with pytest.raises(PortfolioError, match="CAPITAL_MODE_NOT_ENABLED"):
        service.record_funding(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("100"),
            idempotency_key="backtest-user-funding-0001",
            reference_id="backtest-forbidden",
            mode=RunMode.BACKTEST,
        )


def test_latest_snapshot_is_read_only_and_omits_raw_valuation_inputs(
    audited_portfolio_runtime,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    assert service.latest_snapshot(principal=principal, portfolio_id=portfolio.id) is None
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("100"),
        idempotency_key="snapshot-read-funding-0001",
        reference_id="snapshot-read-balance",
    )
    created = service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    latest = service.latest_snapshot(principal=principal, portfolio_id=portfolio.id)
    assert latest is not None
    assert latest.sequence == created.sequence
    assert not hasattr(latest, "prices")
    assert not hasattr(latest, "positions")
    assert latest.freshness == "fresh"


def test_internal_allocator_applies_aggregate_caps_and_reserves_atomically(
    audited_portfolio_runtime,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="allocator-opening-funding-0001",
        reference_id="allocator-opening",
    )
    service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    observed = service._now()
    proposal = TrustedAllocationRequest(
        raw_target_weights={"BTC/BRL": Decimal("0.50"), "ETH/BRL": Decimal("0.50")},
        correlations={("BTC/BRL", "ETH/BRL"): Decimal("0.95")},
        assets={
            symbol: TrustedAssetAllocation(
                symbol=symbol,
                asset_class="crypto",
                price=price,
                conviction=Decimal("1"),
                realized_annual_volatility=Decimal("0.10"),
                average_daily_notional=Decimal("1000000"),
            )
            for symbol, price in {
                "BTC/BRL": Decimal("500000"),
                "ETH/BRL": Decimal("20000"),
            }.items()
        },
        source="analysis_pit",
        observed_at=observed,
        available_at=observed,
        evidence_digest="c" * 64,
    )
    result = service.allocate_and_reserve(
        principal=principal,
        portfolio_id=portfolio.id,
        proposal=proposal,
        idempotency_key="allocator-reservation-0001",
    )
    assert sum((line.capped_weight for line in result.lines), Decimal("0")) <= Decimal(
        "0.10"
    )
    assert all(line.sizing.target_weight <= line.capped_weight for line in result.lines)
    assert result.total_order_notional == Decimal("40.00000000")
    assert result.reservation.amount == result.total_order_notional
    assert result.reservation.purpose_digest == result.allocation_digest
    replay = service.allocate_and_reserve(
        principal=principal,
        portfolio_id=portfolio.id,
        proposal=proposal,
        idempotency_key="allocator-reservation-0001",
    )
    assert replay.reservation.id == result.reservation.id


@pytest.mark.parametrize(
    ("initial_ttl", "replay_ttl"),
    [
        (timedelta(seconds=30), timedelta(hours=1)),
        (timedelta(hours=1), timedelta(seconds=30)),
    ],
)
def test_allocator_replay_binds_requested_ttl(
    audited_portfolio_runtime,
    initial_ttl,
    replay_ttl,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key=f"allocator-ttl-{int(initial_ttl.total_seconds())}-funding",
        reference_id=f"allocator-ttl-{int(initial_ttl.total_seconds())}",
    )
    service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    proposal = _single_asset_proposal(service)
    key = f"allocator-ttl-{int(initial_ttl.total_seconds())}-reservation"
    service.allocate_and_reserve(
        principal=principal,
        portfolio_id=portfolio.id,
        proposal=proposal,
        idempotency_key=key,
        ttl=initial_ttl,
    )

    with pytest.raises(PortfolioError, match="IDEMPOTENCY_PAYLOAD_MISMATCH"):
        service.allocate_and_reserve(
            principal=principal,
            portfolio_id=portfolio.id,
            proposal=proposal,
            idempotency_key=key,
            ttl=replay_ttl,
        )


def test_allocator_replaces_elapsed_sqlite_reservation_without_permanent_lockout(
    audited_portfolio_runtime,
    monkeypatch,
) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="allocator-expiry-funding-0001",
        reference_id="allocator-expiry-funding",
    )
    service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    observed = service._now()
    proposal = TrustedAllocationRequest(
        raw_target_weights={"BTC/BRL": Decimal("0.05")},
        correlations={},
        assets={
            "BTC/BRL": TrustedAssetAllocation(
                symbol="BTC/BRL",
                asset_class="crypto",
                price=Decimal("500000"),
                conviction=Decimal("1"),
                realized_annual_volatility=Decimal("0.10"),
                average_daily_notional=Decimal("1000000"),
            )
        },
        source="analysis_pit",
        observed_at=observed,
        available_at=observed,
        evidence_digest="9" * 64,
    )
    first = service.allocate_and_reserve(
        principal=principal,
        portfolio_id=portfolio.id,
        proposal=proposal,
        idempotency_key="allocator-expiry-reservation-0001",
        ttl=timedelta(seconds=30),
    )
    with factory() as db:
        version_after_first = db.get(Portfolio, portfolio.id).balance_version

    after_expiry = first.reservation.expires_at + timedelta(microseconds=1)
    monkeypatch.setattr(service, "_now", lambda: after_expiry)
    with pytest.raises(PortfolioError, match="IDEMPOTENCY_RESERVATION_FINALIZED"):
        service.allocate_and_reserve(
            principal=principal,
            portfolio_id=portfolio.id,
            proposal=proposal,
            idempotency_key="allocator-expiry-reservation-0001",
            ttl=timedelta(seconds=30),
        )
    second = service.allocate_and_reserve(
        principal=principal,
        portfolio_id=portfolio.id,
        proposal=proposal,
        idempotency_key="allocator-expiry-reservation-0002",
        ttl=timedelta(seconds=30),
    )

    with factory() as db:
        assert db.get(CashReservation, first.reservation.id).status == "expired"
        assert db.get(CashReservation, second.reservation.id).status == "active"
        assert db.get(Portfolio, portfolio.id).balance_version == version_after_first + 2
    with pytest.raises(PortfolioError, match="IDEMPOTENCY_RESERVATION_FINALIZED"):
        service.allocate_and_reserve(
            principal=principal,
            portfolio_id=portfolio.id,
            proposal=proposal,
            idempotency_key="allocator-expiry-reservation-0001",
            ttl=timedelta(seconds=30),
        )


def test_allocator_counts_existing_exposure_before_reserving_new_crypto(
    audited_portfolio_runtime,
) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="existing-exposure-funding-0001",
        reference_id="existing-exposure",
    )
    with factory() as db, db.begin():
        db.add(
            PortfolioPosition(
                tenant_id=principal.tenant_id,
                portfolio_id=portfolio.id,
                symbol="BTC/BRL",
                asset_class="crypto",
                quantity=Decimal("1"),
                average_cost=Decimal("100"),
                realized_pnl=Decimal("0"),
                version=1,
            )
        )
    observed = service._now()
    service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=TrustedValuation(
            prices={"BTC/BRL": Decimal("100")},
            source="market_data_pit",
            observed_at=observed,
            available_at=observed,
            evidence_digest="e" * 64,
        ),
    )
    observed = service._now()
    proposal = TrustedAllocationRequest(
        raw_target_weights={"BTC/BRL": Decimal("0"), "ETH/BRL": Decimal("0.50")},
        correlations={("BTC/BRL", "ETH/BRL"): Decimal("0")},
        assets={
            "BTC/BRL": TrustedAssetAllocation(
                symbol="BTC/BRL",
                asset_class="crypto",
                price=Decimal("100"),
                conviction=Decimal("0"),
                realized_annual_volatility=Decimal("0.10"),
                average_daily_notional=Decimal("1000000"),
            ),
            "ETH/BRL": TrustedAssetAllocation(
                symbol="ETH/BRL",
                asset_class="crypto",
                price=Decimal("10"),
                conviction=Decimal("1"),
                realized_annual_volatility=Decimal("0.10"),
                average_daily_notional=Decimal("1000000"),
            ),
        },
        source="analysis_pit",
        observed_at=observed,
        available_at=observed,
        evidence_digest="f" * 64,
    )
    with pytest.raises(PortfolioError, match="NO_REBALANCE_REQUIRED"):
        service.allocate_and_reserve(
            principal=principal,
            portfolio_id=portfolio.id,
            proposal=proposal,
            idempotency_key="existing-exposure-reserve-0001",
        )


def test_allocator_rejects_stale_risk_baseline(
    audited_portfolio_runtime,
    monkeypatch,
) -> None:
    service, principal, _factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="stale-baseline-funding-0001",
        reference_id="stale-baseline",
    )
    service.capture_trusted_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        valuation=_valuation(service),
    )
    future = service._now() + timedelta(hours=2)
    monkeypatch.setattr(service, "_now", lambda: future)
    proposal = TrustedAllocationRequest(
        raw_target_weights={"BTC/BRL": Decimal("0.01")},
        correlations={},
        assets={
            "BTC/BRL": TrustedAssetAllocation(
                symbol="BTC/BRL",
                asset_class="crypto",
                price=Decimal("500000"),
                conviction=Decimal("1"),
                realized_annual_volatility=Decimal("0.10"),
                average_daily_notional=Decimal("1000000"),
            )
        },
        source="analysis_pit",
        observed_at=future,
        available_at=future,
        evidence_digest="1" * 64,
    )
    with pytest.raises(PortfolioError, match="ALLOCATION_BASELINE_STALE"):
        service.allocate_and_reserve(
            principal=principal,
            portfolio_id=portfolio.id,
            proposal=proposal,
            idempotency_key="stale-baseline-reserve-0001",
        )


def test_breaker_action_precedes_paused_mandate_action(audited_portfolio_runtime) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, mandate = _active(audited_portfolio_runtime, RiskProfile.AGGRESSIVE)
    with factory() as db, db.begin():
        db.add(
            CircuitBreaker(
                tenant_id=principal.tenant_id,
                portfolio_id=portfolio.id,
                mandate_id=mandate.id,
                kind="maximum_drawdown",
                status="open",
                observed_value=Decimal("0.30"),
                threshold_value=Decimal("0.25"),
                evidence_digest="d" * 64,
                triggered_at=service._now(),
            )
        )
    service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.PRESERVATION,
        answers=_answers(conservative=True),
    )
    overview = service.list_portfolios(principal=principal)[0]
    assert overview.mandate_status == "paused"
    assert overview.circuit_breaker_open is True
    assert overview.required_action == "review_risk_hold"
    with pytest.raises(PortfolioError, match="CIRCUIT_BREAKER_OPEN"):
        service.transition_mandate(
            principal=principal,
            portfolio_id=portfolio.id,
            target_status="active",
        )


def test_unit_delta_rounding_matches_postgresql_numeric_round(audited_portfolio_runtime) -> None:
    service, principal, factory = audited_portfolio_runtime
    portfolio, _mandate = _active(audited_portfolio_runtime)
    with factory() as db, db.begin():
        persisted = db.get(Portfolio, portfolio.id)
        assert persisted is not None
        persisted.last_nav_per_unit = Decimal("2")
    row = service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("0.246913578025"),
        idempotency_key="postgres-rounding-parity-0001",
        reference_id="rounding-boundary",
    )
    assert row.unit_delta == Decimal("0.123456789013")


def test_migration_preflight_rejects_transaction_without_postings() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260908_0020_portfolio_audit_hardening.py"
    )
    spec = importlib.util.spec_from_file_location("m4_audit_hardening", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite+pysqlite://")
    with engine.begin() as db:
        db.exec_driver_sql(
            "CREATE TABLE portfolio_mandates "
            "(mode TEXT, status TEXT, ended_at TEXT, effective_at TEXT)"
        )
        db.exec_driver_sql(
            "CREATE TABLE portfolio_ledger_transactions "
            "(id TEXT, tenant_id TEXT, portfolio_id TEXT, currency TEXT, "
            "amount NUMERIC, event_type TEXT)"
        )
        db.exec_driver_sql(
            "CREATE TABLE portfolio_ledger_postings "
            "(id TEXT, transaction_id TEXT, tenant_id TEXT, portfolio_id TEXT, "
            "account TEXT, asset TEXT, quantity NUMERIC)"
        )
        db.exec_driver_sql(
            "INSERT INTO portfolio_ledger_transactions VALUES "
            "('tx','tenant','portfolio','BRL',100,'funding')"
        )
        with pytest.raises(RuntimeError, match="M4_INVALID_LEGACY_LEDGER"):
            module._run_preflight(db)
    engine.dispose()


def test_postgresql_guards_cover_table_shape_and_reservation_updates() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260908_0020_portfolio_audit_hardening.py"
    ).read_text(encoding="utf-8")
    derived_start = migration.index("CREATE FUNCTION portfolio_assert_derived_state")
    branch_at = migration.index("IF TG_TABLE_NAME = 'portfolios'", derived_start)
    generic_new_at = migration.index("NEW.portfolio_id", branch_at)
    assert branch_at < generic_new_at
    assert '"AFTER INSERT OR UPDATE",' in migration
    assert "NEW.mandate_id IS DISTINCT FROM active_mandate" in migration
    assert '["mandate_id", "tenant_id", "portfolio_id"]' in migration


def test_postgresql_offline_plan_contains_preflight_before_m4_ddl() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260908_0020_portfolio_audit_hardening.py"
    ).read_text(encoding="utf-8")
    assert migration.index("_emit_postgres_preflight()") < migration.index(
        'op.add_column("portfolios"'
    )
    for finding in (
        "M4_UNSUPPORTED_LIVE_MANDATE",
        "M4_NEGATIVE_LEGACY_UNITS",
        "M4_INVALID_MANDATE_LIFECYCLE",
        "M4_INVALID_LEGACY_LEDGER",
    ):
        assert finding in migration


def test_portfolio_capability_migration_is_parameter_bound_and_fail_closed() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260909_0021_portfolio_capabilities.py"
    ).read_text(encoding="utf-8")
    assert "portfolio_current_tenant(false)" in migration
    assert "portfolio_current_tenant(true)" in migration
    assert "p_expires_at - p_issued_at > 30" in migration
    assert "PORTFOLIO_CAPABILITY_REPLAY" in migration
    assert "p_ttl_microseconds" in migration
    assert "TRADINGAGENTS_SESSION_TOKEN_PEPPER" in migration
    assert "key_material=EXCLUDED.key_material" in migration
    assert "20260909_0021 is a one-way security migration" in migration


def test_portfolio_operation_ports_are_snapshot_bound_and_least_privilege() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260909_0022_portfolio_operation_ports.py"
    ).read_text(encoding="utf-8")
    verifier = migration[
        migration.index("def _replace_verifier") : migration.index(
            "def _install_nonce_maintenance"
        )
    ]
    assert "p_expires_at<=v_now" in verifier
    assert "DELETE FROM" not in verifier
    assert "FOR UPDATE SKIP LOCKED" in migration
    assert "portfolio_prune_capability_nonces_v1" in migration
    assert "uq_portfolio_single_active_reservation" in migration
    assert "source_state_digest" in migration
    assert "portfolio_source_state_digest_v1" in migration
    assert "portfolio_capture_snapshot_v1" in migration
    assert "p_snapshot_id uuid" in migration
    assert "p_snapshot_digest text" in migration
    assert "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER" in migration
    assert "portfolio_positions\", \"portfolio_snapshots\", \"portfolio_circuit_breakers" in migration
    assert "jsonb_object_length" not in migration
    assert "irreversible security migration" in migration
    restricted = migration[
        migration.index("def _restrict_control_tables") : migration.index(
            "def _replace_reserve_port"
        )
    ]
    assert restricted.index("SET CONSTRAINTS ALL IMMEDIATE") < restricted.index(
            "ck_reservation_active_snapshot"
        )
    reserve_port = migration[
        migration.index("def _replace_reserve_port") : migration.index(
            "def _install_snapshot_port"
        )
    ]
    snapshot_port = migration[
        migration.index("def _install_snapshot_port") : migration.index("def downgrade")
    ]
    for port in (reserve_port, snapshot_port):
        assert "p_nonce,p_mac,false" in port
        assert "p_nonce,p_mac,true" in port
        assert port.index("p_nonce,p_mac,false") < port.index("FOR UPDATE")
        assert port.index("FOR UPDATE") < port.index("p_nonce,p_mac,true")
    assert "[0-9]{0,25}" in snapshot_port
    runbook = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "runbooks"
        / "portfolio-operation-ports-migration.md"
    ).read_text(encoding="utf-8")
    assert "point-in-time recovery" in runbook
    assert "Never attempt a schema downgrade" in runbook


def test_reservation_replay_contract_binds_ttl_and_rechecks_release_capability() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260909_0023_reservation_replay_contract.py"
    ).read_text(encoding="utf-8")
    assert "expires_at - v_result.created_at" in migration
    assert "p_ttl_microseconds * interval '1 microsecond'" in migration
    assert "IDEMPOTENCY_PAYLOAD_MISMATCH" in migration
    assert "portfolio_reserve_cash_v1_0022_impl" in migration
    assert "FROM PUBLIC,tradingagents_app,tradingagents_worker" in migration
    release_port = migration[
        migration.index("def _replace_release_port") : migration.index("def downgrade")
    ]
    assert release_port.index("p_mac,\n                false") < release_port.index(
        "FOR UPDATE"
    )
    assert release_port.index("FOR UPDATE") < release_port.index(
        "p_mac,\n                true"
    )
    assert "20260909_0023 is a one-way security migration" in migration


def test_postgresql_public_reservation_port_checks_ttl_before_finalization() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260909_0024_reservation_contract_parity.py"
    ).read_text(encoding="utf-8")
    assert migration.index("SELECT * INTO v_existing") < migration.index(
        "portfolio_reserve_cash_v1_0022_impl"
    )
    assert migration.index("IDEMPOTENCY_PAYLOAD_MISMATCH") < migration.index(
        "portfolio_reserve_cash_v1_0022_impl"
    )
    assert "p_mac,\n                false" in migration
    assert "FROM PUBLIC,tradingagents_worker" in migration
    assert "20260909_0024 is a one-way security migration" in migration
