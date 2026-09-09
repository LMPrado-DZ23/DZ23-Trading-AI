"""M5.0 contracts are immutable, point-in-time, and digest-bound."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tradingagents.platform.backtest import (
    BacktestSpec,
    CorporateAction,
    DailyBar,
    DatasetLimitation,
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
        "available_at": NOW - timedelta(minutes=45),
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
        "created_at": NOW,
        "source": "dz23-test",
        "source_vintage": source_vintage,
        "instrument_digest": instrument_digest,
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
            content_sha256=content_sha256,
            schedule_digest=schedule_digest,
            source_vintage=source_vintage,
            calendar_engine_version=calendar_engine_version,
            timezone_database_version=timezone_database_version,
            settlement_policy_version=settlement_policy_version,
        ),
        "limitations": (DatasetLimitation.SURVIVORSHIP_UNVERIFIED,),
    }
    data.update(overrides)
    return MarketDatasetManifest(**data)


def _spec(**overrides):
    instrument_id = uuid4()
    data = {
        "backtest_id": uuid4(),
        "tenant_id": uuid4(),
        "created_at": NOW,
        "dataset_manifest_digest": "e" * 64,
        "instrument_ids": (instrument_id,),
        "benchmark_instrument_id": uuid4(),
        "start_session": date(2025, 1, 1),
        "end_session": date(2025, 12, 31),
        "base_currency": "BRL",
        "initial_cash": Decimal("10000.00"),
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
        "pay_date": date(2026, 9, 9),
        "split_ratio": Decimal("2"),
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
    assert spec.fill_policy == "next_session_raw_open"
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
