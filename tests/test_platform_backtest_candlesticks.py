from __future__ import annotations

import ast
import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, Inexact, Rounded, localcontext
from pathlib import Path
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
    FrozenDatasetRepository,
    InstrumentSpec,
    MarketCalendar,
    candlestick_bundle_to_untrusted_evidence,
    canonical_json_bytes,
    derive_candlestick_bundle,
)
from tradingagents.platform.backtest.calendar import BacktestCalendar

TENANT_ID = UUID("30000000-0000-0000-0000-000000000051")
INSTRUMENT_ID = UUID("10000000-0000-0000-0000-000000000051")
CREATED_AT = datetime(2026, 2, 1, tzinfo=UTC)


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


def _view(
    root: Path,
    prices: tuple[tuple[str, str, str, str], ...],
    *,
    dataset_number: int = 1,
    decision_index: int | None = None,
    unknown_indices: frozenset[int] = frozenset(),
    split_index: int | None = None,
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
    actions: tuple[CorporateAction, ...] = ()
    if split_index is not None:
        split_day = start + timedelta(days=split_index)
        actions = (
            CorporateAction(
                action_id=UUID(f"40000000-0000-0000-0000-{dataset_number:012d}"),
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
                source_digest="2" * 64,
            ),
        )
    dataset_id = UUID(f"20000000-0000-0000-0000-{dataset_number:012d}")
    repository = FrozenDatasetRepository(root)
    manifest = repository.ingest(
        dataset_id=dataset_id,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
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
    return repository.load_eligible(
        dataset_id,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
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
    bundle = derive_candlestick_bundle(_view(tmp_path, prices))
    assert bundle.features.status is CandlestickEvaluationStatus.EVALUATED
    assert expected in {item.pattern for item in bundle.features.matches}


def test_doji_boundary_is_inclusive_and_neighbor_is_not(tmp_path: Path) -> None:
    exact = _neutral_prices()[:-1] + (("10", "10.5", "9.5", "10.1"),)
    outside = _neutral_prices()[:-1] + (("10", "10.5", "9.5", "10.1001"),)
    first = derive_candlestick_bundle(_view(tmp_path / "a", exact, dataset_number=10))
    second = derive_candlestick_bundle(_view(tmp_path / "b", outside, dataset_number=11))
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
    patterns = {
        item.pattern for item in derive_candlestick_bundle(_view(tmp_path, down)).features.matches
    }
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
    patterns = {
        item.pattern for item in derive_candlestick_bundle(_view(tmp_path, prices)).features.matches
    }
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
    bundle = derive_candlestick_bundle(_view(tmp_path, prices))
    assert bundle.features.status is status
    if status is not CandlestickEvaluationStatus.EVALUATED:
        assert bundle.features.matches == ()


def test_unknown_bar_cannot_be_bridged(tmp_path: Path) -> None:
    prices = _neutral_prices() + ((_neutral_prices()[-1]),)
    view = _view(tmp_path, prices, unknown_indices=frozenset({3}))
    bundle = derive_candlestick_bundle(view)
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
    bundle = derive_candlestick_bundle(_view(tmp_path, _neutral_prices(), split_index=split_index))
    assert bundle.features.status is expected


def test_zero_range_is_typed_and_empty(tmp_path: Path) -> None:
    prices = _neutral_prices()[:-1] + (("10", "10", "10", "10"),)
    bundle = derive_candlestick_bundle(_view(tmp_path, prices))
    assert bundle.features.status is CandlestickEvaluationStatus.ZERO_RANGE
    assert bundle.features.matches == ()


def test_future_append_changes_lineage_not_historical_features(tmp_path: Path) -> None:
    history = _neutral_prices()
    future = history + (("50", "60", "40", "55"),)
    first = derive_candlestick_bundle(_view(tmp_path / "a", history, dataset_number=20))
    second = derive_candlestick_bundle(
        _view(tmp_path / "b", future, dataset_number=21, decision_index=5)
    )
    assert canonical_json_bytes(first.features) == canonical_json_bytes(second.features)
    assert first.feature_digest == second.feature_digest
    assert first.eligible_view_sha256 != second.eligible_view_sha256
    assert first.ordinal_binding_digest != second.ordinal_binding_digest


def test_positive_price_scaling_preserves_patterns_and_ratios(tmp_path: Path) -> None:
    original = _neutral_prices()[:-1] + (("10", "10.5", "9.5", "10.05"),)
    scaled = tuple(
        tuple(str(Decimal(value) * Decimal("1000")) for value in candle) for candle in original
    )
    first = derive_candlestick_bundle(_view(tmp_path / "a", original, dataset_number=22))
    second = derive_candlestick_bundle(_view(tmp_path / "b", scaled, dataset_number=23))
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
    identity = hashlib.sha256(
        f"{low}:{lower_wick}:{body}:{upper_wick}:{bullish}".encode()
    ).hexdigest()
    view = _view(tmp_path / identity, _neutral_prices()[:-1] + (last,))
    first = derive_candlestick_bundle(view)
    second = derive_candlestick_bundle(view)
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
    view = _view(tmp_path, _neutral_prices())
    baseline = canonical_json_bytes(derive_candlestick_bundle(view).features)
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert canonical_json_bytes(derive_candlestick_bundle(view).features) == baseline


def test_volume_changes_provenance_not_geometry(tmp_path: Path) -> None:
    first = derive_candlestick_bundle(_view(tmp_path / "a", _neutral_prices(), dataset_number=30))
    second = derive_candlestick_bundle(
        _view(
            tmp_path / "b",
            _neutral_prices(),
            dataset_number=31,
            volume_overrides={5: "2000"},
        )
    )

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
    bundle = derive_candlestick_bundle(_view(tmp_path, _neutral_prices()))
    evidence = candlestick_bundle_to_untrusted_evidence(bundle)
    block = evidence.to_prompt_block()
    assert evidence.kind == "market_data"
    assert evidence.derived is True
    assert evidence.available_at == bundle.features.available_at.isoformat()
    assert evidence.ref == bundle.digest
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
    bundle = derive_candlestick_bundle(_view(tmp_path, _neutral_prices()))
    with pytest.raises(ValueError, match="feature digest"):
        CandlestickEvidenceBundle.model_validate(
            bundle.model_dump(mode="python") | {"feature_digest": "f" * 64}
        )
    with pytest.raises(ValueError, match="ordinal binding"):
        CandlestickEvidenceBundle.model_validate(
            bundle.model_dump(mode="python") | {"ordinal_binding_digest": "f" * 64}
        )


def test_detector_does_not_reenter_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _view(tmp_path, _neutral_prices())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("detector re-entered dataset authority")

    monkeypatch.setattr(FrozenDatasetRepository, "load_eligible", forbidden)
    assert derive_candlestick_bundle(view).features.status == "evaluated"


def test_fabricated_or_mutated_view_fails_closed(tmp_path: Path) -> None:
    view = _view(tmp_path, _neutral_prices())
    with pytest.raises(CandlestickEvidenceError, match="chronological|data digest"):
        derive_candlestick_bundle(replace(view, bars=view.bars[:-1] + (view.bars[-2],)))
    with pytest.raises(CandlestickEvidenceError, match="ordinals"):
        derive_candlestick_bundle(replace(view, bar_ordinals=(0, 1, 2, 3, 4, 4)))
    with pytest.raises(CandlestickEvidenceError, match="post-cutoff"):
        derive_candlestick_bundle(
            replace(view, data_cutoff_at=view.bars[-1].available_at - timedelta(seconds=1))
        )


def test_detector_has_no_io_agent_or_capital_imports() -> None:
    source_path = (
        Path(__file__).parents[1] / "tradingagents" / "platform" / "backtest" / "candlesticks.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = {
        "os",
        "pathlib",
        "requests",
        "httpx",
        "pandas",
        "numpy",
        "stockstats",
        "yfinance",
        "talib",
        "brokers",
        "portfolio",
        "agents",
        "signal_processing",
    }
    assert not any(
        any(part == item for part in name.split(".")) for name in imported for item in forbidden
    )
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not ({"now", "utcnow", "open", "getenv"} & calls)
