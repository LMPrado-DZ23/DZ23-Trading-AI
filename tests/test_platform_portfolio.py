from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
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
    LedgerPosting,
    PortfolioError,
    PortfolioService,
    SizingInput,
    cap_target_weights,
    policy_for,
    size_position,
)


@pytest.fixture
def portfolio_runtime():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(factory, token_hasher=TokenHasher(b"p" * 32))
    registration = identity.register_tenant(
        tenant_name="Carteira Teste",
        tenant_slug=f"portfolio-{uuid4().hex[:12]}",
        owner_email=f"portfolio-{uuid4().hex[:12]}@example.test",
        display_name="Pessoa Teste",
        password="correct horse battery staple",
    )
    principal = IdentityPrincipal(
        tenant_id=registration.tenant_id,
        user_id=registration.user_id,
        membership_id=registration.membership_id,
        session_id=uuid4(),
        role=MembershipRole.OWNER,
    )
    service = PortfolioService(factory, idempotency_digest_key=b"i" * 32)
    yield service, principal, factory
    engine.dispose()


def _answers(**overrides):
    answers = {
        "horizon_years": 10,
        "maximum_loss_percent": 40,
        "emergency_fund_months": 12,
        "income_stability": "stable",
        "experience": "experienced",
    }
    answers.update(overrides)
    return answers


def _active_portfolio(runtime, profile=RiskProfile.MODERATE):
    service, principal, _factory = runtime
    assessment = service.assess_suitability(
        principal=principal,
        requested_profile=profile,
        answers=_answers(),
    )
    portfolio = service.create_portfolio(
        principal=principal,
        name=f"Principal {uuid4().hex[:6]}",
        base_currency="brl",
    )
    mandate = service.activate_mandate(
        principal=principal,
        portfolio_id=portfolio.id,
        suitability_id=assessment.id,
        profile=profile,
        mode=RunMode.PAPER,
    )
    return portfolio, mandate


def test_suitability_always_selects_the_more_restrictive_profile(portfolio_runtime) -> None:
    service, principal, _factory = portfolio_runtime
    assessment = service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.AGGRESSIVE,
        answers=_answers(emergency_fund_months=1),
    )
    assert assessment.maximum_profile == RiskProfile.PRESERVATION.value
    assert assessment.effective_profile == RiskProfile.PRESERVATION.value
    assert len(assessment.digest) == 64


@pytest.mark.parametrize(
    ("requested", "maximum", "expected"),
    [
        (RiskProfile.PRESERVATION, RiskProfile.AGGRESSIVE, RiskProfile.PRESERVATION),
        (RiskProfile.MODERATE, RiskProfile.MODERATE, RiskProfile.MODERATE),
        (RiskProfile.AGGRESSIVE, RiskProfile.CONSERVATIVE, RiskProfile.CONSERVATIVE),
    ],
)
def test_profile_order_is_never_relaxed(portfolio_runtime, requested, maximum, expected) -> None:
    service, principal, _factory = portfolio_runtime
    answer_sets = {
        RiskProfile.AGGRESSIVE: _answers(),
        RiskProfile.MODERATE: _answers(experience="none"),
        RiskProfile.CONSERVATIVE: _answers(income_stability="unstable"),
    }
    row = service.assess_suitability(
        principal=principal,
        requested_profile=requested,
        answers=answer_sets[maximum],
    )
    assert row.effective_profile == expected.value


def test_live_mandate_is_fail_closed_until_execution_milestone(portfolio_runtime) -> None:
    service, principal, _factory = portfolio_runtime
    assessment = service.assess_suitability(
        principal=principal,
        requested_profile=RiskProfile.MODERATE,
        answers=_answers(),
    )
    portfolio = service.create_portfolio(
        principal=principal, name="Conta live bloqueada", base_currency="BRL"
    )
    with pytest.raises(PortfolioError, match="CAPITAL_MODE_NOT_ENABLED"):
        service.activate_mandate(
            principal=principal,
            portfolio_id=portfolio.id,
            suitability_id=assessment.id,
            profile=RiskProfile.MODERATE,
            mode=RunMode.LIVE_SUPERVISED,
        )


def test_ledger_is_double_entry_and_funding_is_idempotent(portfolio_runtime) -> None:
    service, principal, factory = portfolio_runtime
    portfolio, _mandate = _active_portfolio(portfolio_runtime)
    first = service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000.00"),
        idempotency_key="funding-request-0001",
        reference_id="bank-transfer-1",
    )
    replay = service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000.00"),
        idempotency_key="funding-request-0001",
        reference_id="bank-transfer-1",
    )
    assert replay.id == first.id
    with factory() as db:
        postings = db.scalars(
            select(LedgerPosting).where(LedgerPosting.transaction_id == first.id)
        ).all()
        assert len(postings) == 2
        assert sum((row.quantity for row in postings), Decimal(0)) == 0


def test_cash_reservation_cannot_exceed_available_balance(portfolio_runtime) -> None:
    service, principal, _factory = portfolio_runtime
    portfolio, _mandate = _active_portfolio(portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("100"),
        idempotency_key="funding-request-0002",
        reference_id="deposit-2",
    )
    reservation = service.reserve_cash(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("80"),
        purpose_digest="a" * 64,
        idempotency_key="reservation-request-0001",
    )
    with pytest.raises(PortfolioError, match="INSUFFICIENT_AVAILABLE_CASH"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("21"),
            purpose_digest="b" * 64,
            idempotency_key="reservation-request-0002",
        )
    assert (
        service.release_reservation(principal=principal, reservation_id=reservation.id)
        == "released"
    )


def test_mandate_pause_is_versioned_and_old_active_row_cannot_be_reused(
    portfolio_runtime,
) -> None:
    service, principal, _factory = portfolio_runtime
    portfolio, mandate = _active_portfolio(portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("100"),
        idempotency_key="funding-pause-0001",
        reference_id="deposit-pause",
    )
    paused = service.transition_mandate(
        principal=principal,
        portfolio_id=portfolio.id,
        target_status="paused",
    )
    assert paused.version == mandate.version + 1
    assert paused.status == "paused"
    with pytest.raises(PortfolioError, match="ACTIVE_MANDATE_REQUIRED"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("1"),
            purpose_digest="d" * 64,
            idempotency_key="reservation-while-paused",
        )
    resumed = service.transition_mandate(
        principal=principal,
        portfolio_id=portfolio.id,
        target_status="active",
    )
    assert resumed.version == paused.version + 1
    assert resumed.status == "active"


def test_concurrent_reservations_never_overdraw(portfolio_runtime) -> None:
    service, principal, factory = portfolio_runtime
    portfolio, _mandate = _active_portfolio(portfolio_runtime)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("100"),
        idempotency_key="funding-concurrency-001",
        reference_id="deposit-race",
    )

    def reserve(index: int) -> str:
        try:
            service.reserve_cash(
                principal=principal,
                portfolio_id=portfolio.id,
                amount=Decimal("60"),
                purpose_digest=str(index) * 64,
                idempotency_key=f"reservation-race-{index:04d}",
            )
            return "reserved"
        except PortfolioError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(reserve, (1, 2)))
    assert outcomes.count("reserved") <= 1
    with factory() as db:
        total = db.scalar(
            select(func.coalesce(func.sum(CashReservation.amount), 0)).where(
                CashReservation.portfolio_id == portfolio.id,
                CashReservation.status == "active",
            )
        )
    assert Decimal(total) <= Decimal("100")


def test_sizing_is_deterministic_capped_and_accepts_no_quantity() -> None:
    policy = policy_for(RiskProfile.MODERATE)
    data = SizingInput(
        capital=Decimal("10000"),
        available_cash=Decimal("8000"),
        price=Decimal("200"),
        conviction=Decimal("0.8"),
        realized_annual_volatility=Decimal("0.5"),
        average_daily_notional=Decimal("100000"),
        current_weight=Decimal("0"),
        trading_cost_bps=Decimal("10"),
    )
    first = size_position(policy, data)
    assert first == size_position(policy, data)
    assert first.target_weight <= policy.max_position_weight
    assert first.order_notional <= Decimal("10000") * policy.max_order_notional_weight
    assert first.quantity == first.order_notional / data.price
    assert "quantity" not in SizingInput.__dataclass_fields__


def test_allocation_caps_correlated_cluster_and_preserves_cash_floor() -> None:
    policy = policy_for(RiskProfile.MODERATE)
    weights = cap_target_weights(
        policy,
        {
            "BTC/USDT": Decimal("0.20"),
            "ETH/USDT": Decimal("0.20"),
            "SOL/USDT": Decimal("0.20"),
            "AAPL": Decimal("0.20"),
        },
        {
            ("BTC/USDT", "ETH/USDT"): Decimal("0.90"),
            ("ETH/USDT", "SOL/USDT"): Decimal("0.85"),
            ("AAPL", "BTC/USDT"): Decimal("0.10"),
        },
    )
    crypto_cluster = sum(weights[symbol] for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"))
    assert crypto_cluster <= policy.max_correlated_bucket_weight
    assert all(value <= policy.max_position_weight for value in weights.values())
    assert sum(weights.values()) <= Decimal(1) - policy.minimum_cash_weight


@pytest.mark.parametrize(
    "field,value",
    [
        ("capital", Decimal("NaN")),
        ("price", Decimal("Infinity")),
        ("conviction", Decimal("-0.1")),
        ("realized_annual_volatility", Decimal("0")),
    ],
)
def test_sizing_rejects_non_finite_and_invalid_inputs(field, value) -> None:
    values = {
        "capital": Decimal("1000"),
        "available_cash": Decimal("1000"),
        "price": Decimal("10"),
        "conviction": Decimal("0.5"),
        "realized_annual_volatility": Decimal("0.2"),
        "average_daily_notional": Decimal("10000"),
    }
    values[field] = value
    with pytest.raises(ValueError):
        size_position(policy_for(RiskProfile.MODERATE), SizingInput(**values))


def test_drawdown_opens_persistent_circuit_breaker(portfolio_runtime) -> None:
    service, principal, factory = portfolio_runtime
    portfolio, _mandate = _active_portfolio(portfolio_runtime, RiskProfile.PRESERVATION)
    service.record_funding(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("1000"),
        idempotency_key="funding-breaker-0001",
        reference_id="deposit-breaker",
    )
    captured = service._now() + timedelta(seconds=1)
    first = service.capture_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        prices={},
        captured_at=captured,
    )
    assert first.equity == Decimal("1000")
    # A cash withdrawal is represented in the double-entry ledger internally.
    service._record_cash_transaction(
        principal=principal,
        portfolio_id=portfolio.id,
        amount=Decimal("100"),
        idempotency_key="withdraw-breaker-0001",
        event_type="withdrawal",
        reference_type="test",
        reference_id="withdrawal-1",
        cash_direction=-1,
    )
    second = service.capture_snapshot(
        principal=principal,
        portfolio_id=portfolio.id,
        prices={},
        captured_at=captured + timedelta(hours=1),
    )
    assert second.drawdown == Decimal("0.1")
    with pytest.raises(PortfolioError, match="CIRCUIT_BREAKER_OPEN"):
        service.reserve_cash(
            principal=principal,
            portfolio_id=portfolio.id,
            amount=Decimal("1"),
            purpose_digest="c" * 64,
            idempotency_key="reservation-after-breaker",
        )
