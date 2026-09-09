from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, Inexact, Rounded, localcontext
from pathlib import Path
from typing import get_type_hints
from uuid import UUID

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from tradingagents.platform.backtest import (
    M5_UNVERIFIED_LIMITATIONS,
    AvailabilityBasis,
    CandlestickEvaluationStatus,
    CandlestickEvidenceBundle,
    CandlestickEvidenceError,
    CandlestickFeatureSet,
    CandlestickMatch,
    CandlestickPattern,
    CorporateAction,
    CorporateActionType,
    DailyBar,
    DatasetIntegrityError,
    FrozenDatasetRepository,
    FxObservation,
    InstrumentSpec,
    MarketCalendar,
    candlestick_bundle_to_untrusted_evidence,
    canonical_json_bytes,
)
from tradingagents.platform.backtest.calendar import BacktestCalendar
from tradingagents.platform.backtest.candlesticks import (
    _derive_candlestick_bundle_from_view,
    _split_context_digest,
)

TENANT_ID = UUID("30000000-0000-0000-0000-000000000051")
INSTRUMENT_ID = UUID("10000000-0000-0000-0000-000000000051")
CREATED_AT = datetime(2028, 1, 1, tzinfo=UTC)


def _instrument() -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=INSTRUMENT_ID,
        symbol="BTC/USD",
        asset_type="crypto",
        venue="kraken",
        venue_instrument_id="BTC/USD",
        base_currency="BTC",
        quote_currency="USD",
        market_calendar=MarketCalendar.CONTINUOUS,
        published_at=datetime(2020, 1, 1, tzinfo=UTC),
        available_at=datetime(2020, 1, 1, tzinfo=UTC),
        listing_date=date(2020, 1, 1),
        source_digest="1" * 64,
    )


def _bar(
    day: date,
    prices: tuple[str, str, str, str],
    *,
    ordinal: int,
    unknown: bool = False,
    volume: str = "1000",
) -> DailyBar:
    window = BacktestCalendar(MarketCalendar.CONTINUOUS).session_window(day)
    opened, high, low, closed = prices
    return DailyBar(
        instrument_id=INSTRUMENT_ID,
        session_date=day,
        session_open_at=window.open_at,
        session_close_at=window.close_at,
        fetched_at=CREATED_AT,
        available_at=None if unknown else window.close_at + timedelta(minutes=15),
        availability_basis=(
            AvailabilityBasis.UNKNOWN if unknown else AvailabilityBasis.EXCHANGE_SETTLEMENT_POLICY
        ),
        open=opened,
        high=high,
        low=low,
        close=closed,
        volume=volume,
        source_digest=hashlib.sha256(f"bar:{ordinal}".encode()).hexdigest(),
    )


@dataclass(frozen=True)
class _PreparedDataset:
    repository: FrozenDatasetRepository
    dataset_id: UUID
    manifest_digest: str
    decision_at: datetime
    decision_session_date: date

    def view(self):
        return self.repository.load_eligible(
            self.dataset_id,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=self.manifest_digest,
            decision_at=self.decision_at,
            decision_session_date=self.decision_session_date,
        )

    def derive(self):
        return self.repository.derive_candlestick_bundle(
            self.dataset_id,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=self.manifest_digest,
            decision_at=self.decision_at,
            decision_session_date=self.decision_session_date,
        )


def _prepare(
    root: Path,
    prices: tuple[tuple[str, str, str, str], ...],
    *,
    dataset_number: int = 1,
    decision_index: int | None = None,
    unknown_indices: frozenset[int] = frozenset(),
    split_index: int | None = None,
    split_indices: tuple[int, ...] = (),
    historical_split_count: int = 0,
    volume_overrides: dict[int, str] | None = None,
):
    start = date(2026, 1, 1)
    bars = tuple(
        _bar(
            start + timedelta(days=index),
            values,
            ordinal=index,
            unknown=index in unknown_indices,
            volume=(volume_overrides or {}).get(index, "1000"),
        )
        for index, values in enumerate(prices)
    )
    split_modes = sum((split_index is not None, bool(split_indices), historical_split_count > 0))
    if split_modes > 1:
        raise ValueError("use only one split fixture mode")
    if historical_split_count < 0:
        raise ValueError("historical split count cannot be negative")
    if historical_split_count:
        resolved_split_indices = (0,) * historical_split_count
    else:
        resolved_split_indices = (split_index,) if split_index is not None else split_indices
    action_rows: list[CorporateAction] = []
    for sequence, index in enumerate(resolved_split_indices, start=1):
        split_day = start + timedelta(days=index)
        action_rows.append(
            CorporateAction(
                action_id=UUID(
                    f"40000000-0000-0000-0000-{dataset_number * 100000 + sequence:012d}"
                ),
                instrument_id=INSTRUMENT_ID,
                action_type=CorporateActionType.SPLIT,
                published_at=datetime.combine(
                    split_day - timedelta(days=1), datetime.min.time(), UTC
                ),
                available_at=datetime.combine(
                    split_day - timedelta(days=1), datetime.min.time(), UTC
                )
                + timedelta(minutes=1),
                ex_date=split_day,
                effective_date=split_day,
                split_ratio="2",
                source_digest=hashlib.sha256(
                    f"split:{dataset_number}:{sequence}".encode()
                ).hexdigest(),
            )
        )
    actions = tuple(action_rows)
    dataset_id = UUID(f"20000000-0000-0000-0000-{dataset_number:012d}")
    repository = FrozenDatasetRepository(root)
    latest_record_at = max(
        [bar.fetched_at for bar in bars] + [action.available_at for action in actions]
    )
    manifest = repository.ingest(
        dataset_id=dataset_id,
        tenant_id=TENANT_ID,
        created_at=max(CREATED_AT, latest_record_at + timedelta(days=1)),
        source="candlestick-fixture",
        source_vintage="fixture-1",
        instrument=_instrument(),
        bars=bars,
        corporate_actions=actions,
        limitations=M5_UNVERIFIED_LIMITATIONS,
    )
    resolved_index = len(bars) - 1 if decision_index is None else decision_index
    decision_bar = bars[resolved_index]
    assert decision_bar.available_at is not None
    return _PreparedDataset(
        repository=repository,
        dataset_id=dataset_id,
        manifest_digest=manifest.digest,
        decision_at=decision_bar.available_at + timedelta(minutes=1),
        decision_session_date=decision_bar.session_date,
    )


def _neutral_prices() -> tuple[tuple[str, str, str, str], ...]:
    return (
        ("10", "13.5", "9.5", "13"),
        ("13", "13.5", "11.5", "12"),
        ("12", "12.5", "10.5", "11"),
        ("11", "11.5", "9.5", "10"),
        ("10", "10.6", "9.5", "10.1"),
        ("10.1", "10.7", "9.6", "10.2"),
    )


@pytest.mark.parametrize(
    ("prices", "expected"),
    [
        (
            _neutral_prices()[:-1] + (("10", "10.5", "9.5", "10.05"),),
            CandlestickPattern.DOJI,
        ),
        (
            _neutral_prices()[:-1] + (("10", "11", "10", "11"),),
            CandlestickPattern.MARUBOZU_BULLISH,
        ),
        (
            _neutral_prices()[:-1] + (("11", "11", "10", "10"),),
            CandlestickPattern.MARUBOZU_BEARISH,
        ),
        (
            (
                ("14", "14.5", "13.5", "14"),
                ("13", "13.5", "12.5", "13"),
                ("12", "12.5", "11.5", "12"),
                ("11", "11.5", "10.5", "11"),
                ("10", "10.5", "9.5", "10"),
                ("10", "10.22", "9.5", "10.2"),
            ),
            CandlestickPattern.HAMMER,
        ),
        (
            (
                ("10", "10.5", "9.5", "10"),
                ("10", "11.5", "9.5", "11"),
                ("11", "12.5", "10.5", "12"),
                ("12", "13.5", "11.5", "13"),
                ("13", "14.5", "12.5", "14"),
                ("14", "14.22", "13.5", "14.2"),
            ),
            CandlestickPattern.HANGING_MAN,
        ),
        (
            (
                ("14", "14.5", "13.5", "14"),
                ("13", "13.5", "12.5", "13"),
                ("12", "12.5", "11.5", "12"),
                ("11", "11.5", "10.5", "11"),
                ("10", "10.5", "9.5", "10"),
                ("10", "10.7", "9.98", "10.2"),
            ),
            CandlestickPattern.INVERTED_HAMMER,
        ),
        (
            (
                ("10", "10.5", "9.5", "10"),
                ("10", "11.5", "9.5", "11"),
                ("11", "12.5", "10.5", "12"),
                ("12", "13.5", "11.5", "13"),
                ("13", "14.5", "12.5", "14"),
                ("14", "14.7", "13.98", "14.2"),
            ),
            CandlestickPattern.SHOOTING_STAR,
        ),
        (
            (
                ("14", "14.5", "13.5", "14"),
                ("14", "14.5", "12.5", "13"),
                ("13", "13.5", "11.5", "12"),
                ("12", "12.5", "10.5", "11"),
                ("10.5", "10.6", "9.9", "10"),
                ("9.9", "10.7", "9.8", "10.6"),
            ),
            CandlestickPattern.ENGULFING_BULLISH,
        ),
        (
            (
                ("9", "9.5", "8.5", "9"),
                ("9", "10.5", "8.5", "10"),
                ("10", "11.5", "9.5", "11"),
                ("11", "12.5", "10.5", "12"),
                ("11.5", "12.1", "11.4", "12"),
                ("12.1", "12.2", "11.3", "11.4"),
            ),
            CandlestickPattern.ENGULFING_BEARISH,
        ),
        (
            (
                ("14", "14.5", "12.5", "13"),
                ("13", "13.5", "11.5", "12"),
                ("12", "12.5", "10.5", "11"),
                ("10.5", "10.6", "9.4", "9.5"),
                ("9.7", "9.8", "9.5", "9.6"),
                ("9.6", "10.3", "9.5", "10.2"),
            ),
            CandlestickPattern.MORNING_STAR,
        ),
        (
            (
                ("9", "10.5", "8.5", "10"),
                ("10", "11.5", "9.5", "11"),
                ("11", "12.5", "10.5", "12"),
                ("11.5", "12.6", "11.4", "12.5"),
                ("12.3", "12.5", "12.1", "12.2"),
                ("12.2", "12.3", "11.6", "11.9"),
            ),
            CandlestickPattern.EVENING_STAR,
        ),
    ],
)
def test_golden_pattern_vectors(
    tmp_path: Path,
    prices: tuple[tuple[str, str, str, str], ...],
    expected: CandlestickPattern,
) -> None:
    bundle = _prepare(tmp_path, prices).derive()
    assert bundle.features.status is CandlestickEvaluationStatus.EVALUATED
    assert expected in {item.pattern for item in bundle.features.matches}


def test_doji_boundary_is_inclusive_and_neighbor_is_not(tmp_path: Path) -> None:
    exact = _neutral_prices()[:-1] + (("10", "10.5", "9.5", "10.1"),)
    outside = _neutral_prices()[:-1] + (("10", "10.5", "9.5", "10.1001"),)
    first = _prepare(tmp_path / "a", exact, dataset_number=10).derive()
    second = _prepare(tmp_path / "b", outside, dataset_number=11).derive()
    assert CandlestickPattern.DOJI in {item.pattern for item in first.features.matches}
    assert CandlestickPattern.DOJI not in {item.pattern for item in second.features.matches}


@pytest.mark.parametrize(
    ("last", "pattern", "present"),
    [
        (("105", "200", "100", "195"), CandlestickPattern.MARUBOZU_BULLISH, True),
        (
            ("105", "200", "100", "194.999"),
            CandlestickPattern.MARUBOZU_BULLISH,
            False,
        ),
        (("10.7", "11", "10", "10.9"), CandlestickPattern.HAMMER, True),
        (("10.7", "11.0001", "10", "10.9"), CandlestickPattern.HAMMER, False),
    ],
)
def test_single_candle_threshold_edges(
    tmp_path: Path,
    last: tuple[str, str, str, str],
    pattern: CandlestickPattern,
    present: bool,
) -> None:
    down = (
        ("14", "14.5", "13.5", "14"),
        ("13", "13.5", "12.5", "13"),
        ("12", "12.5", "11.5", "12"),
        ("11", "11.5", "10.5", "11"),
        ("10", "10.5", "9.5", "10"),
        last,
    )
    patterns = {item.pattern for item in _prepare(tmp_path, down).derive().features.matches}
    assert (pattern in patterns) is present


@pytest.mark.parametrize(("middle_body", "present"), [("0.3", True), ("0.3001", False)])
def test_star_middle_body_threshold_edge(tmp_path: Path, middle_body: str, present: bool) -> None:
    middle_close = str(Decimal("9.5") + Decimal(middle_body))
    prices = (
        ("14", "14.5", "12.5", "13"),
        ("13", "13.5", "11.5", "12"),
        ("12", "12.5", "10.5", "11"),
        ("10.5", "10.6", "9.4", "9.5"),
        ("9.5", "10", "9.4", middle_close),
        ("9.6", "10.3", "9.5", "10.2"),
    )
    patterns = {item.pattern for item in _prepare(tmp_path, prices).derive().features.matches}
    assert (CandlestickPattern.MORNING_STAR in patterns) is present


@pytest.mark.parametrize(
    ("count", "status"),
    [
        (5, CandlestickEvaluationStatus.INSUFFICIENT_HISTORY),
        (6, CandlestickEvaluationStatus.EVALUATED),
    ],
)
def test_requires_complete_fixed_history(
    tmp_path: Path, count: int, status: CandlestickEvaluationStatus
) -> None:
    prices = _neutral_prices()[-count:]
    bundle = _prepare(tmp_path, prices).derive()
    assert bundle.features.status is status
    if status is not CandlestickEvaluationStatus.EVALUATED:
        assert bundle.features.matches == ()


def test_unknown_bar_cannot_be_bridged(tmp_path: Path) -> None:
    prices = _neutral_prices() + ((_neutral_prices()[-1]),)
    prepared = _prepare(tmp_path, prices, unknown_indices=frozenset({3}))
    view = prepared.view()
    bundle = prepared.derive()
    assert view.bar_ordinals == (0, 1, 2, 4, 5, 6)
    assert bundle.features.status is CandlestickEvaluationStatus.DISCONTINUOUS_HISTORY
    assert bundle.features.matches == ()


@pytest.mark.parametrize(
    ("split_index", "expected"),
    [
        (0, CandlestickEvaluationStatus.EVALUATED),
        (1, CandlestickEvaluationStatus.SPLIT_BARRIER),
        (5, CandlestickEvaluationStatus.SPLIT_BARRIER),
    ],
)
def test_split_requires_six_post_split_bars(
    tmp_path: Path, split_index: int, expected: CandlestickEvaluationStatus
) -> None:
    bundle = _prepare(tmp_path, _neutral_prices(), split_index=split_index).derive()
    assert bundle.features.status is expected


def test_many_historical_splits_stay_bounded_and_json_parseable(tmp_path: Path) -> None:
    import json

    prices = tuple(("10", "11", "9", "10.1") for _ in range(356))
    prepared = _prepare(
        tmp_path,
        prices,
        dataset_number=12,
        split_indices=tuple(range(350)),
    )
    bundle = prepared.derive()
    evidence = candlestick_bundle_to_untrusted_evidence(bundle)
    assert bundle.features.latest_split_effective_date == date(2026, 12, 16)
    assert bundle.features.split_context_digest is not None
    assert len(evidence.content) < 20_000
    assert "[TRUNCATED" not in evidence.content
    assert json.loads(evidence.content)["split_context_digest"] == (
        bundle.features.split_context_digest
    )


@pytest.mark.parametrize("split_count", (10_001, 24_993))
def test_public_derivation_supports_full_dataset_action_limit(
    tmp_path: Path, split_count: int
) -> None:
    import json

    prepared = _prepare(
        tmp_path,
        _neutral_prices(),
        dataset_number=13,
        historical_split_count=split_count,
    )
    bundle = prepared.derive()
    evidence = candlestick_bundle_to_untrusted_evidence(bundle)
    assert bundle.features.split_action_count == split_count
    assert bundle.features.split_context_digest is not None
    assert json.loads(evidence.content)["split_action_count"] == split_count
    assert "[TRUNCATED" not in evidence.content
    assert evidence.digest == bundle.feature_digest


def test_streaming_eligible_digest_preserves_existing_canonical_preimage(
    tmp_path: Path,
) -> None:
    view = _prepare(tmp_path, _neutral_prices(), split_index=0).view()
    legacy_payload = {
        "data_cutoff_at": view.data_cutoff_at,
        "decision_at": view.decision_at,
        "decision_session_date": view.decision_session_date,
        "instrument": view.instrument.model_dump(mode="python"),
        "daily_bars": [item.model_dump(mode="python") for item in view.bars],
        "corporate_actions": [item.model_dump(mode="python") for item in view.corporate_actions],
        "fx_observations": [item.model_dump(mode="python") for item in view.fx_observations],
    }
    assert (
        view.eligible_data_sha256
        == hashlib.sha256(canonical_json_bytes(legacy_payload)).hexdigest()
    )


def test_zero_range_is_typed_and_empty(tmp_path: Path) -> None:
    prices = _neutral_prices()[:-1] + (("10", "10", "10", "10"),)
    bundle = _prepare(tmp_path, prices).derive()
    assert bundle.features.status is CandlestickEvaluationStatus.ZERO_RANGE
    assert bundle.features.matches == ()


def test_future_append_changes_lineage_not_historical_features(tmp_path: Path) -> None:
    history = _neutral_prices()
    future = history + (("50", "60", "40", "55"),)
    first = _prepare(tmp_path / "a", history, dataset_number=20).derive()
    second = _prepare(tmp_path / "b", future, dataset_number=21, decision_index=5).derive()
    assert canonical_json_bytes(first.features) == canonical_json_bytes(second.features)
    assert first.feature_digest == second.feature_digest
    assert first.eligible_view_sha256 != second.eligible_view_sha256
    assert first.ordinal_binding_digest != second.ordinal_binding_digest


def test_positive_price_scaling_preserves_patterns_and_ratios(tmp_path: Path) -> None:
    original = _neutral_prices()[:-1] + (("10", "10.5", "9.5", "10.05"),)
    scaled = tuple(
        tuple(str(Decimal(value) * Decimal("1000")) for value in candle) for candle in original
    )
    first = _prepare(tmp_path / "a", original, dataset_number=22).derive()
    second = _prepare(tmp_path / "b", scaled, dataset_number=23).derive()
    assert [item.pattern for item in first.features.matches] == [
        item.pattern for item in second.features.matches
    ]


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=(HealthCheck.function_scoped_fixture,),
)
@given(
    low=st.integers(min_value=1, max_value=1_000_000),
    lower_wick=st.integers(min_value=0, max_value=1_000),
    body=st.integers(min_value=0, max_value=1_000),
    upper_wick=st.integers(min_value=0, max_value=1_000),
    bullish=st.booleans(),
)
def test_valid_ohlc_fuzz_is_bounded_and_repeatable(
    tmp_path: Path,
    low: int,
    lower_wick: int,
    body: int,
    upper_wick: int,
    bullish: bool,
) -> None:
    generated_identity = (low, lower_wick, body, upper_wick, bullish)
    if lower_wick + body + upper_wick == 0:
        upper_wick = 1
    if bullish:
        opened = low + lower_wick
        closed = opened + body
    else:
        closed = low + lower_wick
        opened = closed + body
    high = max(opened, closed) + upper_wick
    last = tuple(str(value) for value in (opened, high, low, closed))
    identity = hashlib.sha256(":".join(map(str, generated_identity)).encode()).hexdigest()
    prepared = _prepare(tmp_path / identity, _neutral_prices()[:-1] + (last,))
    first = prepared.derive()
    second = prepared.derive()
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert len(first.features.parent_bar_digests) <= 6
    assert len(first.features.matches) <= len(CandlestickPattern)
    assert [
        tuple(
            (
                geometry.body_to_range_ppm,
                geometry.upper_wick_to_range_ppm,
                geometry.lower_wick_to_range_ppm,
            )
            for geometry in match.geometry
        )
        for match in first.features.matches
    ] == [
        tuple(
            (
                geometry.body_to_range_ppm,
                geometry.upper_wick_to_range_ppm,
                geometry.lower_wick_to_range_ppm,
            )
            for geometry in match.geometry
        )
        for match in second.features.matches
    ]


def test_decimal_context_cannot_change_results(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path, _neutral_prices())
    baseline = canonical_json_bytes(prepared.derive().features)
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert canonical_json_bytes(prepared.derive().features) == baseline


def test_volume_changes_provenance_not_geometry(tmp_path: Path) -> None:
    first = _prepare(tmp_path / "a", _neutral_prices(), dataset_number=30).derive()
    second = _prepare(
        tmp_path / "b",
        _neutral_prices(),
        dataset_number=31,
        volume_overrides={5: "2000"},
    ).derive()

    def ratios(bundle):
        return [
            tuple(
                (
                    item.direction,
                    item.body_to_range_ppm,
                    item.upper_wick_to_range_ppm,
                    item.lower_wick_to_range_ppm,
                )
                for item in match.geometry
            )
            for match in bundle.features.matches
        ]

    assert ratios(first) == ratios(second)
    assert first.feature_digest != second.feature_digest


def test_feature_becomes_derived_untrusted_data_not_an_order(tmp_path: Path) -> None:
    bundle = _prepare(tmp_path, _neutral_prices()).derive()
    evidence = candlestick_bundle_to_untrusted_evidence(bundle)
    block = evidence.to_prompt_block()
    assert evidence.kind == "market_data"
    assert evidence.derived is True
    assert evidence.available_at == bundle.features.available_at.isoformat()
    assert evidence.ref == bundle.digest
    assert evidence.digest == bundle.feature_digest
    assert block.count("[UNTRUSTED EXTERNAL DATA") == 1
    assert block.count("[END UNTRUSTED DATA") == 1
    for forbidden in (
        '"action"',
        '"rating"',
        '"expected_return"',
        '"profit_confidence"',
        '"target_weight"',
        '"quantity"',
    ):
        assert forbidden not in evidence.content


def test_contract_field_names_have_no_capital_instruction_surface() -> None:
    forbidden = {
        "action",
        "side",
        "rating",
        "confidence",
        "expected_return",
        "profit_confidence",
        "target",
        "weight",
        "quantity",
        "entry_price",
        "stop",
    }
    for model in (
        CandlestickFeatureSet,
        CandlestickEvidenceBundle,
        CandlestickMatch,
    ):
        assert not (forbidden & set(model.model_fields))


def test_bundle_rejects_forged_feature_or_ordinal_binding(tmp_path: Path) -> None:
    bundle = _prepare(tmp_path, _neutral_prices()).derive()
    with pytest.raises(ValueError, match="feature digest"):
        CandlestickEvidenceBundle.model_validate(
            bundle.model_dump(mode="python") | {"feature_digest": "f" * 64}
        )
    with pytest.raises(ValueError, match="ordinal binding"):
        CandlestickEvidenceBundle.model_validate(
            bundle.model_dump(mode="python") | {"ordinal_binding_digest": "f" * 64}
        )


def test_public_derivation_bypasses_overridable_load_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(tmp_path, _neutral_prices())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("overridable load seam was consulted")

    monkeypatch.setattr(FrozenDatasetRepository, "load_eligible", forbidden)
    assert prepared.derive().features.status is CandlestickEvaluationStatus.EVALUATED


def test_public_derivation_rejects_repository_subclasses(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path / "authority", _neutral_prices())
    forged_view = replace(prepared.view(), bar_ordinals=(0, 1, 2, 3, 4, 5))

    class ForgedRepository(FrozenDatasetRepository):
        def load_eligible(self, *_args, **_kwargs):
            return forged_view

    attacker = ForgedRepository(tmp_path / "attacker")
    with pytest.raises(DatasetIntegrityError, match="repository authority"):
        attacker.derive_candlestick_bundle(
            prepared.dataset_id,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=prepared.manifest_digest,
            decision_at=prepared.decision_at,
            decision_session_date=prepared.decision_session_date,
        )


def test_public_derivation_return_annotation_is_introspectable() -> None:
    hints = get_type_hints(FrozenDatasetRepository.derive_candlestick_bundle)
    assert "return" in hints


def test_internal_detector_rejects_mutated_view_records(tmp_path: Path) -> None:
    view = _prepare(tmp_path, _neutral_prices()).view()
    with pytest.raises(CandlestickEvidenceError, match="chronological|data digest"):
        _derive_candlestick_bundle_from_view(replace(view, bars=view.bars[:-1] + (view.bars[-2],)))
    with pytest.raises(CandlestickEvidenceError, match="ordinals"):
        _derive_candlestick_bundle_from_view(replace(view, bar_ordinals=(0, 1, 2, 3, 4, 4)))
    with pytest.raises(CandlestickEvidenceError, match="post-cutoff"):
        _derive_candlestick_bundle_from_view(
            replace(view, data_cutoff_at=view.bars[-1].available_at - timedelta(seconds=1))
        )


def test_internal_detector_rejects_foreign_corporate_action(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path, _neutral_prices(), split_index=0)
    view = prepared.view()
    foreign = view.corporate_actions[0].model_copy(
        update={"instrument_id": UUID("10000000-0000-0000-0000-000000000999")}
    )
    with pytest.raises(CandlestickEvidenceError, match="corporate action"):
        _derive_candlestick_bundle_from_view(replace(view, corporate_actions=(foreign,)))


def test_split_context_digest_streams_beyond_canonical_sequence_limit(tmp_path: Path) -> None:
    view = _prepare(tmp_path, _neutral_prices(), split_index=0).view()
    action = view.corporate_actions[0]
    digest = _split_context_digest(tuple(action for _ in range(10_001)))
    assert digest is not None
    assert len(digest) == 64
    assert digest == _split_context_digest(tuple(action for _ in range(10_001)))


def test_internal_detector_rejects_noncanonical_or_duplicate_fx(tmp_path: Path) -> None:
    view = _prepare(tmp_path, _neutral_prices()).view()
    first = FxObservation(
        base_currency="USD",
        quote_currency="BRL",
        observed_at=view.data_cutoff_at - timedelta(hours=2),
        available_at=view.data_cutoff_at - timedelta(hours=1),
        rate="5.10",
        source_digest="a" * 64,
    )
    second = first.model_copy(
        update={
            "observed_at": first.observed_at + timedelta(minutes=10),
            "available_at": first.available_at + timedelta(minutes=10),
            "source_digest": "b" * 64,
        }
    )
    with pytest.raises(CandlestickEvidenceError, match="FX observations are not canonical"):
        _derive_candlestick_bundle_from_view(replace(view, fx_observations=(second, first)))
    with pytest.raises(CandlestickEvidenceError, match="duplicate FX observations"):
        _derive_candlestick_bundle_from_view(replace(view, fx_observations=(first, first)))


def test_internal_detector_rejects_post_cutoff_instrument(tmp_path: Path) -> None:
    view = _prepare(tmp_path, _neutral_prices()).view()
    future_instrument = view.instrument.model_copy(
        update={"available_at": view.data_cutoff_at + timedelta(seconds=1)}
    )
    with pytest.raises(CandlestickEvidenceError, match="instrument is not available"):
        _derive_candlestick_bundle_from_view(replace(view, instrument=future_instrument))


def test_direct_view_derivation_is_not_exported() -> None:
    import tradingagents.platform.backtest as backtest

    assert not hasattr(backtest, "derive_candlestick_bundle")
    assert hasattr(FrozenDatasetRepository, "derive_candlestick_bundle")


def test_public_derivation_rejects_wrong_external_authority(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path, _neutral_prices())
    with pytest.raises(DatasetIntegrityError, match="binding|tenant|manifest"):
        prepared.repository.derive_candlestick_bundle(
            prepared.dataset_id,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest="f" * 64,
            decision_at=prepared.decision_at,
            decision_session_date=prepared.decision_session_date,
        )


def _import_manifest(source: str):
    tree = ast.parse(source)
    return {
        (
            "import" if isinstance(node, ast.Import) else "from",
            node.level if isinstance(node, ast.ImportFrom) else 0,
            node.module if isinstance(node, ast.ImportFrom) else "",
            tuple((alias.name, alias.asname) for alias in node.names),
        )
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }


def _detector_allowed_imports():
    return {
        ("from", 0, "__future__", (("annotations", None),)),
        ("import", 0, "", (("hashlib", None),)),
        ("import", 0, "", (("hmac", None),)),
        ("from", 0, "datetime", (("UTC", None), ("date", None), ("datetime", None))),
        (
            "from",
            0,
            "decimal",
            (
                ("ROUND_HALF_EVEN", None),
                ("Context", None),
                ("Decimal", None),
                ("localcontext", None),
            ),
        ),
        ("from", 0, "enum", (("StrEnum", None),)),
        ("from", 0, "typing", (("Literal", None),)),
        ("from", 0, "uuid", (("UUID", None),)),
        (
            "from",
            0,
            "pydantic",
            (("Field", None), ("field_validator", None), ("model_validator", None)),
        ),
        (
            "from",
            0,
            "tradingagents.dataflows.evidence",
            (("MAX_CONTENT_CHARS", None), ("UntrustedEvidence", None)),
        ),
        (
            "from",
            1,
            "contracts",
            (
                ("BacktestContract", None),
                ("CorporateAction", None),
                ("CorporateActionType", None),
                ("DailyBar", None),
                ("Digest", None),
                ("FxObservation", None),
                ("InstrumentSpec", None),
                ("canonical_digest", None),
                ("canonical_json_bytes", None),
            ),
        ),
        (
            "from",
            1,
            "repository",
            (("EligibleMarketView", None), ("_eligible_data_sha256", None)),
        ),
    }


def _forbidden_dynamic_names(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id in {"__import__", "compile", "eval", "exec", "open"}
    }


def _detector_source_conforms_to_pure_import_policy(source: str) -> bool:
    return _import_manifest(source) == _detector_allowed_imports() and not _forbidden_dynamic_names(
        source
    )


def test_detector_has_only_allowlisted_pure_imports_and_builtins() -> None:
    source_path = (
        Path(__file__).parents[1] / "tradingagents" / "platform" / "backtest" / "candlesticks.py"
    )
    assert _detector_source_conforms_to_pure_import_policy(source_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "payload",
    (
        "import socket as s\ns.create_connection(('example.invalid', 1))",
        "import subprocess\nsubprocess.run(['false'])",
        "from socket import create_connection\ncreate_connection(('x', 1))",
        "__import__('socket')",
        "open('/tmp/x')",
    ),
)
def test_detector_import_policy_rejects_known_ast_bypass_shapes(payload: str) -> None:
    source = (
        Path(__file__).parents[1] / "tradingagents" / "platform" / "backtest" / "candlesticks.py"
    ).read_text(encoding="utf-8")
    assert not _detector_source_conforms_to_pure_import_policy(f"{source}\n{payload}\n")


def _references_private_detector(source: str) -> bool:
    private_name = "_derive_candlestick_bundle_from_view"
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == private_name:
            return True
        if isinstance(node, ast.Name) and node.id == private_name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == private_name:
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            alias.name == private_name for alias in node.names
        ):
            return True
        if isinstance(node, ast.Constant) and node.value == private_name:
            return True
    return False


def test_private_view_detector_has_no_production_consumer_outside_authority() -> None:
    project_root = Path(__file__).parents[1]
    private_name = "_derive_candlestick_bundle_from_view"
    consumers: list[str] = []
    for root_name in ("tradingagents", "service", "cli"):
        for path in (project_root / root_name).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if private_name in source and _references_private_detector(source):
                consumers.append(path.relative_to(project_root).as_posix())
    assert sorted(consumers) == [
        "tradingagents/platform/backtest/candlesticks.py",
        "tradingagents/platform/backtest/repository.py",
    ]


@pytest.mark.parametrize(
    "source",
    (
        "from tradingagents.platform.backtest.candlesticks import "
        "_derive_candlestick_bundle_from_view",
        "import tradingagents.platform.backtest.candlesticks as c\n"
        "c._derive_candlestick_bundle_from_view(view)",
        "_derive_candlestick_bundle_from_view(view)",
        "getattr(module, '_derive_candlestick_bundle_from_view')(view)",
    ),
)
def test_private_detector_guard_catches_import_attribute_name_and_dynamic_access(
    source: str,
) -> None:
    assert _references_private_detector(source)
