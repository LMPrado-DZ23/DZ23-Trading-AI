from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from tradingagents.platform.backtest.calendar import BacktestCalendar, schedule_digest
from tradingagents.platform.backtest.contracts import (
    AssetType,
    CorporateAction,
    CorporateActionType,
    DailyBar,
    DatasetLimitation,
    FxObservation,
    InstrumentSpec,
    MarketCalendar,
)
from tradingagents.platform.backtest.errors import (
    BacktestDataCode,
    DatasetAlreadyExistsError,
    DatasetIntegrityError,
    MarketCalendarError,
)
from tradingagents.platform.backtest.repository import (
    DATA_FILENAME,
    MANIFEST_FILENAME,
    MANIFEST_SEAL_FILENAME,
    FrozenDatasetRepository,
    _record_bytes,
    _strict_json_loads,
)

INSTRUMENT_ID = UUID("10000000-0000-0000-0000-000000000001")
DATASET_ID = UUID("20000000-0000-0000-0000-000000000001")
TENANT_ID = UUID("30000000-0000-0000-0000-000000000001")
CREATED_AT = datetime(2024, 1, 10, 12, tzinfo=UTC)
DIGEST = "a" * 64


def _instrument(
    *, market_calendar: MarketCalendar = MarketCalendar.NEW_YORK
) -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=INSTRUMENT_ID,
        symbol="AAPL" if market_calendar is not MarketCalendar.CONTINUOUS else "BTC/USD",
        asset_type=(
            AssetType.CRYPTO
            if market_calendar is MarketCalendar.CONTINUOUS
            else AssetType.STOCK
        ),
        venue="coinbase" if market_calendar is MarketCalendar.CONTINUOUS else "nasdaq",
        venue_instrument_id="BTC-USD" if market_calendar is MarketCalendar.CONTINUOUS else "AAPL",
        base_currency="BTC" if market_calendar is MarketCalendar.CONTINUOUS else None,
        quote_currency="USD",
        market_calendar=market_calendar,
        published_at=datetime(2020, 1, 1, tzinfo=UTC),
        available_at=datetime(2020, 1, 1, tzinfo=UTC),
        listing_date=date(2020, 1, 1),
        source_digest=DIGEST,
    )


def _bar(
    session_date: date,
    *,
    instrument: InstrumentSpec | None = None,
    available_lag: timedelta = timedelta(minutes=15),
) -> DailyBar:
    resolved = instrument or _instrument()
    window = BacktestCalendar(resolved.market_calendar).session_window(session_date)
    return DailyBar(
        instrument_id=resolved.instrument_id,
        session_date=session_date,
        session_open_at=window.open_at,
        session_close_at=window.close_at,
        available_at=window.close_at + available_lag,
        open="100",
        high="110",
        low="90",
        close="105",
        volume="1000000",
        source_digest=hashlib.sha256(session_date.isoformat().encode()).hexdigest(),
    )


def _records() -> tuple[
    InstrumentSpec,
    tuple[DailyBar, ...],
    tuple[CorporateAction, ...],
    tuple[FxObservation, ...],
]:
    instrument = _instrument()
    bars = (_bar(date(2024, 1, 2)), _bar(date(2024, 1, 3)))
    actions = (
        CorporateAction(
            action_id=UUID("40000000-0000-0000-0000-000000000001"),
            instrument_id=INSTRUMENT_ID,
            action_type=CorporateActionType.SPLIT,
            announced_at=datetime(2024, 1, 2, 11, tzinfo=UTC),
            published_at=datetime(2024, 1, 2, 12, tzinfo=UTC),
            available_at=datetime(2024, 1, 2, 12, 1, tzinfo=UTC),
            ex_date=date(2024, 1, 3),
            effective_date=date(2024, 1, 3),
            pay_date=date(2024, 1, 3),
            split_ratio="2",
            source_digest="b" * 64,
        ),
    )
    fx = (
        FxObservation(
            base_currency="USD",
            quote_currency="BRL",
            observed_at=datetime(2024, 1, 3, 21, tzinfo=UTC),
            available_at=datetime(2024, 1, 3, 21, 1, tzinfo=UTC),
            rate="4.90",
            source_digest="c" * 64,
        ),
    )
    return instrument, bars, actions, fx


def _ingest(repository: FrozenDatasetRepository):
    instrument, bars, actions, fx = _records()
    return repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="fixture-2024-01-10",
        instrument=instrument,
        bars=bars,
        corporate_actions=actions,
        fx_observations=fx,
        limitations=(DatasetLimitation.SURVIVORSHIP_UNVERIFIED,),
    )


def test_ingest_is_atomic_canonical_immutable_and_round_trips(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)
    dataset_dir = tmp_path / str(DATASET_ID)
    content = (dataset_dir / DATA_FILENAME).read_bytes()
    manifest_bytes = (dataset_dir / MANIFEST_FILENAME).read_bytes()

    assert hashlib.sha256(content).hexdigest() == manifest.content_sha256
    assert hashlib.sha256(manifest_bytes).hexdigest().encode() + b"\n" == (
        dataset_dir / MANIFEST_SEAL_FILENAME
    ).read_bytes()
    assert not ((dataset_dir / DATA_FILENAME).stat().st_mode & stat.S_IWUSR)
    assert list(tmp_path.glob(f".{DATASET_ID}.*")) == [tmp_path / f".{DATASET_ID}.claim"]

    loaded = repository.load(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
    )
    instrument, bars, actions, fx = _records()
    assert loaded.instrument == instrument
    assert loaded.bars == bars
    assert loaded.corporate_actions == actions
    assert loaded.fx_observations == fx
    assert loaded.manifest == manifest
    assert manifest.schedule_digest == schedule_digest(instrument, bars)

    with pytest.raises(DatasetAlreadyExistsError):
        _ingest(repository)


def test_replay_reads_only_frozen_bytes_not_calendar_vendor_or_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    def forbidden(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("replay attempted a non-frozen lookup")

    monkeypatch.setattr("tradingagents.platform.backtest.repository.BacktestCalendar", forbidden)
    loaded = repository.load(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
    )
    assert loaded.manifest == manifest


@pytest.mark.parametrize("tampered_name", [DATA_FILENAME, MANIFEST_FILENAME])
def test_load_rejects_tampered_frozen_bytes(tmp_path: Path, tampered_name: str) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)
    target = tmp_path / str(DATASET_ID) / tampered_name
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(DatasetIntegrityError):
        repository.load(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest.digest,
        )


def test_load_requires_external_manifest_binding(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    with pytest.raises(DatasetIntegrityError, match="external trusted binding"):
        repository.load(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest="f" * 64,
        )
    assert repository.load(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
    ).manifest == manifest


def test_load_rejects_cross_tenant_even_with_known_manifest_digest(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    with pytest.raises(DatasetIntegrityError) as caught:
        repository.load(
            DATASET_ID,
            expected_tenant_id=UUID("30000000-0000-0000-0000-000000000099"),
            expected_manifest_digest=manifest.digest,
        )
    assert caught.value.code is BacktestDataCode.TENANT_SCOPE_MISMATCH


@pytest.mark.parametrize(
    "payload,error",
    [
        (b'{"kind":"instrument","kind":"daily_bar"}', "duplicate object keys"),
        (b'{"value":NaN}', "non-finite number"),
        (b"[" * 65 + b"]" * 65, "nesting limit"),
    ],
)
def test_json_parser_rejects_ambiguous_or_resource_hostile_input(
    payload: bytes, error: str
) -> None:
    with pytest.raises(DatasetIntegrityError, match=error):
        _strict_json_loads(payload)


@pytest.mark.skipif(os.name != "posix", reason="POSIX dirfd hardening is the production path")
def test_load_rejects_symlinked_dataset_component(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path / "repository")
    manifest = _ingest(repository)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n")
    target = repository.root / str(DATASET_ID) / DATA_FILENAME
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(DatasetIntegrityError, match="opened safely"):
        repository.load(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest.digest,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX dirfd hardening is the production path")
def test_load_rejects_symlinked_dataset_directory(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path / "repository")
    manifest = _ingest(repository)
    dataset_dir = repository.root / str(DATASET_ID)
    moved = tmp_path / "moved-dataset"
    dataset_dir.rename(moved)
    dataset_dir.symlink_to(moved, target_is_directory=True)

    with pytest.raises(DatasetIntegrityError, match="opened safely"):
        repository.load(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest.digest,
        )


def test_eligible_view_has_separate_digest_and_excludes_future_records(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    view = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=datetime(2024, 1, 2, 21, 15, tzinfo=UTC),
    )

    assert view.decision_session_date == date(2024, 1, 2)
    assert [bar.session_date for bar in view.bars] == [date(2024, 1, 2)]
    assert len(view.corporate_actions) == 1
    assert view.fx_observations == ()
    assert view.full_content_sha256 == manifest.content_sha256
    assert view.eligible_view_sha256 != view.full_content_sha256
    assert repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=datetime(2024, 1, 2, 21, 15, tzinfo=UTC),
    ).eligible_view_sha256 == view.eligible_view_sha256


def test_eligible_view_requires_exact_frozen_session_cutoff(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    with pytest.raises(DatasetIntegrityError) as caught:
        repository.load_eligible(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest.digest,
            decision_at=datetime(2024, 1, 2, 21, 15, 0, 1, tzinfo=UTC),
        )
    assert caught.value.code is BacktestDataCode.PIT_CUTOFF_MISMATCH


def test_future_append_changes_full_digest_but_not_eligible_view(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument, bars, actions, fx = _records()
    first_id = UUID("20000000-0000-0000-0000-000000000011")
    second_id = UUID("20000000-0000-0000-0000-000000000012")
    first = repository.ingest(
        dataset_id=first_id,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="same-vintage",
        instrument=instrument,
        bars=bars,
        corporate_actions=actions,
        fx_observations=fx,
        limitations=(DatasetLimitation.SURVIVORSHIP_UNVERIFIED,),
    )
    second = repository.ingest(
        dataset_id=second_id,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="same-vintage",
        instrument=instrument,
        bars=bars + (_bar(date(2024, 1, 4)),),
        corporate_actions=actions,
        fx_observations=fx,
        limitations=(DatasetLimitation.SURVIVORSHIP_UNVERIFIED,),
    )
    cutoff = datetime(2024, 1, 2, 21, 15, tzinfo=UTC)
    first_view = repository.load_eligible(
        first_id,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=first.digest,
        decision_at=cutoff,
    )
    second_view = repository.load_eligible(
        second_id,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=second.digest,
        decision_at=cutoff,
    )

    assert first.content_sha256 != second.content_sha256
    assert first.digest != second.digest
    assert first_view.eligible_view_sha256 == second_view.eligible_view_sha256


def test_ingest_rejects_manifest_created_before_latest_record(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument, bars, actions, fx = _records()

    with pytest.raises(DatasetIntegrityError, match="before its latest"):
        repository.ingest(
            dataset_id=DATASET_ID,
            tenant_id=TENANT_ID,
            created_at=datetime(2024, 1, 3, 21, tzinfo=UTC),
            source="test.vendor",
            source_vintage="fixture",
            instrument=instrument,
            bars=bars,
            corporate_actions=actions,
            fx_observations=fx,
        )


def test_later_corporate_action_revision_does_not_leak_into_prior_cutoff(
    tmp_path: Path,
) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument, bars, actions, fx = _records()
    original = actions[0]
    correction = original.model_copy(
        update={
            "revision": 2,
            "published_at": datetime(2024, 1, 4, 12, tzinfo=UTC),
            "available_at": datetime(2024, 1, 4, 12, 1, tzinfo=UTC),
            "split_ratio": Decimal("3"),
            "source_digest": "e" * 64,
        }
    )
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="corrected-vintage",
        instrument=instrument,
        bars=bars + (_bar(date(2024, 1, 4)),),
        corporate_actions=(original, correction),
        fx_observations=fx,
        limitations=(DatasetLimitation.CORPORATE_ACTION_VINTAGE_UNVERIFIED,),
    )

    prior = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=datetime(2024, 1, 3, 21, 15, tzinfo=UTC),
    )
    later = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=datetime(2024, 1, 4, 21, 15, tzinfo=UTC),
    )

    assert prior.corporate_actions == (original,)
    assert later.corporate_actions == (correction,)
    assert prior.corporate_actions[0].split_ratio == Decimal("2")
    assert later.corporate_actions[0].split_ratio == Decimal("3")


@pytest.mark.parametrize(
    "bars,error",
    [
        (
            (_bar(date(2024, 1, 2)), _bar(date(2024, 1, 2))),
            "duplicate daily bars",
        ),
        (
            (_bar(date(2024, 1, 3)), _bar(date(2024, 1, 2))),
            "not strictly chronological",
        ),
    ],
)
def test_ingest_rejects_duplicate_and_nonmonotonic_bars(
    tmp_path: Path, bars: tuple[DailyBar, ...], error: str
) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    with pytest.raises(DatasetIntegrityError, match=error):
        repository.ingest(
            dataset_id=DATASET_ID,
            tenant_id=TENANT_ID,
            created_at=CREATED_AT,
            source="test.vendor",
            source_vintage="fixture",
            instrument=_instrument(),
            bars=bars,
        )
    assert not (tmp_path / str(DATASET_ID)).exists()


def test_ingest_rejects_bar_that_disagrees_with_exchange_schedule(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    original = _bar(date(2024, 1, 2))
    shifted = original.model_copy(
        update={"session_open_at": original.session_open_at + timedelta(minutes=1)}
    )
    with pytest.raises(MarketCalendarError, match="do not match"):
        repository.ingest(
            dataset_id=DATASET_ID,
            tenant_id=TENANT_ID,
            created_at=CREATED_AT,
            source="test.vendor",
            source_vintage="fixture",
            instrument=_instrument(),
            bars=(shifted,),
        )


def test_xnys_and_bvmf_calendars_respect_sessions_and_holidays() -> None:
    xnys = BacktestCalendar("XNYS")
    assert xnys.session_window(date(2024, 7, 3)).close_at == datetime(
        2024, 7, 3, 17, tzinfo=UTC
    )
    with pytest.raises(MarketCalendarError):
        xnys.session_window(date(2024, 7, 4))
    assert xnys.next_session_open(date(2024, 7, 3)) == datetime(
        2024, 7, 5, 13, 30, tzinfo=UTC
    )

    bvmf = BacktestCalendar("BVMF")
    bvmf_window = bvmf.session_window(date(2024, 1, 2))
    assert bvmf_window.open_at == datetime(2024, 1, 2, 13, tzinfo=UTC)
    assert bvmf_window.close_at == datetime(2024, 1, 2, 21, tzinfo=UTC)


def test_continuous_market_fill_occurs_after_bar_availability() -> None:
    calendar = BacktestCalendar("24/7")
    session = date(2024, 1, 2)
    window = calendar.session_window(session)
    available_at = calendar.bar_available_at(session)

    assert window.close_at == datetime(2024, 1, 3, tzinfo=UTC)
    assert calendar.next_session_open(session) == window.close_at
    assert available_at == datetime(2024, 1, 3, 0, 15, tzinfo=UTC)
    assert calendar.next_open_after(available_at) == datetime(2024, 1, 4, tzinfo=UTC)
    assert calendar.next_open_after(available_at) > available_at


def test_schedule_digest_binds_calendar_timezone_and_settlement_versions() -> None:
    instrument, bars, _, _ = _records()
    baseline = schedule_digest(
        instrument,
        bars,
        calendar_engine_version="exchange-calendars/4.13.2",
        timezone_database_version="tzdata/2025.2",
        settlement_policy_version="daily-bar-settlement/1",
    )
    assert baseline != schedule_digest(
        instrument,
        bars,
        calendar_engine_version="exchange-calendars/4.14.0",
        timezone_database_version="tzdata/2025.2",
        settlement_policy_version="daily-bar-settlement/1",
    )
    assert baseline != schedule_digest(
        instrument,
        bars,
        calendar_engine_version="exchange-calendars/4.13.2",
        timezone_database_version="tzdata/2026.1",
        settlement_policy_version="daily-bar-settlement/1",
    )
    assert baseline != schedule_digest(
        instrument,
        bars,
        calendar_engine_version="exchange-calendars/4.13.2",
        timezone_database_version="tzdata/2025.2",
        settlement_policy_version="daily-bar-settlement/2",
    )


def test_manifest_json_is_canonical_and_contains_no_binary_float(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    _ingest(repository)
    dataset_dir = tmp_path / str(DATASET_ID)
    manifest_bytes = (dataset_dir / MANIFEST_FILENAME).read_bytes()
    parsed = json.loads(manifest_bytes)
    canonical = json.dumps(
        parsed, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode() + b"\n"
    assert manifest_bytes == canonical
    assert Decimal(json.loads((dataset_dir / DATA_FILENAME).read_text().splitlines()[1])["payload"]["open"]) == Decimal("100")


def test_canonical_record_normalizes_equivalent_decimal_encodings() -> None:
    bar = _bar(date(2024, 1, 2))
    equivalent = bar.model_copy(
        update={"open": Decimal("100.000"), "volume": Decimal("1000000.00")}
    )
    assert _record_bytes("daily_bar", bar) == _record_bytes("daily_bar", equivalent)
