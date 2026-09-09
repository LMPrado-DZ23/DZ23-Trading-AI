from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tradingagents.platform.backtest.calendar import BacktestCalendar, schedule_digest
from tradingagents.platform.backtest.contracts import (
    M5_UNVERIFIED_LIMITATIONS,
    AssetType,
    AvailabilityBasis,
    CorporateAction,
    CorporateActionType,
    DailyBar,
    ExecutionPriceBundle,
    ExecutionPriceProjection,
    ExecutionPriceProvenance,
    FxObservation,
    InstrumentSpec,
    MarketCalendar,
    canonical_digest,
    revalidate_execution_price_bundle,
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
    MAX_DATASET_ROWS,
    FrozenDatasetRepository,
    _record_bytes,
    _strict_json_loads,
    verify_execution_price_authority,
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
        fetched_at=CREATED_AT,
        available_at=window.close_at + available_lag,
        availability_basis=AvailabilityBasis.EXCHANGE_SETTLEMENT_POLICY,
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
        limitations=M5_UNVERIFIED_LIMITATIONS,
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

    loaded = repository._load_verified(
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
    loaded = repository._load_verified(
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
        repository._load_verified(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest.digest,
        )


def test_load_requires_external_manifest_binding(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    with pytest.raises(DatasetIntegrityError, match="external trusted binding"):
        repository._load_verified(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest="f" * 64,
        )
    assert repository._load_verified(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
    ).manifest == manifest


def test_load_rejects_cross_tenant_even_with_known_manifest_digest(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    with pytest.raises(DatasetIntegrityError) as caught:
        repository._load_verified(
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
        repository._load_verified(
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
        repository._load_verified(
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
        decision_session_date=date(2024, 1, 2),
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
        decision_session_date=date(2024, 1, 2),
    ).eligible_view_sha256 == view.eligible_view_sha256


def test_eligible_view_requires_cutoff_not_earlier_than_frozen_availability(
    tmp_path: Path,
) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    with pytest.raises(DatasetIntegrityError) as caught:
        repository.load_eligible(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest.digest,
            decision_at=datetime(2024, 1, 2, 21, 14, 59, 999999, tzinfo=UTC),
            decision_session_date=date(2024, 1, 2),
        )
    assert caught.value.code is BacktestDataCode.PIT_CUTOFF_MISMATCH

    later_global_cutoff = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=datetime(2024, 1, 2, 21, 20, tzinfo=UTC),
        decision_session_date=date(2024, 1, 2),
    )
    assert later_global_cutoff.decision_at == datetime(2024, 1, 2, 21, 20, tzinfo=UTC)


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
        limitations=M5_UNVERIFIED_LIMITATIONS,
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
        limitations=M5_UNVERIFIED_LIMITATIONS,
    )
    cutoff = datetime(2024, 1, 2, 21, 15, tzinfo=UTC)
    first_view = repository.load_eligible(
        first_id,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=first.digest,
        decision_at=cutoff,
        decision_session_date=date(2024, 1, 2),
    )
    second_view = repository.load_eligible(
        second_id,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=second.digest,
        decision_at=cutoff,
        decision_session_date=date(2024, 1, 2),
    )

    assert first.content_sha256 != second.content_sha256
    assert first.digest != second.digest
    assert first_view.eligible_data_sha256 == second_view.eligible_data_sha256
    assert first_view.eligible_view_sha256 != second_view.eligible_view_sha256
    assert first_view.dataset_id == first_id
    assert second_view.dataset_id == second_id
    assert first_view.tenant_id == TENANT_ID
    assert first_view.quantitative_claims_eligible is False


def test_eligible_view_digest_binds_tenant_and_dataset_provenance(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument, bars, actions, fx = _records()
    other_dataset = UUID("20000000-0000-0000-0000-000000000099")
    other_tenant = UUID("30000000-0000-0000-0000-000000000099")
    first = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="same-content",
        instrument=instrument,
        bars=bars,
        corporate_actions=actions,
        fx_observations=fx,
    )
    second = repository.ingest(
        dataset_id=other_dataset,
        tenant_id=other_tenant,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="same-content",
        instrument=instrument,
        bars=bars,
        corporate_actions=actions,
        fx_observations=fx,
    )
    cutoff = bars[0].available_at
    assert cutoff is not None
    first_view = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=first.digest,
        decision_at=cutoff,
        decision_session_date=bars[0].session_date,
    )
    second_view = repository.load_eligible(
        other_dataset,
        expected_tenant_id=other_tenant,
        expected_manifest_digest=second.digest,
        decision_at=cutoff,
        decision_session_date=bars[0].session_date,
    )
    assert first_view.eligible_data_sha256 == second_view.eligible_data_sha256
    assert first_view.eligible_view_sha256 != second_view.eligible_view_sha256


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
        limitations=M5_UNVERIFIED_LIMITATIONS,
    )

    prior = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=datetime(2024, 1, 3, 21, 15, tzinfo=UTC),
        decision_session_date=date(2024, 1, 3),
    )
    later = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=datetime(2024, 1, 4, 21, 15, tzinfo=UTC),
        decision_session_date=date(2024, 1, 4),
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

    with pytest.raises(MarketCalendarError, match="operational limit"):
        xnys.session_dates_in_range(date(1900, 1, 1), date(2100, 1, 1))


def test_continuous_market_fill_occurs_after_bar_availability() -> None:
    calendar = BacktestCalendar("24/7")
    session = date(2024, 1, 2)
    window = calendar.session_window(session)
    available_at = calendar.settlement_policy_available_at(session)

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


@pytest.mark.parametrize("payload", [b'{\"a\":[1,2}}', b'[{\"a\":1}] }'])
def test_json_parser_rejects_typed_delimiter_mismatch(payload: bytes) -> None:
    with pytest.raises(DatasetIntegrityError, match="mismatched delimiters"):
        _strict_json_loads(payload)


def test_parser_rejects_impossible_manifest_count_before_record_processing() -> None:
    with pytest.raises(DatasetIntegrityError, match="row count"):
        FrozenDatasetRepository._parse_records(b"not-json\n", MAX_DATASET_ROWS + 1)


def test_record_parser_is_incremental_and_has_no_splitlines_amplification() -> None:
    source = (
        Path(__file__).parents[1]
        / "tradingagents"
        / "platform"
        / "backtest"
        / "repository.py"
    ).read_text(encoding="utf-8")
    parser_source = source[source.index("    def _parse_records(") : source.index("    @staticmethod\n    def _is_reparse")]
    assert ".splitlines(" not in parser_source
    assert ".readline(MAX_JSON_RECORD_BYTES + 2)" in parser_source


def test_repository_rejects_runtime_type_confusion_before_io(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    class EqualToEverything:
        def __eq__(self, _other: object) -> bool:
            return True

    with pytest.raises(DatasetIntegrityError) as dataset_error:
        repository.verify(
            str(DATASET_ID),  # type: ignore[arg-type]
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest.digest,
        )
    assert dataset_error.value.code is BacktestDataCode.DATASET_INVALID

    with pytest.raises(DatasetIntegrityError) as tenant_error:
        repository.verify(
            DATASET_ID,
            expected_tenant_id=EqualToEverything(),  # type: ignore[arg-type]
            expected_manifest_digest=manifest.digest,
        )
    assert tenant_error.value.code is BacktestDataCode.DATASET_INVALID


def test_public_repository_api_does_not_expose_full_future_snapshot(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    manifest = _ingest(repository)

    assert not hasattr(repository, "load")
    assert repository.verify(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
    ) == manifest


def test_full_snapshot_loader_remains_private_to_repository_module() -> None:
    project = Path(__file__).parents[1]
    sources = tuple((project / "tradingagents").rglob("*.py")) + tuple(
        (project / "service").rglob("*.py")
    ) + tuple(
        (project / "cli").rglob("*.py")
    )
    authority = project / "tradingagents" / "platform" / "backtest" / "repository.py"
    for source in sources:
        if source == authority:
            continue
        content = source.read_text(encoding="utf-8")
        assert "_load_verified" not in content, source
        assert "_FrozenMarketDataset" not in content, source


@pytest.mark.skipif(os.name != "posix", reason="POSIX dirfd hardening is the production path")
def test_repository_root_handle_survives_ancestor_symlink_swap(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    repository = FrozenDatasetRepository(authority / "repository")
    manifest = _ingest(repository)
    moved = tmp_path / "moved-authority"
    authority.rename(moved)
    attacker = tmp_path / "attacker"
    (attacker / "repository").mkdir(parents=True)
    authority.symlink_to(attacker, target_is_directory=True)

    assert repository.verify(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
    ) == manifest


@pytest.mark.parametrize(
    "revision_update",
    [
        {"revision": 2, "published_at": datetime(2024, 1, 2, 12, tzinfo=UTC)},
        {
            "revision": 2,
            "published_at": datetime(2024, 1, 2, 12, 0, 30, tzinfo=UTC),
            "available_at": datetime(2024, 1, 2, 12, 1, tzinfo=UTC),
        },
        {"revision": 3},
        {
            "revision": 2,
            "effective_date": date(2024, 1, 4),
            "pay_date": date(2024, 1, 4),
        },
        {"revision": 2, "ex_date": date(2024, 1, 4)},
    ],
)
def test_ingest_rejects_backdated_gapped_or_identity_rewriting_revision(
    tmp_path: Path, revision_update: dict[str, object]
) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument, bars, actions, fx = _records()
    update = {
        "revision": 2,
        "published_at": datetime(2024, 1, 4, 12, tzinfo=UTC),
        "available_at": datetime(2024, 1, 4, 12, 1, tzinfo=UTC),
        "source_digest": "e" * 64,
    }
    update.update(revision_update)
    correction = actions[0].model_copy(update=update)

    with pytest.raises(DatasetIntegrityError, match="revisions|rewrite"):
        repository.ingest(
            dataset_id=DATASET_ID,
            tenant_id=TENANT_ID,
            created_at=CREATED_AT,
            source="test.vendor",
            source_vintage="adversarial-revision",
            instrument=instrument,
            bars=bars,
            corporate_actions=(actions[0], correction),
            fx_observations=fx,
        )


def test_delisting_requires_matching_action_and_forbids_post_delisting_bars(
    tmp_path: Path,
) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    base = _instrument()
    instrument = InstrumentSpec(**(base.model_dump() | {"delisting_date": date(2024, 1, 3)}))
    delisting = CorporateAction(
        action_id=UUID("40000000-0000-0000-0000-000000000099"),
        instrument_id=instrument.instrument_id,
        action_type=CorporateActionType.DELISTING,
        published_at=datetime(2024, 1, 2, 12, tzinfo=UTC),
        available_at=datetime(2024, 1, 2, 12, 1, tzinfo=UTC),
        effective_date=date(2024, 1, 3),
        source_digest="d" * 64,
    )

    with pytest.raises(DatasetIntegrityError, match="requires matching"):
        repository.ingest(
            dataset_id=DATASET_ID,
            tenant_id=TENANT_ID,
            created_at=CREATED_AT,
            source="test.vendor",
            source_vintage="missing-delisting",
            instrument=instrument,
            bars=(_bar(date(2024, 1, 2), instrument=instrument),),
        )

    with pytest.raises(DatasetIntegrityError, match="follows.*delisting"):
        repository.ingest(
            dataset_id=DATASET_ID,
            tenant_id=TENANT_ID,
            created_at=CREATED_AT,
            source="test.vendor",
            source_vintage="post-delisting",
            instrument=instrument,
            bars=(
                _bar(date(2024, 1, 2), instrument=instrument),
                _bar(date(2024, 1, 4), instrument=instrument),
            ),
            corporate_actions=(delisting,),
        )


def test_unknown_bar_availability_is_never_eligible(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument = _instrument()
    known = _bar(date(2024, 1, 2), instrument=instrument)
    unknown = DailyBar(
        **(
            _bar(date(2024, 1, 3), instrument=instrument).model_dump()
            | {
                "availability_basis": AvailabilityBasis.UNKNOWN,
                "available_at": None,
                "source_published_at": None,
            }
        )
    )
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="unknown-availability",
        instrument=instrument,
        bars=(known, unknown),
        limitations=M5_UNVERIFIED_LIMITATIONS,
    )

    with pytest.raises(DatasetIntegrityError) as caught:
        repository.load_eligible(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest.digest,
            decision_at=unknown.session_close_at + timedelta(minutes=15),
            decision_session_date=unknown.session_date,
        )
    assert caught.value.code is BacktestDataCode.PIT_AVAILABILITY_UNKNOWN


def test_vendor_published_bar_uses_actual_delayed_availability(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument = _instrument()
    base = _bar(date(2024, 1, 2), instrument=instrument)
    source_published_at = base.session_close_at + timedelta(hours=2)
    actual = DailyBar(
        **(
            base.model_dump()
            | {
                "availability_basis": AvailabilityBasis.SOURCE_PUBLISHED,
                "source_published_at": source_published_at,
                "available_at": source_published_at + timedelta(seconds=5),
            }
        )
    )
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="actual-publication",
        instrument=instrument,
        bars=(actual,),
        limitations=M5_UNVERIFIED_LIMITATIONS,
    )

    view = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=actual.available_at,
        decision_session_date=actual.session_date,
    )
    assert view.bars == (actual,)


def test_continuous_execution_open_is_strictly_after_decision_and_minimal(
    tmp_path: Path,
) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument = _instrument(market_calendar=MarketCalendar.CONTINUOUS)
    bars = tuple(
        _bar(session, instrument=instrument)
        for session in (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))
    )
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="continuous-next-open",
        instrument=instrument,
        bars=bars,
        limitations=M5_UNVERIFIED_LIMITATIONS,
    )
    decision_at = bars[0].available_at
    assert decision_at is not None
    view = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=decision_at,
        decision_session_date=bars[0].session_date,
    )

    execution = repository.load_execution_open(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=decision_at,
        decision_session_date=bars[0].session_date,
        expected_eligible_view_digest=view.eligible_view_sha256,
    )
    assert execution.execution_at == bars[2].session_open_at
    assert execution.execution_at > decision_at
    assert not hasattr(execution, "close")
    assert execution.eligible_view_digest == view.eligible_view_sha256
    assert execution.manifest_digest == manifest.digest

    with pytest.raises(DatasetIntegrityError) as caught:
        repository.load_execution_open(
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest.digest,
            decision_at=decision_at,
            decision_session_date=bars[0].session_date,
            expected_eligible_view_digest="f" * 64,
        )
    assert caught.value.code is BacktestDataCode.DATASET_BINDING_MISMATCH


def _execution_price_fixture(
    root: Path,
    *,
    future_open: str = "100",
    future_high: str = "110",
    future_low: str = "90",
    future_close: str = "105",
    future_volume: str = "1000000",
):
    repository = FrozenDatasetRepository(root)
    instrument = _instrument()
    decision_bar = _bar(date(2024, 1, 2), instrument=instrument)
    future_bar = _bar(date(2024, 1, 3), instrument=instrument).model_copy(
        update={
            "open": Decimal(future_open),
            "high": Decimal(future_high),
            "low": Decimal(future_low),
            "close": Decimal(future_close),
            "volume": Decimal(future_volume),
        }
    )
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="execution-price-projection",
        instrument=instrument,
        bars=(decision_bar, future_bar),
        limitations=M5_UNVERIFIED_LIMITATIONS,
    )
    assert decision_bar.available_at is not None
    view = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=decision_bar.available_at,
        decision_session_date=decision_bar.session_date,
    )
    bundle = repository.load_execution_price(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=instrument.instrument_id,
        expected_manifest_digest=manifest.digest,
        decision_at=decision_bar.available_at,
        decision_session_date=decision_bar.session_date,
        expected_eligible_view_digest=view.eligible_view_sha256,
    )
    return repository, manifest, view, bundle


def test_execution_price_is_stable_when_only_future_non_open_fields_change(
    tmp_path: Path,
) -> None:
    first = _execution_price_fixture(tmp_path / "first")[-1]
    second = _execution_price_fixture(
        tmp_path / "second",
        future_high="120",
        future_low="80",
        future_close="99",
        future_volume="77",
    )[-1]
    assert first.price == second.price
    assert first.price.projection_digest == second.price.projection_digest
    assert first.provenance.provenance_digest != second.provenance.provenance_digest
    assert first.bundle_digest != second.bundle_digest
    assert not hasattr(first.price, "high")
    assert not hasattr(first.price, "volume")


def test_execution_price_changes_only_when_raw_open_changes(tmp_path: Path) -> None:
    first = _execution_price_fixture(tmp_path / "first")[-1]
    second = _execution_price_fixture(
        tmp_path / "second",
        future_open="101",
        future_high="111",
    )[-1]
    assert first.price.raw_open == Decimal("100")
    assert second.price.raw_open == Decimal("101")
    assert first.price.projection_digest != second.price.projection_digest


def test_execution_price_contract_rejects_economic_digest_tampering(tmp_path: Path) -> None:
    bundle = _execution_price_fixture(tmp_path)[-1]
    with pytest.raises(ValueError, match="execution-price digest"):
        ExecutionPriceProjection.model_validate(
            bundle.price.model_dump(mode="python") | {"raw_open": Decimal("99")}
        )


def test_execution_price_deep_revalidation_rejects_preconstructed_tampering(
    tmp_path: Path,
) -> None:
    bundle = _execution_price_fixture(tmp_path)[-1]
    other = _execution_price_fixture(
        tmp_path / "other", future_open="101", future_high="111"
    )[-1]
    cases = (
        bundle.model_copy(
            update={
                "price": bundle.price.model_copy(update={"raw_open": Decimal("999999")})
            }
        ),
        bundle.model_copy(
            update={
                "provenance": bundle.provenance.model_copy(
                    update={"dataset_id": uuid4()}
                )
            }
        ),
        bundle.model_copy(update={"bundle_digest": "0" * 64}),
        bundle.model_copy(update={"price": other.price}),
    )

    for forged in cases:
        with pytest.raises(ValueError):
            ExecutionPriceBundle.model_validate(forged)
        with pytest.raises(ValueError):
            revalidate_execution_price_bundle(forged)


def test_execution_price_boundary_rejects_contract_subclasses(tmp_path: Path) -> None:
    bundle = _execution_price_fixture(tmp_path)[-1]

    class ForgedBundle(ExecutionPriceBundle):
        pass

    forged = ForgedBundle.model_construct(**bundle.model_dump(mode="python"))
    with pytest.raises(ValueError, match="exact typed contract"):
        revalidate_execution_price_bundle(forged)


def test_repository_rejects_self_consistent_forged_execution_price(tmp_path: Path) -> None:
    repository, manifest, view, bundle = _execution_price_fixture(tmp_path)
    forged_price_payload = bundle.price.model_dump(
        mode="python", exclude={"projection_digest"}
    ) | {
        "instrument_id": uuid4(),
        "raw_open": Decimal("999999"),
        "execution_at": bundle.price.execution_at + timedelta(minutes=1),
    }
    forged_price = ExecutionPriceProjection(
        **forged_price_payload,
        projection_digest=canonical_digest(forged_price_payload),
    )
    forged_provenance_payload = bundle.provenance.model_dump(
        mode="python", exclude={"provenance_digest"}
    ) | {"projection_digest": forged_price.projection_digest}
    forged_provenance = ExecutionPriceProvenance(
        **forged_provenance_payload,
        provenance_digest=canonical_digest(forged_provenance_payload),
    )
    forged_bundle_payload = {
        "bundle_version": "execution-price-bundle/1",
        "price_digest": forged_price.projection_digest,
        "provenance_digest": forged_provenance.provenance_digest,
    }
    forged = ExecutionPriceBundle(
        price=forged_price,
        provenance=forged_provenance,
        bundle_digest=canonical_digest(forged_bundle_payload),
    )

    with pytest.raises(DatasetIntegrityError, match="does not match frozen market data"):
        verify_execution_price_authority(
            repository,
            forged,
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_instrument_id=INSTRUMENT_ID,
            expected_manifest_digest=manifest.digest,
            decision_at=view.decision_at,
            decision_session_date=view.decision_session_date,
            expected_eligible_view_digest=view.eligible_view_sha256,
        )

    assert verify_execution_price_authority(
        repository,
        bundle,
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=view.decision_at,
        decision_session_date=view.decision_session_date,
        expected_eligible_view_digest=view.eligible_view_sha256,
    ) == bundle


def test_production_code_has_no_direct_execution_price_consumer() -> None:
    package_root = Path(__file__).parents[1] / "tradingagents"
    allowed = {
        package_root / "platform" / "backtest" / "contracts.py",
        package_root / "platform" / "backtest" / "repository.py",
        package_root / "platform" / "backtest" / "__init__.py",
    }
    protected = {
        "ExecutionPriceBundle",
        "ExecutionPriceProjection",
        "ExecutionPriceProvenance",
    }
    violations: list[str] = []
    for path in package_root.rglob("*.py"):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        referenced = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        overlap = sorted(referenced & protected)
        if overlap:
            violations.append(f"{path.relative_to(package_root)}: {', '.join(overlap)}")
    assert not violations, (
        "production consumers must resolve execution prices through the non-virtual "
        f"repository authority boundary: {violations}"
    )


def test_execution_price_requires_exact_repository_authority(tmp_path: Path) -> None:
    repository, manifest, view, bundle = _execution_price_fixture(tmp_path / "source")

    class ForgedRepository(FrozenDatasetRepository):
        def load_execution_price(self, *args, **kwargs):
            return bundle.model_copy(
                update={
                    "price": bundle.price.model_copy(
                        update={"raw_open": Decimal("999999")}
                    )
                }
            )

    attacker = ForgedRepository(tmp_path / "attacker")
    with pytest.raises(DatasetIntegrityError, match="repository authority"):
        verify_execution_price_authority(
            attacker,
            bundle,
            DATASET_ID,
            expected_tenant_id=TENANT_ID,
            expected_instrument_id=INSTRUMENT_ID,
            expected_manifest_digest=manifest.digest,
            decision_at=view.decision_at,
            decision_session_date=view.decision_session_date,
            expected_eligible_view_digest=view.eligible_view_sha256,
        )
    with pytest.raises(AttributeError):
        repository.load_execution_price = lambda *args, **kwargs: bundle  # type: ignore[method-assign]
    assert repository.load_execution_price(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=view.decision_at,
        decision_session_date=view.decision_session_date,
        expected_eligible_view_digest=view.eligible_view_sha256,
    ).price.raw_open == Decimal("100")


def test_ingest_revalidates_preconstructed_contract_instances(tmp_path: Path) -> None:
    instrument, bars, actions, fx = _records()
    cases = {
        "instrument": {
            "instrument": instrument.model_copy(update={"symbol": "invalid symbol"}),
            "bars": bars,
            "corporate_actions": actions,
            "fx_observations": fx,
        },
        "bar": {
            "instrument": instrument,
            "bars": (bars[0].model_copy(update={"open": Decimal("-1")}),),
            "corporate_actions": (),
            "fx_observations": (),
        },
        "action": {
            "instrument": instrument,
            "bars": bars,
            "corporate_actions": (
                actions[0].model_copy(update={"split_ratio": Decimal("-1")}),
            ),
            "fx_observations": fx,
        },
        "fx": {
            "instrument": instrument,
            "bars": bars,
            "corporate_actions": actions,
            "fx_observations": (fx[0].model_copy(update={"rate": Decimal("-1")}),),
        },
    }

    for label, records in cases.items():
        repository = FrozenDatasetRepository(tmp_path / label)
        with pytest.raises(DatasetIntegrityError, match="revalidation"):
            repository.ingest(
                dataset_id=DATASET_ID,
                tenant_id=TENANT_ID,
                created_at=CREATED_AT,
                source="test.vendor",
                source_vintage=f"invalid-{label}",
                **records,
            )


def test_ingest_bounds_untrusted_limitation_iterable(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument, bars, actions, fx = _records()

    def endless_limitations():
        while True:
            yield M5_UNVERIFIED_LIMITATIONS[0]

    with pytest.raises(DatasetIntegrityError, match="limitation row limit"):
        repository.ingest(
            dataset_id=DATASET_ID,
            tenant_id=TENANT_ID,
            created_at=CREATED_AT,
            source="test.vendor",
            source_vintage="unbounded-limitations",
            instrument=instrument,
            bars=bars,
            corporate_actions=actions,
            fx_observations=fx,
            limitations=endless_limitations(),  # type: ignore[arg-type]
        )


def test_ingest_rejects_missing_internal_exchange_session(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument = _instrument()
    with pytest.raises(DatasetIntegrityError, match="missing exchange session"):
        repository.ingest(
            dataset_id=DATASET_ID,
            tenant_id=TENANT_ID,
            created_at=CREATED_AT,
            source="test.vendor",
            source_vintage="missing-session",
            instrument=instrument,
            bars=(
                _bar(date(2024, 1, 2), instrument=instrument),
                _bar(date(2024, 1, 4), instrument=instrument),
            ),
        )


def test_eligible_instrument_hides_future_delisting_until_announcement(
    tmp_path: Path,
) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    base = _instrument()
    instrument = InstrumentSpec(**(base.model_dump() | {"delisting_date": date(2024, 1, 5)}))
    bars = tuple(
        _bar(session, instrument=instrument)
        for session in (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))
    )
    delisting = CorporateAction(
        action_id=UUID("40000000-0000-0000-0000-000000000098"),
        instrument_id=instrument.instrument_id,
        action_type=CorporateActionType.DELISTING,
        published_at=datetime(2024, 1, 4, 12, tzinfo=UTC),
        available_at=datetime(2024, 1, 4, 12, 1, tzinfo=UTC),
        effective_date=date(2024, 1, 5),
        source_digest="d" * 64,
    )
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="future-delisting",
        instrument=instrument,
        bars=bars,
        corporate_actions=(delisting,),
    )

    before = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=bars[0].available_at,
        decision_session_date=bars[0].session_date,
    )
    after = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=bars[2].available_at,
        decision_session_date=bars[2].session_date,
    )
    assert before.instrument.delisting_date is None
    assert before.corporate_actions == ()
    assert after.instrument.delisting_date == date(2024, 1, 5)
    assert after.corporate_actions == (delisting,)


def test_ingest_rejects_multiple_logical_delisting_actions(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    base = _instrument()
    instrument = InstrumentSpec(**(base.model_dump() | {"delisting_date": date(2024, 1, 3)}))

    def delisting(action_id: str, amount: str) -> CorporateAction:
        return CorporateAction(
            action_id=UUID(action_id),
            instrument_id=instrument.instrument_id,
            action_type=CorporateActionType.DELISTING,
            published_at=datetime(2024, 1, 2, 12, tzinfo=UTC),
            available_at=datetime(2024, 1, 2, 12, 1, tzinfo=UTC),
            effective_date=date(2024, 1, 3),
            pay_date=date(2024, 1, 4),
            cash_consideration=Decimal(amount),
            currency="USD",
            source_digest="d" * 64,
        )

    with pytest.raises(DatasetIntegrityError, match="multiple logical delisting"):
        repository.ingest(
            dataset_id=DATASET_ID,
            tenant_id=TENANT_ID,
            created_at=CREATED_AT,
            source="test.vendor",
            source_vintage="duplicate-delisting",
            instrument=instrument,
            bars=(
                _bar(date(2024, 1, 2), instrument=instrument),
                _bar(date(2024, 1, 3), instrument=instrument),
            ),
            corporate_actions=(
                delisting("40000000-0000-0000-0000-000000000091", "10"),
                delisting("40000000-0000-0000-0000-000000000092", "11"),
            ),
        )


def test_eligible_digest_binds_decision_session_even_with_shared_cutoff(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument = _instrument()
    shared_cutoff = datetime(2024, 1, 4, 12, tzinfo=UTC)
    bars = tuple(
        DailyBar(
            **(
                _bar(session, instrument=instrument).model_dump()
                | {
                    "availability_basis": AvailabilityBasis.SOURCE_PUBLISHED,
                    "source_published_at": shared_cutoff - timedelta(seconds=1),
                    "available_at": shared_cutoff,
                }
            )
        )
        for session in (date(2024, 1, 2), date(2024, 1, 3))
    )
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="shared-cutoff",
        instrument=instrument,
        bars=bars,
    )
    first = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=shared_cutoff,
        decision_session_date=bars[0].session_date,
    )
    second = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=shared_cutoff,
        decision_session_date=bars[1].session_date,
    )
    assert first.eligible_data_sha256 != second.eligible_data_sha256
    assert first.eligible_view_sha256 != second.eligible_view_sha256


def test_delayed_bar_cutoff_never_exposes_a_later_session_bar(tmp_path: Path) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    instrument = _instrument()
    first_base = _bar(date(2024, 1, 2), instrument=instrument)
    second_base = _bar(date(2024, 1, 3), instrument=instrument)
    first_available = datetime(2024, 1, 4, 12, tzinfo=UTC)
    first = DailyBar(
        **(
            first_base.model_dump()
            | {
                "availability_basis": AvailabilityBasis.SOURCE_PUBLISHED,
                "source_published_at": first_available - timedelta(seconds=1),
                "available_at": first_available,
            }
        )
    )
    second = DailyBar(
        **(
            second_base.model_dump()
            | {
                "availability_basis": AvailabilityBasis.SOURCE_PUBLISHED,
                "source_published_at": second_base.available_at - timedelta(seconds=1),
            }
        )
    )
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="cross-session-delay",
        instrument=instrument,
        bars=(first, second),
    )

    view = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=first_available,
        decision_session_date=first.session_date,
    )
    assert view.data_cutoff_at == first_available
    assert view.bars == (first,)


@pytest.mark.skipif(os.name != "posix", reason="POSIX dirfd hardening is the production path")
def test_repository_init_detects_root_identity_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "repository"
    target.mkdir()
    original_target = tmp_path / "original-repository"
    original_open = os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and dir_fd is None and Path(path) == target:
            target.rename(original_target)
            target.mkdir()
            swapped = True
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(DatasetIntegrityError, match="identity changed"):
        FrozenDatasetRepository(target)


@pytest.mark.skipif(os.name != "posix", reason="POSIX renameat2 is the production path")
def test_atomic_publish_never_replaces_racing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = FrozenDatasetRepository(tmp_path)
    original = repository._rename_directory_noreplace

    def race(directory_fd: int, source: str, destination: str) -> None:
        os.mkdir(destination, dir_fd=directory_fd)
        original(directory_fd, source, destination)

    monkeypatch.setattr(repository, "_rename_directory_noreplace", race)
    with pytest.raises(DatasetAlreadyExistsError):
        _ingest(repository)
    assert (tmp_path / str(DATASET_ID)).is_dir()
    assert not (tmp_path / str(DATASET_ID) / DATA_FILENAME).exists()
