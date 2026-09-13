from __future__ import annotations

import hashlib
import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

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
    canonical_json_bytes,
)
from tradingagents.platform.backtest.repository import FrozenDatasetRepository
from tradingagents.platform.backtest.simulation_economics import (
    AuthorizedCorporateCashConversion,
    CorporateActionApplication,
    CorporateCashLedgerBasis,
    EconomicPhase,
    InstrumentEconomicsBinding,
    SimulationEconomicsError,
    SimulationEconomicsPolicy,
    fx_authority_book_digest,
)
from tradingagents.platform.backtest.simulation_economics_authority import (
    FrozenSimulationEconomicsAuthority,
    open_simulation_economics_authority,
)
from tradingagents.platform.backtest.simulation_ledger import (
    InstrumentSimulationRule,
    SimulationLedgerPolicy,
)

TENANT_ID = UUID("11000000-0000-0000-0000-000000000001")
DATASET_ID = UUID("22000000-0000-0000-0000-000000000001")
INSTRUMENT_ID = UUID("33000000-0000-0000-0000-000000000001")
DIVIDEND_ID = UUID("44000000-0000-0000-0000-000000000001")
DELISTING_ID = UUID("44000000-0000-0000-0000-000000000002")
FX_ID = UUID("55000000-0000-0000-0000-000000000001")
BACKTEST_ID = UUID("66000000-0000-0000-0000-000000000001")
LEDGER_ID = UUID("77000000-0000-0000-0000-000000000001")
BENCHMARK_ID = UUID("88000000-0000-0000-0000-000000000001")
BENCHMARK_DATASET_ID = UUID("99000000-0000-0000-0000-000000000001")
RECEIPT_DIGEST = "a" * 64


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
    available_at = calendar.settlement_policy_available_at(session_date)
    return DailyBar(
        instrument_id=INSTRUMENT_ID,
        session_date=session_date,
        session_open_at=window.open_at,
        session_close_at=window.close_at,
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


def _dividend(*, currency: str = "USD") -> CorporateAction:
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
        currency=currency,
        source_digest="d" * 64,
    )


def _delisting(*, currency: str = "BRL") -> CorporateAction:
    return CorporateAction(
        action_id=DELISTING_ID,
        instrument_id=INSTRUMENT_ID,
        action_type=CorporateActionType.DELISTING,
        published_at=datetime(2024, 1, 1, 12, tzinfo=UTC),
        available_at=datetime(2024, 1, 1, 12, 1, tzinfo=UTC),
        effective_date=date(2024, 1, 2),
        pay_date=date(2024, 1, 2),
        cash_consideration="10",
        currency=currency,
        source_digest="e" * 64,
    )


def _fx(event_at: datetime, *, rate: str = "0.2") -> FxObservation:
    return FxObservation(
        observation_id=FX_ID,
        base_currency="BRL",
        quote_currency="USD",
        observed_at=event_at - timedelta(minutes=1),
        available_at=event_at,
        rate=rate,
        source_digest="f" * 64,
    )


def _open_authority(
    tmp_path: Path,
    *,
    action: CorporateAction,
    fx: tuple[FxObservation, ...],
) -> FrozenSimulationEconomicsAuthority:
    repository = FrozenDatasetRepository(tmp_path / "datasets")
    bars = tuple(_bar(date(2024, 1, day)) for day in (1, 2, 3, 4))
    instrument = _instrument()
    if action.action_type is CorporateActionType.DELISTING:
        instrument = instrument.model_copy(
            update={"delisting_date": action.effective_date}
        )
        bars = tuple(item for item in bars if item.session_date <= action.effective_date)
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=datetime(2024, 1, 10, tzinfo=UTC),
        source="test.vendor",
        source_vintage="m5.2c2-corporate-cash",
        instrument=instrument,
        bars=bars,
        corporate_actions=(action,),
        fx_observations=fx,
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
        created_at=datetime(2024, 1, 10, tzinfo=UTC),
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
    return open_simulation_economics_authority(
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


def _basis(
    authority: FrozenSimulationEconomicsAuthority,
    *,
    action_id: UUID,
    application: CorporateActionApplication,
    session_date: date,
    quantity: object = Decimal("2"),
    checkpoint_digest: str = "b" * 64,
) -> CorporateCashLedgerBasis:
    payload = {
        "basis_version": "corporate-cash-ledger-basis/1",
        "tenant_id": TENANT_ID,
        "backtest_id": BACKTEST_ID,
        "ledger_id": LEDGER_ID,
        "backtest_spec_digest": authority._spec_digest,
        "ledger_policy_digest": authority._ledger_policy.digest,
        "economics_policy_digest": authority._economics_policy.digest,
        "checkpoint_id": UUID(int=1001),
        "checkpoint_digest": checkpoint_digest,
        "checkpoint_generation": 4,
        "prior_session_receipt_digest": RECEIPT_DIGEST,
        "instrument_id": INSTRUMENT_ID,
        "action_id": action_id,
        "application": application,
        "session_date": session_date,
        "phase": EconomicPhase.POST_CLOSE,
        "quantity_basis": "entitled_quantity"
        if application is CorporateActionApplication.DIVIDEND_PAYMENT
        else "position_quantity",
        "quantity": quantity,
        "quantity_source_digest": "c" * 64,
        "ledger_receipt_id": "d" * 64,
    }
    return CorporateCashLedgerBasis(**payload, basis_digest=canonical_digest(payload))


def _verified(authority, basis):
    return authority._bind_corporate_cash_ledger_basis(
        basis,
        expected_basis_digest=basis.basis_digest,
        expected_checkpoint_digest=basis.checkpoint_digest,
        expected_prior_session_receipt_digest=basis.prior_session_receipt_digest,
    )


def test_dividend_cash_rederives_terms_and_direct_pit_fx_from_opaque_basis(tmp_path: Path) -> None:
    event_at = _bar(date(2024, 1, 3)).session_close_at
    authority = _open_authority(tmp_path, action=_dividend(), fx=(_fx(event_at),))
    basis = _basis(
        authority,
        action_id=DIVIDEND_ID,
        application=CorporateActionApplication.DIVIDEND_PAYMENT,
        session_date=date(2024, 1, 3),
    )
    verified = _verified(authority, basis)
    cash = authority._convert_corporate_cash_from_ledger_basis(verified)
    event = authority.resolve_event(date(2024, 1, 3), EconomicPhase.POST_CLOSE)

    assert cash.expected_quantity == Decimal("2")
    assert cash.unit_cash_amount == Decimal("1.25")
    assert cash.source_currency == "USD"
    assert cash.conversion.source_amount == Decimal("2.50")
    assert cash.conversion.functional_amount == Decimal("12.50")
    assert cash.ledger_basis_digest == basis.basis_digest
    assert cash.checkpoint_digest == basis.checkpoint_digest
    assert cash.event_authority_digest == event.authority_digest
    assert authority._verify_corporate_cash_conversion(cash, verified) == cash


def test_delisting_cash_uses_action_currency_and_identity_fx(tmp_path: Path) -> None:
    authority = _open_authority(tmp_path, action=_delisting(), fx=())
    basis = _basis(
        authority,
        action_id=DELISTING_ID,
        application=CorporateActionApplication.DELISTING_SETTLEMENT,
        session_date=date(2024, 1, 2),
        quantity=Decimal("3"),
    )
    cash = authority._convert_corporate_cash_from_ledger_basis(_verified(authority, basis))
    assert cash.quantity_basis == "position_quantity"
    assert cash.source_currency == "BRL"
    assert cash.projection.mode.value == "identity"
    assert cash.conversion.functional_amount == Decimal("30.00")


def test_public_scalar_corporate_cash_oracle_is_absent() -> None:
    assert not hasattr(FrozenSimulationEconomicsAuthority, "convert_corporate_cash")
    assert not hasattr(FrozenSimulationEconomicsAuthority, "verify_corporate_cash_conversion")
    parameters = inspect.signature(
        FrozenSimulationEconomicsAuthority._convert_corporate_cash_from_ledger_basis
    ).parameters
    assert tuple(parameters) == ("self", "verified_basis")


def test_calculation_ignores_ambient_decimal_context(tmp_path: Path) -> None:
    event_at = _bar(date(2024, 1, 3)).session_close_at
    authority = _open_authority(tmp_path, action=_dividend(), fx=(_fx(event_at),))
    basis = _basis(
        authority,
        action_id=DIVIDEND_ID,
        application=CorporateActionApplication.DIVIDEND_PAYMENT,
        session_date=date(2024, 1, 3),
        quantity=Decimal("123456789012345678.1234567890123456"),
    )
    verified = _verified(authority, basis)
    baseline = authority._convert_corporate_cash_from_ledger_basis(verified)
    with localcontext() as context:
        context.prec = 2
        constrained = authority._convert_corporate_cash_from_ledger_basis(verified)
    assert constrained == baseline


@pytest.mark.parametrize(
    "quantity",
    [2.0, True, Decimal("NaN"), Decimal("Infinity"), Decimal("0"), Decimal("-1")],
)
def test_ledger_basis_quantity_is_exact_bounded_and_positive(quantity: object) -> None:
    payload = {
        "basis_version": "corporate-cash-ledger-basis/1",
        "tenant_id": TENANT_ID,
        "backtest_id": BACKTEST_ID,
        "ledger_id": LEDGER_ID,
        "backtest_spec_digest": "1" * 64,
        "ledger_policy_digest": "2" * 64,
        "economics_policy_digest": "3" * 64,
        "checkpoint_id": UUID(int=1001),
        "checkpoint_digest": "4" * 64,
        "checkpoint_generation": 1,
        "prior_session_receipt_digest": "5" * 64,
        "instrument_id": INSTRUMENT_ID,
        "action_id": DIVIDEND_ID,
        "application": CorporateActionApplication.DIVIDEND_PAYMENT,
        "session_date": date(2024, 1, 3),
        "phase": EconomicPhase.POST_CLOSE,
        "quantity_basis": "entitled_quantity",
        "quantity": quantity,
        "quantity_source_digest": "6" * 64,
        "ledger_receipt_id": "7" * 64,
    }
    with pytest.raises((ValidationError, ValueError)):
        CorporateCashLedgerBasis(**payload, basis_digest=canonical_digest(payload))


def test_self_digested_or_foreign_basis_cannot_cross_external_anchor(tmp_path: Path) -> None:
    event_at = _bar(date(2024, 1, 3)).session_close_at
    authority = _open_authority(tmp_path / "one", action=_dividend(), fx=(_fx(event_at),))
    basis = _basis(
        authority,
        action_id=DIVIDEND_ID,
        application=CorporateActionApplication.DIVIDEND_PAYMENT,
        session_date=date(2024, 1, 3),
    )
    forged_payload = basis.model_dump(mode="python", exclude={"basis_digest"})
    forged_payload["quantity"] = Decimal("200")
    forged = CorporateCashLedgerBasis(
        **forged_payload, basis_digest=canonical_digest(forged_payload)
    )
    with pytest.raises(SimulationEconomicsError, match="differs"):
        authority._bind_corporate_cash_ledger_basis(
            forged,
            expected_basis_digest=basis.basis_digest,
            expected_checkpoint_digest=basis.checkpoint_digest,
            expected_prior_session_receipt_digest=basis.prior_session_receipt_digest,
        )

    other = _open_authority(tmp_path / "two", action=_dividend(), fx=(_fx(event_at),))
    with pytest.raises(SimulationEconomicsError, match="not owned"):
        other._convert_corporate_cash_from_ledger_basis(_verified(authority, basis))


def test_verified_basis_is_immutable_and_serialization_roundtrips(tmp_path: Path) -> None:
    event_at = _bar(date(2024, 1, 3)).session_close_at
    authority = _open_authority(tmp_path, action=_dividend(), fx=(_fx(event_at),))
    basis = _basis(
        authority,
        action_id=DIVIDEND_ID,
        application=CorporateActionApplication.DIVIDEND_PAYMENT,
        session_date=date(2024, 1, 3),
    )
    assert CorporateCashLedgerBasis.model_validate_json(canonical_json_bytes(basis)) == basis
    verified = _verified(authority, basis)
    with pytest.raises(AttributeError, match="immutable"):
        verified.basis = basis  # type: ignore[misc]
    object.__setattr__(verified.basis, "quantity", Decimal("200"))
    with pytest.raises(SimulationEconomicsError, match="corrupted"):
        authority._convert_corporate_cash_from_ledger_basis(verified)


@pytest.mark.parametrize("generation", [0, True, "4", 4.0])
def test_checkpoint_generation_requires_a_materialized_exact_head(
    tmp_path: Path, generation: object
) -> None:
    event_at = _bar(date(2024, 1, 3)).session_close_at
    authority = _open_authority(tmp_path, action=_dividend(), fx=(_fx(event_at),))
    basis = _basis(
        authority,
        action_id=DIVIDEND_ID,
        application=CorporateActionApplication.DIVIDEND_PAYMENT,
        session_date=date(2024, 1, 3),
    )
    payload = basis.model_dump(mode="python", exclude={"basis_digest"})
    payload["checkpoint_generation"] = generation
    with pytest.raises(ValidationError):
        CorporateCashLedgerBasis(**payload, basis_digest=basis.basis_digest)


def test_candidate_mutation_and_market_conversion_are_rejected(tmp_path: Path) -> None:
    event_at = _bar(date(2024, 1, 3)).session_close_at
    authority = _open_authority(tmp_path, action=_dividend(), fx=(_fx(event_at),))
    basis = _basis(
        authority,
        action_id=DIVIDEND_ID,
        application=CorporateActionApplication.DIVIDEND_PAYMENT,
        session_date=date(2024, 1, 3),
    )
    verified = _verified(authority, basis)
    cash = authority._convert_corporate_cash_from_ledger_basis(verified)
    payload = cash.model_dump(mode="python", exclude={"authority_digest"})
    payload["expected_quantity"] = Decimal("200")
    conversion = dict(payload["conversion"])
    conversion["source_amount"] = Decimal("250")
    conversion["functional_amount"] = Decimal("1250")
    conversion["conversion_digest"] = canonical_digest(
        {key: value for key, value in conversion.items() if key != "conversion_digest"}
    )
    payload["conversion"] = conversion
    forged = AuthorizedCorporateCashConversion(
        **payload, authority_digest=canonical_digest(payload)
    )
    with pytest.raises(SimulationEconomicsError, match="does not match"):
        authority._verify_corporate_cash_conversion(forged, verified)

    market = authority.convert(
        Decimal("2.50"),
        date(2024, 1, 3),
        EconomicPhase.POST_CLOSE,
        source_reference_digest=RECEIPT_DIGEST,
    )
    with pytest.raises(SimulationEconomicsError, match="another contract type"):
        authority._verify_corporate_cash_conversion(market, verified)  # type: ignore[arg-type]


def test_corporate_cash_contract_cannot_be_relabelled_market_valuation(tmp_path: Path) -> None:
    authority = _open_authority(tmp_path, action=_delisting(), fx=())
    basis = _basis(
        authority,
        action_id=DELISTING_ID,
        application=CorporateActionApplication.DELISTING_SETTLEMENT,
        session_date=date(2024, 1, 2),
        quantity=Decimal("1"),
    )
    cash = authority._convert_corporate_cash_from_ledger_basis(_verified(authority, basis))
    payload = cash.model_dump(mode="python", exclude={"authority_digest"})
    payload["purpose"] = "market_valuation"
    with pytest.raises(ValidationError):
        AuthorizedCorporateCashConversion(**payload, authority_digest=canonical_digest(payload))
