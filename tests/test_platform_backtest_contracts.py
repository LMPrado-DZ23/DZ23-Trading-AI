"""M5.0 contracts are immutable, point-in-time, and digest-bound."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tradingagents.platform.backtest import (
    M5_UNVERIFIED_LIMITATIONS,
    AvailabilityBasis,
    BacktestSpec,
    CorporateAction,
    DailyBar,
    DatasetLimitation,
    DatasetReference,
    DatasetRole,
    ExecutionOpen,
    FxObservation,
    InstrumentSpec,
    MarketDatasetManifest,
    canonical_digest,
    market_dataset_binding_digest,
)

NOW = datetime(2026, 9, 9, 20, tzinfo=UTC)
DIGEST = "a" * 64


def _instrument(**overrides):
    data = {
        "instrument_id": uuid4(),
        "symbol": "PETR4.SA",
        "asset_type": "stock",
        "venue": "b3",
        "venue_instrument_id": "PETR4",
        "quote_currency": "BRL",
        "market_calendar": "BVMF",
        "published_at": NOW - timedelta(days=100),
        "available_at": NOW - timedelta(days=100) + timedelta(seconds=1),
        "listing_date": date(2020, 1, 1),
        "source_digest": DIGEST,
    }
    data.update(overrides)
    return InstrumentSpec(**data)


def _bar(**overrides):
    data = {
        "instrument_id": uuid4(),
        "session_date": date(2026, 9, 8),
        "session_open_at": NOW - timedelta(hours=8),
        "session_close_at": NOW - timedelta(hours=1),
        "fetched_at": NOW - timedelta(minutes=30),
        "available_at": NOW - timedelta(minutes=45),
        "availability_basis": AvailabilityBasis.EXCHANGE_SETTLEMENT_POLICY,
        "open": Decimal("30.00"),
        "high": Decimal("32.00"),
        "low": Decimal("29.00"),
        "close": Decimal("31.00"),
        "volume": Decimal("1000000"),
        "source_digest": DIGEST,
    }
    data.update(overrides)
    return DailyBar(**data)


def _manifest(**overrides):
    dataset_id = overrides.pop("dataset_id", uuid4())
    tenant_id = overrides.pop("tenant_id", uuid4())
    instrument_digest = overrides.pop("instrument_digest", "b" * 64)
    content_sha256 = overrides.pop("content_sha256", "c" * 64)
    content_bytes = overrides.pop("content_bytes", 12_345)
    schedule_digest = overrides.pop("schedule_digest", "d" * 64)
    source_vintage = overrides.pop("source_vintage", "vendor-snapshot-2026-09-09")
    calendar_engine_version = overrides.pop(
        "calendar_engine_version", "exchange-calendars/4.13.2"
    )
    timezone_database_version = overrides.pop("timezone_database_version", "tzdata/2025.2")
    settlement_policy_version = overrides.pop(
        "settlement_policy_version", "daily-bar-settlement/1"
    )
    data = {
        "dataset_id": dataset_id,
        "tenant_id": tenant_id,
        "schema_version": 2,
        "created_at": NOW,
        "source": "dz23-test",
        "source_vintage": source_vintage,
        "instrument_digest": instrument_digest,
        "content_bytes": content_bytes,
        "content_sha256": content_sha256,
        "row_count": 20,
        "first_session": date(2026, 8, 1),
        "last_session": date(2026, 9, 8),
        "schedule_digest": schedule_digest,
        "calendar_engine_version": calendar_engine_version,
        "timezone_database_version": timezone_database_version,
        "settlement_policy_version": settlement_policy_version,
        "binding_digest": market_dataset_binding_digest(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            instrument_digest=instrument_digest,
            content_bytes=content_bytes,
            content_sha256=content_sha256,
            schedule_digest=schedule_digest,
            source_vintage=source_vintage,
            calendar_engine_version=calendar_engine_version,
            timezone_database_version=timezone_database_version,
            settlement_policy_version=settlement_policy_version,
            schema_version=2,
        ),
        "limitations": M5_UNVERIFIED_LIMITATIONS,
    }
    data.update(overrides)
    return MarketDatasetManifest(**data)


def _spec(**overrides):
    instrument_ids = overrides.pop("instrument_ids", (uuid4(),))
    benchmark_id = overrides.pop("benchmark_instrument_id", uuid4())
    dataset_references = overrides.pop(
        "dataset_references",
        tuple(
            DatasetReference(
                instrument_id=instrument_id,
                dataset_id=uuid4(),
                manifest_digest="e" * 64,
                role="tradeable",
            )
            for instrument_id in instrument_ids
        )
        + (
            DatasetReference(
                instrument_id=benchmark_id,
                dataset_id=uuid4(),
                manifest_digest="f" * 64,
                role="benchmark",
            ),
        ),
    )
    data = {
        "backtest_id": uuid4(),
        "tenant_id": uuid4(),
        "created_at": NOW,
        "dataset_references": dataset_references,
        "instrument_ids": instrument_ids,
        "benchmark_instrument_id": benchmark_id,
        "start_session": date(2025, 1, 1),
        "end_session": date(2025, 12, 31),
        "base_currency": "BRL",
        "initial_cash": Decimal("10000.00"),
        "simulation_policy_digest": "a" * 64,
    }
    data.update(overrides)
    return BacktestSpec(**data)


def test_contracts_are_frozen_and_reject_extra_fields():
    bar = _bar()
    with pytest.raises(ValidationError):
        bar.close = Decimal("1")
    with pytest.raises(ValidationError):
        _bar(adjusted_close=Decimal("31"))


@pytest.mark.parametrize(
    "field",
    ["session_open_at", "session_close_at", "available_at"],
)
def test_bar_requires_utc_timestamps(field):
    with pytest.raises(ValidationError, match="timezone"):
        _bar(**{field: NOW.replace(tzinfo=None)})
    with pytest.raises(ValidationError, match="UTC"):
        _bar(**{field: NOW.astimezone(timezone(timedelta(hours=-3)))})


def test_instrument_is_point_in_time_and_calendar_typed():
    with pytest.raises(ValidationError, match="availability"):
        _instrument(available_at=NOW - timedelta(days=101))
    with pytest.raises(ValidationError, match="24/7"):
        _instrument(asset_type="crypto")
    with pytest.raises(ValidationError, match="validity"):
        _instrument(delisting_date=date(2019, 12, 31))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", Decimal("0")),
        ("high", Decimal("Infinity")),
        ("low", Decimal("-1")),
        ("close", Decimal("NaN")),
        ("volume", Decimal("-1")),
    ],
)
def test_bar_rejects_invalid_ohlcv(field, value):
    with pytest.raises(ValidationError):
        _bar(**{field: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {"low": Decimal("33")},
        {"open": Decimal("33")},
        {"close": Decimal("28")},
        {"session_close_at": NOW - timedelta(hours=9)},
        {"available_at": NOW - timedelta(hours=1)},
    ],
)
def test_bar_rejects_inconsistent_prices_and_ordering(overrides):
    with pytest.raises(ValidationError):
        _bar(**overrides)


def test_bar_is_raw_only_and_rejects_binary_float_input():
    with pytest.raises(ValidationError):
        _bar(price_basis="adjusted")
    with pytest.raises(ValidationError, match="binary"):
        _bar(close=31.0)


def test_split_and_dividend_are_typed_and_point_in_time():
    common = {
        "action_id": uuid4(),
        "instrument_id": uuid4(),
        "published_at": NOW - timedelta(days=10),
        "available_at": NOW - timedelta(days=10) + timedelta(minutes=1),
        "effective_date": date(2026, 9, 8),
        "source_digest": DIGEST,
    }
    split = CorporateAction(
        **common,
        action_type="split",
        ex_date=date(2026, 9, 8),
        split_ratio=Decimal("2"),
        split_ratio_convention="new_shares_per_old_share",
    )
    dividend = CorporateAction(
        **(
            common
            | {
                "action_id": uuid4(),
                "action_type": "cash_dividend",
                "ex_date": date(2026, 9, 8),
                "pay_date": date(2026, 9, 9),
                "cash_amount": Decimal("1.25"),
                "cash_amount_basis": "net_cash_per_entitled_share",
                "currency": "BRL",
            }
        )
    )
    assert split.split_ratio == Decimal("2")
    assert dividend.cash_amount == Decimal("1.25")
    with pytest.raises(ValidationError, match="only a split ratio"):
        CorporateAction(
            **(
                common
                | {
                    "action_id": uuid4(),
                    "action_type": "split",
                    "ex_date": date(2026, 9, 8),
                    "split_ratio": Decimal("2"),
                    "cash_amount": Decimal("1"),
                    "currency": "BRL",
                }
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"available_at": NOW - timedelta(days=11)},
        {"pay_date": date(2026, 8, 1)},
    ],
)
def test_corporate_action_rejects_impossible_date_order(overrides):
    data = {
        "action_id": uuid4(),
        "instrument_id": uuid4(),
        "action_type": "split",
        "published_at": NOW - timedelta(days=10),
        "available_at": NOW - timedelta(days=10) + timedelta(minutes=1),
        "effective_date": date(2026, 9, 8),
        "ex_date": date(2026, 9, 8),
        "split_ratio": Decimal("2"),
        "split_ratio_convention": "new_shares_per_old_share",
        "source_digest": DIGEST,
    }
    data.update(overrides)
    with pytest.raises(ValidationError):
        CorporateAction(**data)


def test_corporate_action_supports_post_effective_revision_and_delisting():
    corrected = CorporateAction(
        action_id=uuid4(),
        instrument_id=uuid4(),
        revision=2,
        action_type="cash_dividend",
        announced_at=NOW - timedelta(days=30),
        published_at=NOW,
        available_at=NOW + timedelta(minutes=1),
        ex_date=date(2026, 8, 1),
        effective_date=date(2026, 8, 1),
        pay_date=date(2026, 8, 10),
        cash_amount=Decimal("1.05"),
        cash_amount_basis="net_cash_per_entitled_share",
        currency="BRL",
        source_digest=DIGEST,
    )
    delisting = CorporateAction(
        action_id=uuid4(),
        instrument_id=uuid4(),
        action_type="delisting",
        published_at=NOW - timedelta(days=1),
        available_at=NOW,
        effective_date=date(2026, 9, 9),
        pay_date=date(2026, 9, 12),
        cash_consideration=Decimal("12.50"),
        currency="BRL",
        source_digest=DIGEST,
    )
    assert corrected.revision == 2
    assert delisting.cash_consideration == Decimal("12.50")


@pytest.mark.parametrize(
    "value",
    [
        "1" * 81,
        Decimal("1e38"),
        Decimal("0.0000000000000000001"),
        2**257,
    ],
)
def test_decimal_inputs_have_resource_precision_and_scale_bounds(value):
    with pytest.raises(ValidationError, match="resource|digits"):
        _bar(close=value)


def test_fx_observation_is_positive_available_and_non_identity():
    data = {
        "observation_id": uuid4(),
        "base_currency": "USD",
        "quote_currency": "BRL",
        "observed_at": NOW - timedelta(minutes=2),
        "available_at": NOW - timedelta(minutes=1),
        "rate": Decimal("5.25"),
        "source_digest": DIGEST,
    }
    observation = FxObservation(**data)
    assert observation.rate == Decimal("5.25")
    with pytest.raises(ValidationError):
        FxObservation(**(data | {"quote_currency": "USD"}))
    with pytest.raises(ValidationError):
        FxObservation(**(data | {"available_at": NOW - timedelta(minutes=3)}))


def test_manifest_validates_digests_counts_dates_and_limitations():
    manifest = _manifest()
    assert len(manifest.digest) == 64
    assert manifest.quantitative_claims_eligible is False
    with pytest.raises(ValidationError):
        _manifest(content_sha256="not-a-digest")
    with pytest.raises(ValidationError):
        _manifest(row_count=0)
    with pytest.raises(ValidationError):
        _manifest(last_session=date(2026, 7, 31))
    with pytest.raises(ValidationError):
        _manifest(
            limitations=(
                DatasetLimitation.SURVIVORSHIP_UNVERIFIED,
                DatasetLimitation.SURVIVORSHIP_UNVERIFIED,
            )
        )
    with pytest.raises(ValidationError, match="binding"):
        _manifest(binding_digest="f" * 64)


def test_manifest_schema_omission_preserves_legacy_v1_constructor() -> None:
    current = _manifest()
    legacy = current.model_dump(mode="python")
    legacy.pop("schema_version")
    legacy["binding_digest"] = market_dataset_binding_digest(
        tenant_id=current.tenant_id,
        dataset_id=current.dataset_id,
        instrument_digest=current.instrument_digest,
        content_bytes=current.content_bytes,
        content_sha256=current.content_sha256,
        schedule_digest=current.schedule_digest,
        source_vintage=current.source_vintage,
        calendar_engine_version=current.calendar_engine_version,
        timezone_database_version=current.timezone_database_version,
        settlement_policy_version=current.settlement_policy_version,
    )

    restored = MarketDatasetManifest(**legacy)

    assert restored.schema_version == 1
    assert restored.binding_digest == legacy["binding_digest"]


def test_digest_is_decimal_canonical_and_materially_bound():
    first = _bar(open=Decimal("30.0"), close=Decimal("31.00"))
    same = _bar(
        instrument_id=first.instrument_id,
        open=Decimal("30.000"),
        close=Decimal("31.0000"),
    )
    changed = _bar(
        instrument_id=first.instrument_id,
        open=Decimal("30.000"),
        close=Decimal("31.01"),
    )
    assert first.digest == same.digest
    assert first.digest != changed.digest
    assert first.model_dump(mode="json")["close"] == "31.00"


@pytest.mark.parametrize("payload", [{"x": 0.1}, {"x": True}, {1: "non-string-key"}])
def test_canonical_digest_rejects_ambiguous_python_scalars(payload):
    with pytest.raises(ValueError, match="canonical"):
        canonical_digest(payload)


def test_backtest_spec_binds_honest_execution_assumptions():
    spec = _spec()
    assert spec.fill_policy == "first_session_raw_open_after_decision"
    assert spec.price_basis == "raw"
    assert spec.missing_data_policy == "fail_closed"
    assert spec.digest != _spec(
        backtest_id=spec.backtest_id,
        tenant_id=spec.tenant_id,
        instrument_ids=spec.instrument_ids,
        benchmark_instrument_id=spec.benchmark_instrument_id,
        commission_bps=Decimal("1"),
    ).digest


@pytest.mark.parametrize(
    "overrides",
    [
        {"initial_cash": Decimal("0")},
        {"initial_cash": 10000.0},
        {"commission_bps": Decimal("1001")},
        {"slippage_bps": Decimal("NaN")},
        {"max_volume_participation": Decimal("0")},
        {"max_volume_participation": Decimal("1.1")},
        {"end_session": date(2024, 12, 31)},
        {"instrument_ids": (uuid4(),) * 2},
        {"fill_policy": "same_close"},
        {"price_basis": "adjusted"},
        {"missing_data_policy": "forward_fill"},
    ],
)
def test_backtest_spec_rejects_unsafe_or_ambiguous_inputs(overrides):
    with pytest.raises(ValidationError):
        _spec(**overrides)


def test_decimal_canonicalization_ignores_ambient_context_and_traps():
    first = _bar(open=Decimal("30.45"), close=Decimal("31.00"))
    second = _bar(
        instrument_id=first.instrument_id,
        open=Decimal("30.46"),
        close=Decimal("31.00"),
    )
    baseline = first.digest

    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert first.digest == baseline
        assert second.digest != baseline


def test_bar_availability_distinguishes_vendor_publication_policy_and_unknown():
    vendor = _bar(
        availability_basis=AvailabilityBasis.SOURCE_PUBLISHED,
        source_published_at=NOW - timedelta(minutes=44),
        available_at=NOW - timedelta(minutes=43),
    )
    unknown = _bar(
        availability_basis=AvailabilityBasis.UNKNOWN,
        source_published_at=None,
        available_at=None,
    )

    assert vendor.available_at > vendor.source_published_at
    assert unknown.available_at is None
    with pytest.raises(ValidationError, match="unknown"):
        _bar(availability_basis=AvailabilityBasis.UNKNOWN)
    with pytest.raises(ValidationError, match="publication"):
        _bar(
            availability_basis=AvailabilityBasis.SOURCE_PUBLISHED,
            source_published_at=None,
        )


def test_execution_open_is_a_minimal_raw_projection():
    execution = ExecutionOpen(
        dataset_id=uuid4(),
        manifest_digest="c" * 64,
        instrument_id=uuid4(),
        quote_currency="USD",
        decision_session_date=date(2026, 9, 8),
        decision_at=NOW - timedelta(minutes=1),
        eligible_view_digest="d" * 64,
        session_date=date(2026, 9, 9),
        execution_at=NOW,
        raw_open=Decimal("30.25"),
        decision_volume=Decimal("1000000"),
        parent_bar_digest="b" * 64,
        source_digest=DIGEST,
        decision_bar_digest="e" * 64,
        decision_bar_source_digest="f" * 64,
    )

    assert set(execution.model_dump()) == {
        "instrument_id",
        "quote_currency",
        "dataset_id",
        "manifest_digest",
        "decision_session_date",
        "decision_at",
        "eligible_view_digest",
        "session_date",
        "execution_at",
        "raw_open",
        "decision_volume",
        "parent_bar_digest",
        "source_digest",
        "decision_bar_digest",
        "decision_bar_source_digest",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_at": NOW - timedelta(minutes=1)},
        {"execution_at": NOW - timedelta(minutes=2)},
        {"session_date": date(2026, 9, 8)},
        {"session_date": date(2026, 9, 7)},
    ],
)
def test_execution_open_rejects_equal_or_predecision_fill(overrides):
    data = {
        "dataset_id": uuid4(),
            "manifest_digest": "c" * 64,
            "instrument_id": uuid4(),
            "quote_currency": "USD",
        "decision_session_date": date(2026, 9, 8),
        "decision_at": NOW - timedelta(minutes=1),
        "eligible_view_digest": "d" * 64,
        "session_date": date(2026, 9, 9),
        "execution_at": NOW,
            "raw_open": Decimal("30.25"),
            "decision_volume": Decimal("1000000"),
            "parent_bar_digest": "b" * 64,
            "source_digest": DIGEST,
            "decision_bar_digest": "e" * 64,
            "decision_bar_source_digest": "f" * 64,
    }
    data.update(overrides)
    with pytest.raises(ValidationError, match="strictly after|follow"):
        ExecutionOpen(**data)


def test_backtest_spec_binds_every_instrument_and_unique_dataset():
    spec = _spec()
    shared = {
        "instrument_ids": spec.instrument_ids,
        "benchmark_instrument_id": spec.benchmark_instrument_id,
    }
    with pytest.raises(ValidationError, match="tradeable|benchmark"):
        _spec(dataset_references=spec.dataset_references[:-1], **shared)

    duplicated_dataset = spec.dataset_references[1].model_copy(
        update={"dataset_id": spec.dataset_references[0].dataset_id}
    )
    with pytest.raises(ValidationError, match="dataset may bind"):
        _spec(dataset_references=(spec.dataset_references[0], duplicated_dataset), **shared)

    wrong_benchmark = spec.dataset_references[1].model_copy(
        update={"role": DatasetRole.TRADEABLE}
    )
    with pytest.raises(ValidationError, match="tradeable|benchmark"):
        _spec(dataset_references=(spec.dataset_references[0], wrong_benchmark), **shared)

    with pytest.raises(ValidationError, match="distinct"):
        _spec(
            instrument_ids=spec.instrument_ids,
            benchmark_instrument_id=spec.instrument_ids[0],
        )

    first_id, second_id = uuid4(), uuid4()
    ordered = _spec(instrument_ids=(first_id, second_id))
    with pytest.raises(ValidationError, match="instrument order"):
        _spec(
            instrument_ids=ordered.instrument_ids,
            benchmark_instrument_id=ordered.benchmark_instrument_id,
            dataset_references=(
                ordered.dataset_references[1],
                ordered.dataset_references[0],
                ordered.dataset_references[2],
            ),
        )


def test_manifest_requires_positive_bounded_content_length_and_limitations():
    with pytest.raises(ValidationError):
        _manifest(content_bytes=0)
    with pytest.raises(ValidationError):
        _manifest(content_bytes=(16 * 1024 * 1024) + 1)
    with pytest.raises(ValidationError):
        _manifest(limitations=())
    with pytest.raises(ValidationError, match="canonical"):
        _manifest(limitations=tuple(reversed(M5_UNVERIFIED_LIMITATIONS)))


def test_backtest_package_exports_the_supported_authority_api():
    import tradingagents.platform.backtest as backtest

    expected = {
        "BacktestDataCode",
        "BacktestDataError",
        "DatasetAlreadyExistsError",
        "DatasetIntegrityError",
        "EligibleMarketView",
        "ExecutionOpen",
        "FrozenDatasetRepository",
        "MarketCalendarError",
    }
    assert expected <= set(backtest.__all__)
    assert all(hasattr(backtest, name) for name in expected)
