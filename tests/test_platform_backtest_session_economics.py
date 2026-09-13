from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, getcontext
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

import tradingagents.platform.backtest.repository as repository_module
from tradingagents.platform.backtest.calendar import BacktestCalendar
from tradingagents.platform.backtest.contracts import (
    AssetType,
    AvailabilityBasis,
    BacktestSpecV3,
    CorporateAction,
    CorporateActionType,
    DailyBar,
    DatasetReference,
    DatasetRole,
    FxObservation,
    InstrumentSpec,
    MarketCalendar,
    canonical_digest,
)
from tradingagents.platform.backtest.errors import DatasetIntegrityError
from tradingagents.platform.backtest.repository import (
    FrozenDatasetRepository,
    _resolve_economic_event_projection,
    _resolve_fx_rate_projection,
)
from tradingagents.platform.backtest.simulation_economics import (
    AuthorizedEconomicEventView,
    AuthorizedFxConversion,
    CorporateActionApplication,
    CorporateActionProjection,
    EconomicEventView,
    EconomicPhase,
    FxConversionMode,
    FxRateProjection,
    InstrumentEconomicsBinding,
    SimulationEconomicsError,
    SimulationEconomicsPolicy,
    _convert_quote_to_functional,
    fx_authority_book_digest,
)
from tradingagents.platform.backtest.simulation_economics_authority import (
    open_simulation_economics_authority,
)
from tradingagents.platform.backtest.simulation_ledger import (
    InstrumentSimulationRule,
    SimulationLedgerPolicy,
)

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_TENANT_ID = UUID("10000000-0000-0000-0000-000000000002")
DATASET_ID = UUID("20000000-0000-0000-0000-000000000001")
INSTRUMENT_ID = UUID("30000000-0000-0000-0000-000000000001")
SPLIT_ID = UUID("40000000-0000-0000-0000-000000000001")
DIVIDEND_ID = UUID("40000000-0000-0000-0000-000000000002")
FX_OLD_ID = UUID("50000000-0000-0000-0000-000000000001")
FX_NEW_ID = UUID("50000000-0000-0000-0000-000000000002")
SECOND_SPLIT_ID = UUID("40000000-0000-0000-0000-000000000003")
CREATED_AT = datetime(2024, 1, 10, tzinfo=UTC)
BACKTEST_ID = UUID("60000000-0000-0000-0000-000000000001")
LEDGER_ID = UUID("70000000-0000-0000-0000-000000000001")
BENCHMARK_ID = UUID("80000000-0000-0000-0000-000000000001")
BENCHMARK_DATASET_ID = UUID("90000000-0000-0000-0000-000000000001")
VALUATION_REFERENCE_DIGEST = "c" * 64


def _instrument() -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=INSTRUMENT_ID,
        symbol="BTC/USD",
        asset_type=AssetType.CRYPTO,
        venue="test",
        venue_instrument_id="BTC/USD",
        base_currency="BTC",
        quote_currency="USD",
        market_calendar=MarketCalendar.CONTINUOUS,
        published_at=datetime(2023, 12, 1, tzinfo=UTC),
        available_at=datetime(2023, 12, 1, 0, 1, tzinfo=UTC),
        listing_date=date(2024, 1, 1),
        source_digest="1" * 64,
    )


def _bar(session_date: date) -> DailyBar:
    calendar = BacktestCalendar(MarketCalendar.CONTINUOUS)
    window = calendar.session_window(session_date)
    opened = window.open_at
    closed = window.close_at
    available_at = calendar.settlement_policy_available_at(session_date)
    return DailyBar(
        instrument_id=INSTRUMENT_ID,
        session_date=session_date,
        session_open_at=opened,
        session_close_at=closed,
        fetched_at=available_at,
        available_at=available_at,
        availability_basis=AvailabilityBasis.EXCHANGE_SETTLEMENT_POLICY,
        open="100",
        high="110",
        low="90",
        close="105",
        volume="1000",
        source_digest=hashlib.sha256(session_date.isoformat().encode()).hexdigest(),
    )


def _split(*, available_at: datetime, revision: int = 1, ratio: str = "2") -> CorporateAction:
    return CorporateAction(
        action_id=SPLIT_ID,
        instrument_id=INSTRUMENT_ID,
        revision=revision,
        action_type=CorporateActionType.SPLIT,
        published_at=available_at - timedelta(seconds=1),
        available_at=available_at,
        ex_date=date(2024, 1, 3),
        effective_date=date(2024, 1, 3),
        split_ratio=ratio,
        split_ratio_convention="new_shares_per_old_share",
        source_digest=str(revision) * 64,
    )


def _dividend() -> CorporateAction:
    return CorporateAction(
        action_id=DIVIDEND_ID,
        instrument_id=INSTRUMENT_ID,
        action_type=CorporateActionType.CASH_DIVIDEND,
        published_at=datetime(2024, 1, 1, 12, tzinfo=UTC),
        available_at=datetime(2024, 1, 1, 12, 1, tzinfo=UTC),
        ex_date=date(2024, 1, 2),
        effective_date=date(2024, 1, 2),
        pay_date=date(2024, 1, 3),
        cash_amount="1.25",
        cash_amount_basis="net_cash_per_entitled_share",
        currency="USD",
        source_digest="d" * 64,
    )


def _fx(
    observation_id: UUID,
    *,
    observed_at: datetime,
    available_at: datetime,
    rate: str,
    base: str = "BRL",
    quote: str = "USD",
) -> FxObservation:
    return FxObservation(
        observation_id=observation_id,
        base_currency=base,
        quote_currency=quote,
        observed_at=observed_at,
        available_at=available_at,
        rate=rate,
        source_digest=hashlib.sha256(str(observation_id).encode()).hexdigest(),
    )


def _ingest(
    tmp_path: Path,
    *,
    actions: tuple[CorporateAction, ...] = (),
    fx: tuple[FxObservation, ...] = (),
):
    repository = FrozenDatasetRepository(tmp_path / "datasets")
    bars = tuple(_bar(date(2024, 1, day)) for day in (1, 2, 3, 4))
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="m5.2c-fixture",
        instrument=_instrument(),
        bars=bars,
        corporate_actions=actions,
        fx_observations=fx,
    )
    return repository, manifest, bars


def _open_authority(
    tmp_path: Path,
    *,
    actions: tuple[CorporateAction, ...] = (),
    fx: tuple[FxObservation, ...] = (),
):
    repository, manifest, bars = _ingest(tmp_path, actions=actions, fx=fx)
    ledger_policy = SimulationLedgerPolicy(
        ledger_id=LEDGER_ID,
        opened_at=datetime(2024, 1, 1, tzinfo=UTC),
        base_currency="BRL",
        initial_cash="10000.00",
        money_quantum="0.01",
        instrument_rules=(
            InstrumentSimulationRule(
                instrument_id=INSTRUMENT_ID,
                currency="BRL",
                lot_size="0.00000001",
                price_tick="0.01",
            ),
        ),
    )
    economics_policy = SimulationEconomicsPolicy(
        tenant_id=TENANT_ID,
        backtest_id=BACKTEST_ID,
        ledger_id=LEDGER_ID,
        functional_currency="BRL",
        fx_max_staleness_seconds=3_600,
        fx_book_digest=fx_authority_book_digest(fx),
        money_quantum="0.01",
        instrument_bindings=(
            InstrumentEconomicsBinding(
                instrument_id=INSTRUMENT_ID,
                dataset_id=DATASET_ID,
                manifest_digest=manifest.digest,
                quote_currency="USD",
            ),
        ),
    )
    spec = BacktestSpecV3(
        backtest_id=BACKTEST_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        dataset_references=(
            DatasetReference(
                instrument_id=INSTRUMENT_ID,
                dataset_id=DATASET_ID,
                manifest_digest=manifest.digest,
                role=DatasetRole.TRADEABLE,
            ),
            DatasetReference(
                instrument_id=BENCHMARK_ID,
                dataset_id=BENCHMARK_DATASET_ID,
                manifest_digest="b" * 64,
                role=DatasetRole.BENCHMARK,
            ),
        ),
        instrument_ids=(INSTRUMENT_ID,),
        benchmark_instrument_id=BENCHMARK_ID,
        start_session=bars[0].session_date,
        end_session=bars[-1].session_date,
        base_currency="BRL",
        initial_cash="10000.00",
        simulation_policy_digest=ledger_policy.digest,
        economics_policy_digest=economics_policy.digest,
    )
    authority = open_simulation_economics_authority(
        repository,
        DATASET_ID,
        backtest_spec=spec,
        ledger_policy=ledger_policy,
        economics_policy=economics_policy,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_economics_policy_digest=economics_policy.digest,
    )
    return authority, repository, spec, ledger_policy, economics_policy, bars


def test_split_and_dividend_conventions_are_explicit() -> None:
    split = _split(available_at=datetime(2024, 1, 2, 23, 59, 30, tzinfo=UTC))
    assert split.split_ratio_convention == "new_shares_per_old_share"
    assert _dividend().cash_amount_basis == "net_cash_per_entitled_share"
    with pytest.raises(ValidationError):
        CorporateAction.model_validate(
            split.model_dump(exclude={"split_ratio_convention"})
        )


def test_execution_event_clock_is_separate_from_decision_clock(tmp_path: Path) -> None:
    split = _split(available_at=datetime(2024, 1, 2, 23, 59, 30, tzinfo=UTC))
    repository, manifest, bars = _ingest(tmp_path, actions=(split,))
    decision = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=bars[0].available_at,
        decision_session_date=bars[0].session_date,
    )
    assert decision.corporate_actions == ()
    event_view = _resolve_economic_event_projection(
        repository,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        session_date=bars[2].session_date,
        phase=EconomicPhase.PRE_OPEN,
        as_of=bars[2].session_open_at,
    )
    assert event_view.actions[0].application is CorporateActionApplication.SPLIT


def test_action_available_after_its_phase_fails_closed(tmp_path: Path) -> None:
    split = _split(available_at=datetime(2024, 1, 3, 0, 0, 1, tzinfo=UTC))
    repository, manifest, bars = _ingest(tmp_path, actions=(split,))
    with pytest.raises(DatasetIntegrityError, match="unavailable"):
        _resolve_economic_event_projection(
            repository,
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_instrument_id=INSTRUMENT_ID,
            expected_manifest_digest=manifest.digest,
            session_date=bars[2].session_date,
            phase=EconomicPhase.PRE_OPEN,
            as_of=bars[2].session_open_at,
        )


def test_action_revision_selection_is_point_in_time_and_tamper_evident(
    tmp_path: Path,
) -> None:
    first = _split(available_at=datetime(2024, 1, 2, 23, tzinfo=UTC))
    future = _split(
        available_at=datetime(2024, 1, 3, 0, 1, tzinfo=UTC),
        revision=2,
        ratio="2",
    )
    repository, manifest, bars = _ingest(tmp_path, actions=(first, future))
    view = _resolve_economic_event_projection(
        repository,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        session_date=bars[2].session_date,
        phase=EconomicPhase.PRE_OPEN,
        as_of=bars[2].session_open_at,
    )
    assert view.actions[0].revision == 1
    assert view.actions[0].split_ratio == Decimal("2")
    tampered = view.actions[0].model_copy(update={"split_ratio": Decimal("200")})
    with pytest.raises(ValidationError, match="digest"):
        type(tampered).model_validate(tampered.model_dump(mode="python"))


def test_dividend_entitlement_and_payment_have_distinct_phases(tmp_path: Path) -> None:
    repository, manifest, bars = _ingest(tmp_path, actions=(_dividend(),))
    entitlement = _resolve_economic_event_projection(
        repository,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        session_date=bars[1].session_date,
        phase=EconomicPhase.PRE_OPEN,
        as_of=bars[1].session_open_at,
    )
    payment = _resolve_economic_event_projection(
        repository,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        session_date=bars[2].session_date,
        phase=EconomicPhase.POST_CLOSE,
        as_of=bars[2].session_close_at,
    )
    assert entitlement.actions[0].application is CorporateActionApplication.DIVIDEND_ENTITLEMENT
    assert payment.actions[0].application is CorporateActionApplication.DIVIDEND_PAYMENT


def test_future_dividend_revision_cannot_rewrite_past_payment(tmp_path: Path) -> None:
    first = _dividend()
    repository_clock = _bar(date(2024, 1, 3)).session_close_at
    future = first.model_copy(
        update={
            "revision": 2,
            "published_at": repository_clock + timedelta(seconds=30),
            "available_at": repository_clock + timedelta(minutes=1),
            "pay_date": date(2024, 1, 4),
            "source_digest": "e" * 64,
        }
    )
    with pytest.raises(DatasetIntegrityError, match="economic terms"):
        _ingest(tmp_path, actions=(first, future))


def test_multiple_same_session_splits_and_wrong_clock_fail_closed(tmp_path: Path) -> None:
    first = _split(available_at=datetime(2024, 1, 2, 23, tzinfo=UTC))
    second = first.model_copy(
        update={"action_id": SECOND_SPLIT_ID, "source_digest": "a" * 64}
    )
    repository, manifest, bars = _ingest(tmp_path, actions=(first, second))
    common = {
        "expected_tenant_id": TENANT_ID,
        "expected_instrument_id": INSTRUMENT_ID,
        "expected_manifest_digest": manifest.digest,
        "session_date": bars[2].session_date,
        "phase": EconomicPhase.PRE_OPEN,
    }
    with pytest.raises(DatasetIntegrityError, match="ambiguous"):
        _resolve_economic_event_projection(
            repository, DATASET_ID, as_of=bars[2].session_open_at, **common
        )
    with pytest.raises(DatasetIntegrityError, match="time"):
        _resolve_economic_event_projection(
            repository,
            DATASET_ID,
            as_of=bars[2].session_open_at + timedelta(microseconds=1),
            **common,
        )


def test_pre_open_actions_use_explicit_economic_precedence(tmp_path: Path) -> None:
    split = _split(available_at=datetime(2024, 1, 2, 22, tzinfo=UTC))
    dividend = _dividend().model_copy(
        update={
            "available_at": datetime(2024, 1, 2, 22, 1, tzinfo=UTC),
            "published_at": datetime(2024, 1, 2, 22, tzinfo=UTC),
            "ex_date": date(2024, 1, 3),
            "effective_date": date(2024, 1, 3),
            "pay_date": date(2024, 1, 4),
        }
    )
    repository, manifest, bars = _ingest(tmp_path, actions=(dividend, split))
    view = _resolve_economic_event_projection(
        repository,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        session_date=bars[2].session_date,
        phase=EconomicPhase.PRE_OPEN,
        as_of=bars[2].session_open_at,
    )
    assert [item.application for item in view.actions] == [
        CorporateActionApplication.SPLIT,
        CorporateActionApplication.DIVIDEND_ENTITLEMENT,
    ]
def test_fx_identity_requires_no_observation(tmp_path: Path) -> None:
    repository, manifest, bars = _ingest(tmp_path)
    projection = _resolve_fx_rate_projection(
        repository,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        functional_currency="USD",
        source_currency="USD",
        event_at=bars[2].session_open_at,
        max_staleness_seconds=86_400,
    )
    assert projection.mode is FxConversionMode.IDENTITY
    assert projection.rate == 1
    assert projection.observation_id is None


def test_economic_authorities_reject_cross_tenant_and_manifest_substitution(
    tmp_path: Path,
) -> None:
    repository, manifest, bars = _ingest(tmp_path)
    common = {
        "expected_instrument_id": INSTRUMENT_ID,
        "functional_currency": "USD",
        "source_currency": "USD",
        "event_at": bars[2].session_open_at,
        "max_staleness_seconds": 86_400,
    }
    with pytest.raises(DatasetIntegrityError, match="tenant"):
        _resolve_fx_rate_projection(
            repository,
            DATASET_ID,
            expected_tenant_id=OTHER_TENANT_ID,
            expected_manifest_digest=manifest.digest,
            **common,
        )
    with pytest.raises(DatasetIntegrityError, match="manifest"):
        _resolve_fx_rate_projection(
            repository,
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest="0" * 64,
            **common,
        )


def test_fx_selects_latest_direct_rate_available_at_event(tmp_path: Path) -> None:
    event_at = datetime(2024, 1, 3, tzinfo=UTC)
    older = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(hours=2),
        available_at=event_at - timedelta(hours=1),
        rate="5",
    )
    future = _fx(
        FX_NEW_ID,
        observed_at=event_at - timedelta(minutes=10),
        available_at=event_at + timedelta(microseconds=1),
        rate="6",
    )
    repository, manifest, _ = _ingest(tmp_path, fx=(older, future))
    projection = _resolve_fx_rate_projection(
        repository,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        functional_currency="BRL",
        source_currency="USD",
        event_at=event_at,
        max_staleness_seconds=10_800,
    )
    assert projection.observation_id == FX_OLD_ID
    assert projection.rate == Decimal("5")


def test_fx_missing_stale_inverse_and_ambiguous_rates_fail_closed(tmp_path: Path) -> None:
    event_at = datetime(2024, 1, 3, tzinfo=UTC)
    first = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(hours=2),
        available_at=event_at - timedelta(hours=1),
        rate="5",
    )
    second = _fx(
        FX_NEW_ID,
        observed_at=first.observed_at,
        available_at=event_at - timedelta(minutes=30),
        rate="5.1",
    )
    repository, manifest, _ = _ingest(tmp_path, fx=(first, second))
    common = {
        "expected_tenant_id": TENANT_ID,
        "expected_instrument_id": INSTRUMENT_ID,
        "expected_manifest_digest": manifest.digest,
        "event_at": event_at,
    }
    with pytest.raises(DatasetIntegrityError, match="ambiguous"):
        _resolve_fx_rate_projection(
            repository,
            DATASET_ID,
            functional_currency="BRL",
            source_currency="USD",
            max_staleness_seconds=10_800,
            **common,
        )
    with pytest.raises(DatasetIntegrityError, match="unavailable"):
        _resolve_fx_rate_projection(
            repository,
            DATASET_ID,
            functional_currency="USD",
            source_currency="BRL",
            max_staleness_seconds=10_800,
            **common,
        )
    with pytest.raises(DatasetIntegrityError, match="staleness"):
        stale_repository, stale_manifest, _ = _ingest(tmp_path / "stale", fx=(first,))
        _resolve_fx_rate_projection(
            stale_repository,
            DATASET_ID,
            expected_manifest_digest=stale_manifest.digest,
            functional_currency="BRL",
            source_currency="USD",
            max_staleness_seconds=60,
            **{key: value for key, value in common.items() if key != "expected_manifest_digest"},
        )


def test_fx_conversion_is_exact_single_rounding_and_context_independent(tmp_path: Path) -> None:
    event_at = datetime(2024, 1, 3, tzinfo=UTC)
    rate = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(minutes=1),
        available_at=event_at,
        rate="5",
    )
    repository, manifest, _ = _ingest(tmp_path, fx=(rate,))
    projection = _resolve_fx_rate_projection(
        repository,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        functional_currency="BRL",
        source_currency="USD",
        event_at=event_at,
        max_staleness_seconds=60,
    )
    original_rounding = getcontext().rounding
    getcontext().rounding = ROUND_DOWN
    try:
        conversion = _convert_quote_to_functional(
            Decimal("10.025"), projection, money_quantum=Decimal("0.01")
        )
    finally:
        getcontext().rounding = original_rounding
    assert conversion.functional_amount == Decimal("2.00")
    with pytest.raises(SimulationEconomicsError):
        _convert_quote_to_functional(1.0, projection, money_quantum=Decimal("0.01"))
    with pytest.raises(SimulationEconomicsError, match="dust"):
        _convert_quote_to_functional(
            Decimal("0.01"), projection, money_quantum=Decimal("1")
        )


def test_fx_revision_selection_is_point_in_time(tmp_path: Path) -> None:
    event_at = datetime(2024, 1, 3, tzinfo=UTC)
    first = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(hours=1),
        available_at=event_at - timedelta(minutes=30),
        rate="5",
    )
    second = first.model_copy(
        update={
            "revision": 2,
            "available_at": event_at + timedelta(minutes=1),
            "rate": Decimal("5.2"),
            "source_digest": "f" * 64,
        }
    )
    repository, manifest, _ = _ingest(tmp_path, fx=(first, second))
    projection = _resolve_fx_rate_projection(
        repository,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        functional_currency="BRL",
        source_currency="USD",
        event_at=event_at,
        max_staleness_seconds=7_200,
    )
    assert projection.observation_revision == 1
    assert projection.rate == Decimal("5")


def test_fx_revision_chain_rejects_gaps_and_identity_rewrites(tmp_path: Path) -> None:
    event_at = datetime(2024, 1, 3, tzinfo=UTC)
    first = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(hours=1),
        available_at=event_at - timedelta(minutes=30),
        rate="5",
    )
    gap = first.model_copy(
        update={
            "revision": 3,
            "available_at": event_at + timedelta(minutes=1),
            "source_digest": "b" * 64,
        }
    )
    with pytest.raises(DatasetIntegrityError, match="contiguous"):
        _ingest(tmp_path / "gap", fx=(first, gap))
    rewritten = first.model_copy(
        update={
            "revision": 2,
            "observed_at": first.observed_at + timedelta(seconds=1),
            "available_at": event_at + timedelta(minutes=1),
            "source_digest": "c" * 64,
        }
    )
    with pytest.raises(DatasetIntegrityError, match="cannot rewrite"):
        _ingest(tmp_path / "rewrite", fx=(first, rewritten))


def test_fx_contract_rejects_fractional_staleness_beyond_policy(tmp_path: Path) -> None:
    event_at = datetime(2024, 1, 3, tzinfo=UTC)
    rate = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(seconds=60, microseconds=1),
        available_at=event_at - timedelta(seconds=1),
        rate="5",
    )
    repository, manifest, _ = _ingest(tmp_path, fx=(rate,))
    with pytest.raises(DatasetIntegrityError, match="staleness"):
        _resolve_fx_rate_projection(
            repository,
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_instrument_id=INSTRUMENT_ID,
            expected_manifest_digest=manifest.digest,
            functional_currency="BRL",
            source_currency="USD",
            event_at=event_at,
            max_staleness_seconds=60,
        )


def test_authority_binds_fx_to_spec_policy_instrument_and_session(tmp_path: Path) -> None:
    event_at = _bar(date(2024, 1, 3)).session_open_at
    rate = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(minutes=1),
        available_at=event_at,
        rate="0.2",
    )
    authority, _repository, spec, ledger_policy, economics_policy, bars = _open_authority(
        tmp_path, fx=(rate,)
    )
    result = authority.convert(
        Decimal("10.00"),
        bars[2].session_date,
        EconomicPhase.PRE_OPEN,
        source_reference_digest=VALUATION_REFERENCE_DIGEST,
    )
    assert result.tenant_id == TENANT_ID
    assert result.backtest_id == spec.backtest_id
    assert result.ledger_policy_digest == ledger_policy.digest
    assert result.economics_policy_digest == economics_policy.digest
    assert result.projection.source_currency == "USD"
    assert result.projection.functional_currency == "BRL"
    assert result.projection.max_staleness_seconds == 3_600
    assert result.conversion.money_quantum == Decimal("0.01")
    assert result.conversion.functional_amount == Decimal("50.00")
    assert result.quantitative_claims_status == "ineligible_unverified_coverage"
    assert result.limitations


def test_authority_rederivation_rejects_forged_self_digests(tmp_path: Path) -> None:
    event_at = _bar(date(2024, 1, 3)).session_open_at
    rate = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(minutes=1),
        available_at=event_at,
        rate="0.2",
    )
    authority, _repository, _spec, _ledger, _economics, bars = _open_authority(
        tmp_path, fx=(rate,)
    )
    legitimate = authority.convert(
        Decimal("10.00"),
        bars[2].session_date,
        EconomicPhase.PRE_OPEN,
        source_reference_digest=VALUATION_REFERENCE_DIGEST,
    )
    forged_projection_payload = legitimate.projection.model_dump(
        mode="python", exclude={"projection_digest"}
    )
    forged_projection_payload["rate"] = Decimal("500")
    forged_projection = FxRateProjection(
        **forged_projection_payload,
        projection_digest=canonical_digest(forged_projection_payload),
    )
    forged_conversion = _convert_quote_to_functional(
        Decimal("10.00"), forged_projection, money_quantum=Decimal("0.01")
    )
    forged_payload = legitimate.model_dump(
        mode="python", exclude={"authority_digest"}
    )
    forged_payload["projection"] = forged_projection.model_dump(mode="python")
    forged_payload["conversion"] = forged_conversion.model_dump(mode="python")
    forged = AuthorizedFxConversion(
        **forged_payload,
        authority_digest=canonical_digest(forged_payload),
    )
    with pytest.raises(SimulationEconomicsError):
        authority.verify_conversion(
            forged,
            expected_source_amount=Decimal("10.00"),
            expected_session_date=bars[2].session_date,
            expected_phase=EconomicPhase.PRE_OPEN,
            expected_source_reference_digest=VALUATION_REFERENCE_DIGEST,
        )


def test_authority_verifies_dataset_once_and_reuses_frozen_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_at = _bar(date(2024, 1, 3)).session_open_at
    rate = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(minutes=1),
        available_at=event_at,
        rate="0.2",
    )
    reads = 0
    index_builds = 0
    original = FrozenDatasetRepository._read_dataset_files
    original_index = repository_module._build_economics_index

    def counted(repository, dataset_id):
        nonlocal reads
        reads += 1
        return original(repository, dataset_id)

    def counted_index(dataset):
        nonlocal index_builds
        index_builds += 1
        return original_index(dataset)

    monkeypatch.setattr(FrozenDatasetRepository, "_read_dataset_files", counted)
    monkeypatch.setattr(repository_module, "_build_economics_index", counted_index)
    authority, _repository, _spec, _ledger, _economics, bars = _open_authority(
        tmp_path, fx=(rate,), actions=(_dividend(),)
    )
    authority.resolve_event(bars[1].session_date, EconomicPhase.PRE_OPEN)
    authority.convert(
        Decimal("1"),
        bars[2].session_date,
        EconomicPhase.PRE_OPEN,
        source_reference_digest=VALUATION_REFERENCE_DIGEST,
    )
    authority.convert(
        Decimal("2"),
        bars[2].session_date,
        EconomicPhase.PRE_OPEN,
        source_reference_digest=VALUATION_REFERENCE_DIGEST,
    )
    assert reads == 1
    assert index_builds == 1


def test_authority_rejects_unanchored_identity_and_policy_overrides(tmp_path: Path) -> None:
    _authority, repository, spec, ledger_policy, economics_policy, _bars = _open_authority(
        tmp_path
    )
    weakened_policy = economics_policy.model_copy(
        update={"fx_max_staleness_seconds": 604_800}
    )
    with pytest.raises(SimulationEconomicsError, match="policies do not match"):
        open_simulation_economics_authority(
            repository,
            DATASET_ID,
            backtest_spec=spec,
            ledger_policy=ledger_policy,
            economics_policy=weakened_policy,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=spec.digest,
            expected_economics_policy_digest=weakened_policy.digest,
        )
    with pytest.raises(SimulationEconomicsError, match="identity is not anchored"):
        open_simulation_economics_authority(
            repository,
            DATASET_ID,
            backtest_spec=spec,
            ledger_policy=ledger_policy,
            economics_policy=economics_policy,
            expected_tenant_id=OTHER_TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=spec.digest,
            expected_economics_policy_digest=economics_policy.digest,
        )
    with pytest.raises(SimulationEconomicsError, match="identity is not anchored"):
        open_simulation_economics_authority(
            repository,
            DATASET_ID,
            backtest_spec=spec,
            ledger_policy=ledger_policy,
            economics_policy=economics_policy,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest="f" * 64,
            expected_economics_policy_digest=economics_policy.digest,
        )


def test_raw_economic_helpers_are_not_public_package_authorities() -> None:
    import tradingagents.platform.backtest as backtest

    assert not hasattr(backtest, "resolve_economic_event_authority")
    assert not hasattr(backtest, "resolve_fx_rate_authority")
    assert not hasattr(backtest, "convert_quote_to_functional")
    project = Path(__file__).parents[1]
    allowed = {
        project
        / "tradingagents"
        / "platform"
        / "backtest"
        / "simulation_economics.py",
        project
        / "tradingagents"
        / "platform"
        / "backtest"
        / "simulation_economics_authority.py",
        project / "tradingagents" / "platform" / "backtest" / "repository.py",
    }
    forbidden = (
        "_convert_quote_to_functional",
        "_resolve_economic_event_projection",
        "_resolve_fx_rate_projection",
    )
    sources = tuple((project / "tradingagents").rglob("*.py")) + tuple(
        (project / "service").rglob("*.py")
    ) + tuple((project / "cli").rglob("*.py"))
    for source in sources:
        if source in allowed:
            continue
        content = source.read_text(encoding="utf-8")
        assert not any(name in content for name in forbidden), source


def test_authority_is_sealed_and_rechecks_policy_bindings(tmp_path: Path) -> None:
    authority, _repository, _spec, _ledger, economics_policy, _bars = _open_authority(
        tmp_path
    )
    weakened = economics_policy.model_copy(
        update={"fx_max_staleness_seconds": 604_800}
    )
    with pytest.raises(AttributeError, match="cannot be mutated"):
        authority._economics_policy = weakened
    object.__setattr__(authority, "_economics_policy", weakened)
    with pytest.raises(SimulationEconomicsError, match="bindings changed"):
        authority.resolve_event(date(2024, 1, 2), EconomicPhase.PRE_OPEN)


def test_economics_index_rejects_reflective_field_replacement(tmp_path: Path) -> None:
    authority, _repository, _spec, _ledger, _economics_policy, _bars = (
        _open_authority(tmp_path, actions=(_dividend(),))
    )

    with pytest.raises(AttributeError):
        object.__setattr__(
            authority._handle._index,
            "fx_chains_by_pair",
            {},
        )
    with pytest.raises(AttributeError):
        object.__setattr__(
            authority._handle._index,
            "action_chains_by_phase",
            {},
        )

    assert authority.resolve_event(
        date(2024, 1, 2), EconomicPhase.PRE_OPEN
    ).view.actions


def test_authority_rejects_sessions_outside_spec_and_untrusted_verify_inputs(
    tmp_path: Path,
) -> None:
    event_at = _bar(date(2024, 1, 3)).session_open_at
    rate = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(minutes=1),
        available_at=event_at,
        rate="0.2",
    )
    authority, _repository, _spec, _ledger, _economics, bars = _open_authority(
        tmp_path, fx=(rate,), actions=(_dividend(),)
    )
    with pytest.raises(SimulationEconomicsError, match="outside the backtest window"):
        authority.resolve_event(date(2023, 12, 31), EconomicPhase.PRE_OPEN)

    legitimate = authority.convert(
        Decimal("10"),
        bars[2].session_date,
        EconomicPhase.PRE_OPEN,
        source_reference_digest=VALUATION_REFERENCE_DIGEST,
    )
    with pytest.raises(SimulationEconomicsError, match="does not match"):
        authority.verify_conversion(
            legitimate,
            expected_source_amount=Decimal("11"),
            expected_session_date=bars[2].session_date,
            expected_phase=EconomicPhase.PRE_OPEN,
            expected_source_reference_digest=VALUATION_REFERENCE_DIGEST,
        )
    with pytest.raises(SimulationEconomicsError):
        authority.verify_conversion(
            legitimate,
            expected_source_amount=Decimal("10"),
            expected_session_date=bars[2].session_date,
            expected_phase=EconomicPhase.POST_CLOSE,
            expected_source_reference_digest=VALUATION_REFERENCE_DIGEST,
        )
    with pytest.raises(SimulationEconomicsError, match="does not match"):
        authority.verify_conversion(
            legitimate,
            expected_source_amount=Decimal("10"),
            expected_session_date=bars[2].session_date,
            expected_phase=EconomicPhase.PRE_OPEN,
            expected_source_reference_digest="d" * 64,
        )
    forged_claims = legitimate.model_dump(mode="python")
    forged_claims["quantitative_claims_status"] = "eligible"
    with pytest.raises(ValidationError):
        AuthorizedFxConversion.model_validate(forged_claims)

    event = authority.resolve_event(bars[1].session_date, EconomicPhase.PRE_OPEN)
    with pytest.raises(SimulationEconomicsError, match="does not match"):
        authority.verify_event(
            event,
            expected_session_date=bars[2].session_date,
            expected_phase=EconomicPhase.PRE_OPEN,
        )


def test_authority_rederives_forged_event_with_all_self_digests(tmp_path: Path) -> None:
    authority, _repository, _spec, _ledger, _economics, bars = _open_authority(
        tmp_path, actions=(_dividend(),)
    )
    legitimate = authority.resolve_event(
        bars[1].session_date, EconomicPhase.PRE_OPEN
    )
    action_payload = legitimate.view.actions[0].model_dump(
        mode="python", exclude={"projection_digest"}
    )
    action_payload["cash_amount"] = Decimal("999")
    forged_action = CorporateActionProjection(
        **action_payload,
        projection_digest=canonical_digest(action_payload),
    )
    view_payload = legitimate.view.model_dump(mode="python", exclude={"view_digest"})
    view_payload["actions"] = (forged_action.model_dump(mode="python"),)
    forged_view = EconomicEventView(
        **view_payload,
        view_digest=canonical_digest(view_payload),
    )
    authority_payload = legitimate.model_dump(
        mode="python", exclude={"authority_digest"}
    )
    authority_payload["view"] = forged_view.model_dump(mode="python")
    forged = AuthorizedEconomicEventView(
        **authority_payload,
        authority_digest=canonical_digest(authority_payload),
    )
    with pytest.raises(SimulationEconomicsError, match="does not match"):
        authority.verify_event(
            forged,
            expected_session_date=bars[1].session_date,
            expected_phase=EconomicPhase.PRE_OPEN,
        )


def test_authority_rejects_foreign_handle_swap_with_same_tenant_and_fx_book(
    tmp_path: Path,
) -> None:
    event_at = _bar(date(2024, 1, 3)).session_open_at
    rate = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(minutes=1),
        available_at=event_at,
        rate="0.2",
    )
    authority, repository, _spec, _ledger, _economics, bars = _open_authority(
        tmp_path, fx=(rate,)
    )
    direct = authority.convert(
        Decimal("10"),
        bars[2].session_date,
        EconomicPhase.PRE_OPEN,
        source_reference_digest=VALUATION_REFERENCE_DIGEST,
    )
    assert direct.conversion.functional_amount == Decimal("50")

    foreign_dataset_id = UUID("20000000-0000-0000-0000-000000000099")
    foreign_instrument_id = UUID("30000000-0000-0000-0000-000000000099")
    foreign_instrument = _instrument().model_copy(
        update={
            "instrument_id": foreign_instrument_id,
            "quote_currency": "BRL",
            "venue_instrument_id": "BTC/BRL",
        }
    )
    foreign_bars = tuple(
        _bar(item.session_date).model_copy(
            update={"instrument_id": foreign_instrument_id}
        )
        for item in bars
    )
    foreign_manifest = repository.ingest(
        dataset_id=foreign_dataset_id,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="foreign-handle",
        instrument=foreign_instrument,
        bars=foreign_bars,
        fx_observations=(rate,),
    )
    foreign_handle = repository.open_economics_handle(
        foreign_dataset_id,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=foreign_manifest.digest,
    )
    object.__setattr__(authority, "_handle", foreign_handle)
    with pytest.raises(SimulationEconomicsError, match="bindings changed"):
        authority.convert(
            Decimal("10"),
            bars[2].session_date,
            EconomicPhase.PRE_OPEN,
            source_reference_digest=VALUATION_REFERENCE_DIGEST,
        )
    with pytest.raises(SimulationEconomicsError, match="bindings changed"):
        authority.resolve_event(bars[2].session_date, EconomicPhase.PRE_OPEN)


def test_authority_rejects_equivalent_handle_from_another_repository(
    tmp_path: Path,
) -> None:
    authority, _repository, _spec, _ledger, _economics, bars = _open_authority(
        tmp_path / "primary"
    )
    other_repository, other_manifest, _ = _ingest(tmp_path / "other")
    other_handle = other_repository.open_economics_handle(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=other_manifest.digest,
    )
    object.__setattr__(authority, "_handle", other_handle)
    with pytest.raises(SimulationEconomicsError, match="bindings changed"):
        authority.resolve_event(bars[0].session_date, EconomicPhase.PRE_OPEN)


def test_authority_normalizes_numeric_overflow_to_domain_error(tmp_path: Path) -> None:
    event_at = _bar(date(2024, 1, 3)).session_open_at
    rate = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(minutes=1),
        available_at=event_at,
        rate="0.000000000000000001",
    )
    authority, _repository, _spec, _ledger, _economics, bars = _open_authority(
        tmp_path, fx=(rate,)
    )
    with pytest.raises(SimulationEconomicsError, match="authorized FX conversion"):
        authority.convert(
            Decimal("9" * 38),
            bars[2].session_date,
            EconomicPhase.PRE_OPEN,
            source_reference_digest=VALUATION_REFERENCE_DIGEST,
        )


def test_fx_book_digest_is_order_independent_and_content_sensitive() -> None:
    observed_at = datetime(2024, 1, 3, tzinfo=UTC)
    first = _fx(
        FX_OLD_ID,
        observed_at=observed_at,
        available_at=observed_at,
        rate="0.2",
    )
    second = _fx(
        FX_NEW_ID,
        observed_at=observed_at + timedelta(minutes=1),
        available_at=observed_at + timedelta(minutes=1),
        rate="0.21",
    )
    assert fx_authority_book_digest((first, second)) == fx_authority_book_digest(
        (second, first)
    )
    changed = second.model_copy(update={"rate": Decimal("0.22")})
    assert fx_authority_book_digest((first, second)) != fx_authority_book_digest(
        (first, changed)
    )


@pytest.mark.parametrize(
    ("second_rate_text", "same_fx_book"),
    (("0.2", True), ("0.25", False)),
)
def test_multi_instrument_authorities_require_one_common_fx_book(
    tmp_path: Path, second_rate_text: str, same_fx_book: bool
) -> None:
    event_at = _bar(date(2024, 1, 3)).session_open_at
    first_rate = _fx(
        FX_OLD_ID,
        observed_at=event_at - timedelta(minutes=1),
        available_at=event_at,
        rate="0.2",
    )
    repository, first_manifest, bars = _ingest(tmp_path, fx=(first_rate,))
    second_instrument_id = UUID("30000000-0000-0000-0000-000000000099")
    second_dataset_id = UUID("20000000-0000-0000-0000-000000000099")
    second_instrument = _instrument().model_copy(
        update={
            "instrument_id": second_instrument_id,
            "symbol": "ETH/USD",
            "venue_instrument_id": "ETH/USD",
        }
    )
    second_bars = tuple(
        bar.model_copy(update={"instrument_id": second_instrument_id}) for bar in bars
    )
    second_rate = first_rate.model_copy(update={"rate": Decimal(second_rate_text)})
    second_manifest = repository.ingest(
        dataset_id=second_dataset_id,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="second-instrument-fx-book",
        instrument=second_instrument,
        bars=second_bars,
        fx_observations=(second_rate,),
    )
    ledger_policy = SimulationLedgerPolicy(
        ledger_id=LEDGER_ID,
        opened_at=datetime(2024, 1, 1, tzinfo=UTC),
        base_currency="BRL",
        initial_cash="10000.00",
        money_quantum="0.01",
        instrument_rules=(
            InstrumentSimulationRule(
                instrument_id=INSTRUMENT_ID,
                currency="BRL",
                lot_size="0.00000001",
                price_tick="0.01",
            ),
            InstrumentSimulationRule(
                instrument_id=second_instrument_id,
                currency="BRL",
                lot_size="0.00000001",
                price_tick="0.01",
            ),
        ),
    )
    economics_policy = SimulationEconomicsPolicy(
        tenant_id=TENANT_ID,
        backtest_id=BACKTEST_ID,
        ledger_id=LEDGER_ID,
        functional_currency="BRL",
        fx_max_staleness_seconds=3_600,
        fx_book_digest=fx_authority_book_digest((first_rate,)),
        money_quantum="0.01",
        instrument_bindings=(
            InstrumentEconomicsBinding(
                instrument_id=INSTRUMENT_ID,
                dataset_id=DATASET_ID,
                manifest_digest=first_manifest.digest,
                quote_currency="USD",
            ),
            InstrumentEconomicsBinding(
                instrument_id=second_instrument_id,
                dataset_id=second_dataset_id,
                manifest_digest=second_manifest.digest,
                quote_currency="USD",
            ),
        ),
    )
    spec = BacktestSpecV3(
        backtest_id=BACKTEST_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        dataset_references=(
            DatasetReference(
                instrument_id=INSTRUMENT_ID,
                dataset_id=DATASET_ID,
                manifest_digest=first_manifest.digest,
                role=DatasetRole.TRADEABLE,
            ),
            DatasetReference(
                instrument_id=second_instrument_id,
                dataset_id=second_dataset_id,
                manifest_digest=second_manifest.digest,
                role=DatasetRole.TRADEABLE,
            ),
            DatasetReference(
                instrument_id=BENCHMARK_ID,
                dataset_id=BENCHMARK_DATASET_ID,
                manifest_digest="b" * 64,
                role=DatasetRole.BENCHMARK,
            ),
        ),
        instrument_ids=(INSTRUMENT_ID, second_instrument_id),
        benchmark_instrument_id=BENCHMARK_ID,
        start_session=bars[0].session_date,
        end_session=bars[-1].session_date,
        base_currency="BRL",
        initial_cash="10000.00",
        simulation_policy_digest=ledger_policy.digest,
        economics_policy_digest=economics_policy.digest,
    )
    first_authority = open_simulation_economics_authority(
        repository,
        DATASET_ID,
        backtest_spec=spec,
        ledger_policy=ledger_policy,
        economics_policy=economics_policy,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_economics_policy_digest=economics_policy.digest,
    )
    if not same_fx_book:
        with pytest.raises(SimulationEconomicsError, match="schema-v2 tradeable"):
            open_simulation_economics_authority(
                repository,
                second_dataset_id,
                backtest_spec=spec,
                ledger_policy=ledger_policy,
                economics_policy=economics_policy,
                expected_tenant_id=TENANT_ID,
                expected_backtest_id=BACKTEST_ID,
                expected_backtest_spec_digest=spec.digest,
                expected_economics_policy_digest=economics_policy.digest,
            )
        return

    open_simulation_economics_authority(
        repository,
        second_dataset_id,
        backtest_spec=spec,
        ledger_policy=ledger_policy,
        economics_policy=economics_policy,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_economics_policy_digest=economics_policy.digest,
    )
    second_handle = repository.open_economics_handle(
        second_dataset_id,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=second_manifest.digest,
    )
    object.__setattr__(first_authority, "_handle", second_handle)
    with pytest.raises(SimulationEconomicsError, match="bindings changed"):
        first_authority.resolve_event(bars[0].session_date, EconomicPhase.PRE_OPEN)
