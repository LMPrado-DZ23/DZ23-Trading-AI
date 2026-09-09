from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
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
    CircuitBreaker,
    Portfolio,
    PortfolioError,
    PortfolioMandate,
    PortfolioPosition,
    PortfolioService,
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
