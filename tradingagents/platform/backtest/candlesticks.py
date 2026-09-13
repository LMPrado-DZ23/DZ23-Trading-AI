"""Pure, deterministic candlestick evidence derived from one PIT-safe view.

This module performs no I/O and has no trading side effects.  Pattern matches
are untrusted derived evidence for later analysis, never executable signals.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from tradingagents.dataflows.evidence import MAX_CONTENT_CHARS, UntrustedEvidence

from .contracts import (
    BacktestContract,
    CorporateAction,
    CorporateActionType,
    DailyBar,
    Digest,
    FxObservation,
    InstrumentSpec,
    canonical_digest,
    canonical_json_bytes,
)
from .repository import EligibleMarketView, _eligible_data_sha256

CANDLESTICK_SCHEMA_VERSION = "candlestick-evidence/1"
CANDLESTICK_DETECTOR_VERSION = "dz23-candlestick/1.0.0"
TREND_LOOKBACK = 3
MAX_CONSTITUENT_BARS = 3
REQUIRED_WINDOW_BARS = TREND_LOOKBACK + MAX_CONSTITUENT_BARS
RATIO_SCALE = 1_000_000

_POLICY = {
    "detector_version": CANDLESTICK_DETECTOR_VERSION,
    "doji_body_max_percent": 10,
    "marubozu_body_min_percent": 90,
    "marubozu_wick_max_percent": 5,
    "hammer_long_wick_body_multiple": 2,
    "hammer_opposite_wick_max_percent": 10,
    "star_middle_body_max_first_percent": 30,
    "star_gap_required": 0,
    "trend_lookback": TREND_LOOKBACK,
}
CANDLESTICK_POLICY_DIGEST = canonical_digest(_POLICY)


class CandlestickEvidenceError(ValueError):
    """Raised when a supplied eligible view is not internally trustworthy."""


class CandleDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    FLAT = "flat"


class PriorTrend(StrEnum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class CandlestickEvaluationStatus(StrEnum):
    EVALUATED = "evaluated"
    INSUFFICIENT_HISTORY = "insufficient_history"
    DISCONTINUOUS_HISTORY = "discontinuous_history"
    SPLIT_BARRIER = "split_barrier"
    ZERO_RANGE = "zero_range"


class CandlestickPattern(StrEnum):
    DOJI = "doji"
    MARUBOZU_BULLISH = "marubozu_bullish"
    MARUBOZU_BEARISH = "marubozu_bearish"
    ENGULFING_BULLISH = "engulfing_bullish"
    ENGULFING_BEARISH = "engulfing_bearish"
    HAMMER = "hammer"
    HANGING_MAN = "hanging_man"
    INVERTED_HAMMER = "inverted_hammer"
    SHOOTING_STAR = "shooting_star"
    MORNING_STAR = "morning_star"
    EVENING_STAR = "evening_star"


_PATTERN_ORDER = {pattern: index for index, pattern in enumerate(CandlestickPattern)}
_PATTERN_ARITY = {
    CandlestickPattern.DOJI: 1,
    CandlestickPattern.MARUBOZU_BULLISH: 1,
    CandlestickPattern.MARUBOZU_BEARISH: 1,
    CandlestickPattern.HAMMER: 1,
    CandlestickPattern.HANGING_MAN: 1,
    CandlestickPattern.INVERTED_HAMMER: 1,
    CandlestickPattern.SHOOTING_STAR: 1,
    CandlestickPattern.ENGULFING_BULLISH: 2,
    CandlestickPattern.ENGULFING_BEARISH: 2,
    CandlestickPattern.MORNING_STAR: 3,
    CandlestickPattern.EVENING_STAR: 3,
}


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise ValueError("candlestick timestamps must use UTC")
    return value.astimezone(UTC)


class CandleGeometry(BacktestContract):
    session_date: date
    parent_bar_digest: Digest
    direction: CandleDirection
    body_to_range_ppm: int = Field(ge=0, le=RATIO_SCALE)
    upper_wick_to_range_ppm: int = Field(ge=0, le=RATIO_SCALE)
    lower_wick_to_range_ppm: int = Field(ge=0, le=RATIO_SCALE)

    @model_validator(mode="after")
    def validate_ratio_partition(self):
        total = self.body_to_range_ppm + self.upper_wick_to_range_ppm + self.lower_wick_to_range_ppm
        if abs(total - RATIO_SCALE) > 1:
            raise ValueError("candlestick ratios do not partition the candle range")
        return self


class CandlestickMatch(BacktestContract):
    pattern: CandlestickPattern
    constituent_session_dates: tuple[date, ...]
    constituent_bar_digests: tuple[Digest, ...]
    prior_trend: PriorTrend
    trend_session_dates: tuple[date, ...]
    trend_bar_digests: tuple[Digest, ...]
    geometry: tuple[CandleGeometry, ...]
    available_at: datetime

    _available_at_is_utc = field_validator("available_at")(_utc_timestamp)

    @model_validator(mode="after")
    def validate_match_shape(self):
        size = len(self.constituent_session_dates)
        if size < 1 or size > MAX_CONSTITUENT_BARS:
            raise ValueError("candlestick matches require one to three constituents")
        if size != _PATTERN_ARITY[self.pattern]:
            raise ValueError("candlestick pattern arity does not match its constituents")
        if len(self.constituent_bar_digests) != size or len(self.geometry) != size:
            raise ValueError("candlestick constituent fields must have equal lengths")
        if len(self.trend_session_dates) != TREND_LOOKBACK:
            raise ValueError("candlestick prior trend requires the fixed lookback")
        if len(self.trend_bar_digests) != TREND_LOOKBACK:
            raise ValueError("candlestick trend fields must have equal lengths")
        if tuple(sorted(self.trend_session_dates)) != self.trend_session_dates or len(
            set(self.trend_session_dates)
        ) != len(self.trend_session_dates):
            raise ValueError("candlestick trend sessions must be strictly chronological")
        if tuple(sorted(self.constituent_session_dates)) != self.constituent_session_dates or len(
            set(self.constituent_session_dates)
        ) != len(self.constituent_session_dates):
            raise ValueError("candlestick constituent sessions must be strictly chronological")
        if self.trend_session_dates[-1] >= self.constituent_session_dates[0]:
            raise ValueError("candlestick trend must precede every constituent")
        if tuple(item.session_date for item in self.geometry) != self.constituent_session_dates:
            raise ValueError("candlestick geometry sessions do not match constituents")
        if tuple(item.parent_bar_digest for item in self.geometry) != self.constituent_bar_digests:
            raise ValueError("candlestick geometry digests do not match constituents")
        return self


class CandlestickFeatureSet(BacktestContract):
    schema_version: Literal["candlestick-evidence/1"] = CANDLESTICK_SCHEMA_VERSION
    detector_version: Literal["dz23-candlestick/1.0.0"] = CANDLESTICK_DETECTOR_VERSION
    policy_digest: Digest = CANDLESTICK_POLICY_DIGEST
    instrument_id: UUID
    eligible_data_sha256: Digest
    decision_session_date: date
    data_cutoff_at: datetime
    decision_at: datetime
    available_at: datetime
    status: CandlestickEvaluationStatus
    parent_bar_ordinals: tuple[int, ...]
    parent_bar_digests: tuple[Digest, ...]
    split_action_count: int = Field(ge=0, le=25_000)
    latest_split_effective_date: date | None
    split_context_digest: Digest | None
    matches: tuple[CandlestickMatch, ...]

    _timestamps_are_utc = field_validator("data_cutoff_at", "decision_at", "available_at")(
        _utc_timestamp
    )

    @model_validator(mode="after")
    def validate_feature_set(self):
        if self.data_cutoff_at > self.decision_at:
            raise ValueError("candlestick data cutoff cannot follow the decision")
        if self.available_at > self.data_cutoff_at:
            raise ValueError("candlestick parent availability cannot follow the data cutoff")
        if self.policy_digest != CANDLESTICK_POLICY_DIGEST:
            raise ValueError("candlestick policy digest is not the fixed detector policy")
        if len(self.parent_bar_ordinals) != len(self.parent_bar_digests):
            raise ValueError("candlestick parent ordinals and digests must align")
        if len(self.parent_bar_ordinals) > REQUIRED_WINDOW_BARS:
            raise ValueError("candlestick parent window exceeds its fixed bound")
        if any(type(item) is not int or item < 0 for item in self.parent_bar_ordinals):
            raise ValueError("candlestick parent ordinals are invalid")
        if any(
            right <= left
            for left, right in zip(
                self.parent_bar_ordinals,
                self.parent_bar_ordinals[1:],
                strict=False,
            )
        ):
            raise ValueError("candlestick parent ordinals must be strictly increasing")
        if self.status is CandlestickEvaluationStatus.INSUFFICIENT_HISTORY:
            if len(self.parent_bar_ordinals) >= REQUIRED_WINDOW_BARS:
                raise ValueError("insufficient candlestick history cannot contain a full window")
        elif len(self.parent_bar_ordinals) != REQUIRED_WINDOW_BARS:
            raise ValueError("candlestick evaluation statuses require the fixed full window")
        if self.status is not CandlestickEvaluationStatus.EVALUATED and self.matches:
            raise ValueError("non-evaluated candlestick evidence cannot contain matches")
        if self.split_action_count == 0:
            if (
                self.latest_split_effective_date is not None
                or self.split_context_digest is not None
            ):
                raise ValueError("empty split context cannot carry split provenance")
        elif self.latest_split_effective_date is None or self.split_context_digest is None:
            raise ValueError("non-empty split context requires bounded provenance")
        if any(item.available_at > self.available_at for item in self.matches):
            raise ValueError("candlestick match availability exceeds its feature parents")
        ordered = tuple(
            sorted(
                self.matches,
                key=lambda item: (
                    _PATTERN_ORDER[item.pattern],
                    item.constituent_session_dates,
                    item.constituent_bar_digests,
                ),
            )
        )
        if self.matches != ordered or len({item.digest for item in self.matches}) != len(
            self.matches
        ):
            raise ValueError("candlestick matches must be canonical and unique")
        if any(
            item.constituent_session_dates[-1] != self.decision_session_date
            for item in self.matches
        ):
            raise ValueError("candlestick matches must end on the decision session")
        return self


class CandlestickEvidenceBundle(BacktestContract):
    tenant_id: UUID
    dataset_id: UUID
    manifest_digest: Digest
    binding_digest: Digest
    full_content_sha256: Digest
    eligible_view_sha256: Digest
    ordinal_binding_digest: Digest
    feature_digest: Digest
    features: CandlestickFeatureSet

    @model_validator(mode="after")
    def validate_feature_binding(self):
        if not hmac.compare_digest(self.feature_digest, self.features.digest):
            raise ValueError("candlestick feature digest does not match the feature set")
        expected_ordinal_binding = canonical_digest(
            {
                "binding_version": "candlestick-ordinals/1",
                "detector_version": self.features.detector_version,
                "eligible_data_sha256": self.features.eligible_data_sha256,
                "full_content_sha256": self.full_content_sha256,
                "parent_bar_digests": self.features.parent_bar_digests,
                "parent_bar_ordinals": self.features.parent_bar_ordinals,
            }
        )
        if not hmac.compare_digest(self.ordinal_binding_digest, expected_ordinal_binding):
            raise ValueError("candlestick ordinal binding does not match its parents")
        return self


def _view_authority_payload(
    view: EligibleMarketView, eligible_data_sha256: Digest
) -> dict[str, object]:
    return {
        "binding_digest": view.binding_digest,
        "dataset_id": view.dataset_id,
        "eligible_data_sha256": eligible_data_sha256,
        "limitations": view.limitations,
        "manifest_digest": view.manifest_digest,
        "source_vintage": view.source_vintage,
        "tenant_id": view.tenant_id,
    }


def _validate_view(view: EligibleMarketView) -> None:
    if type(view) is not EligibleMarketView:
        raise CandlestickEvidenceError("candlestick derivation requires an exact eligible view")
    if type(view.instrument) is not InstrumentSpec:
        raise CandlestickEvidenceError("eligible-view instrument contract is invalid")
    if view.decision_at.tzinfo is None or view.decision_at.utcoffset() != UTC.utcoffset(None):
        raise CandlestickEvidenceError("eligible-view decision timestamp must use UTC")
    if view.data_cutoff_at.tzinfo is None or view.data_cutoff_at.utcoffset() != UTC.utcoffset(None):
        raise CandlestickEvidenceError("eligible-view data cutoff must use UTC")
    if view.data_cutoff_at > view.decision_at:
        raise CandlestickEvidenceError("eligible-view data cutoff follows the decision")
    if view.instrument.available_at > view.data_cutoff_at:
        raise CandlestickEvidenceError("eligible-view instrument is not available at the cutoff")
    if not view.bars or len(view.bars) != len(view.bar_ordinals):
        raise CandlestickEvidenceError("eligible-view bars and ordinals must align")
    if any(type(value) is not int or value < 0 for value in view.bar_ordinals):
        raise CandlestickEvidenceError("eligible-view bar ordinals are invalid")
    if any(
        right <= left for left, right in zip(view.bar_ordinals, view.bar_ordinals[1:], strict=False)
    ):
        raise CandlestickEvidenceError("eligible-view bar ordinals are not strictly increasing")
    previous_date: date | None = None
    for bar in view.bars:
        if type(bar) is not DailyBar or bar.instrument_id != view.instrument.instrument_id:
            raise CandlestickEvidenceError("eligible-view contains an invalid bar identity")
        if previous_date is not None and bar.session_date <= previous_date:
            raise CandlestickEvidenceError("eligible-view bars are not strictly chronological")
        if (
            bar.session_date > view.decision_session_date
            or bar.available_at is None
            or bar.available_at > view.data_cutoff_at
        ):
            raise CandlestickEvidenceError("eligible-view contains post-cutoff bar data")
        previous_date = bar.session_date
    if view.bars[-1].session_date != view.decision_session_date:
        raise CandlestickEvidenceError("eligible-view does not end on the decision session")
    expected_action_order = tuple(
        sorted(
            view.corporate_actions,
            key=lambda item: (item.effective_date, str(item.action_id)),
        )
    )
    if view.corporate_actions != expected_action_order:
        raise CandlestickEvidenceError("eligible-view corporate actions are not canonical")
    if any(
        type(item) is not CorporateAction
        or item.instrument_id != view.instrument.instrument_id
        or item.available_at > view.data_cutoff_at
        for item in view.corporate_actions
    ):
        raise CandlestickEvidenceError("eligible-view contains an invalid corporate action")
    if len({item.action_id for item in view.corporate_actions}) != len(view.corporate_actions):
        raise CandlestickEvidenceError("eligible-view contains duplicate corporate actions")
    if any(
        type(item) is not FxObservation or item.available_at > view.data_cutoff_at
        for item in view.fx_observations
    ):
        raise CandlestickEvidenceError("eligible-view contains an invalid FX observation")
    expected_fx_order = tuple(
        sorted(
            view.fx_observations,
            key=lambda item: (
                item.observed_at.isoformat(),
                item.base_currency,
                item.quote_currency,
                str(item.observation_id),
            ),
        )
    )
    if view.fx_observations != expected_fx_order:
        raise CandlestickEvidenceError("eligible-view FX observations are not canonical")
    fx_keys = {item.observation_id for item in view.fx_observations}
    if len(fx_keys) != len(view.fx_observations):
        raise CandlestickEvidenceError("eligible-view contains duplicate FX observations")
    data_digest = _eligible_data_sha256(
        data_cutoff_at=view.data_cutoff_at,
        decision_at=view.decision_at,
        decision_session_date=view.decision_session_date,
        instrument=view.instrument,
        bars=view.bars,
        corporate_actions=view.corporate_actions,
        fx_observations=view.fx_observations,
    )
    authority = _view_authority_payload(view, data_digest)
    view_digest = hashlib.sha256(canonical_json_bytes(authority)).hexdigest()
    if not hmac.compare_digest(data_digest, view.eligible_data_sha256):
        raise CandlestickEvidenceError("eligible-view data digest does not match its records")
    if not hmac.compare_digest(view_digest, view.eligible_view_sha256):
        raise CandlestickEvidenceError("eligible-view authority digest does not match its lineage")


def _direction(bar: DailyBar) -> CandleDirection:
    if bar.close > bar.open:
        return CandleDirection.BULLISH
    if bar.close < bar.open:
        return CandleDirection.BEARISH
    return CandleDirection.FLAT


def _parts(bar: DailyBar) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    body = abs(bar.close - bar.open)
    upper = bar.high - max(bar.open, bar.close)
    lower = min(bar.open, bar.close) - bar.low
    return body, upper, lower, bar.high - bar.low


def _ratio_ppm(part: Decimal, whole: Decimal) -> int:
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        return int(
            (part * Decimal(RATIO_SCALE) / whole).to_integral_value(rounding=ROUND_HALF_EVEN)
        )


def _geometry(bar: DailyBar) -> CandleGeometry:
    body, upper, lower, full_range = _parts(bar)
    return CandleGeometry(
        session_date=bar.session_date,
        parent_bar_digest=bar.digest,
        direction=_direction(bar),
        body_to_range_ppm=_ratio_ppm(body, full_range),
        upper_wick_to_range_ppm=_ratio_ppm(upper, full_range),
        lower_wick_to_range_ppm=_ratio_ppm(lower, full_range),
    )


def _trend(bars: tuple[DailyBar, ...]) -> PriorTrend:
    closes = tuple(item.close for item in bars)
    if closes[0] < closes[1] < closes[2]:
        return PriorTrend.UP
    if closes[0] > closes[1] > closes[2]:
        return PriorTrend.DOWN
    return PriorTrend.FLAT


def _match(
    pattern: CandlestickPattern,
    constituents: tuple[DailyBar, ...],
    trend_bars: tuple[DailyBar, ...],
) -> CandlestickMatch:
    parents = trend_bars + constituents
    available_at = max(item.available_at for item in parents if item.available_at is not None)
    return CandlestickMatch(
        pattern=pattern,
        constituent_session_dates=tuple(item.session_date for item in constituents),
        constituent_bar_digests=tuple(item.digest for item in constituents),
        prior_trend=_trend(trend_bars),
        trend_session_dates=tuple(item.session_date for item in trend_bars),
        trend_bar_digests=tuple(item.digest for item in trend_bars),
        geometry=tuple(_geometry(item) for item in constituents),
        available_at=available_at,
    )


def _single_patterns(bar: DailyBar, trend: PriorTrend) -> tuple[CandlestickPattern, ...]:
    body, upper, lower, full_range = _parts(bar)
    direction = _direction(bar)
    result: list[CandlestickPattern] = []
    if body * 100 <= full_range * 10:
        result.append(CandlestickPattern.DOJI)
    if direction is not CandleDirection.FLAT and (
        body * 100 >= full_range * 90
        and upper * 100 <= full_range * 5
        and lower * 100 <= full_range * 5
    ):
        result.append(
            CandlestickPattern.MARUBOZU_BULLISH
            if direction is CandleDirection.BULLISH
            else CandlestickPattern.MARUBOZU_BEARISH
        )
    if body > 0 and lower >= body * 2 and upper * 100 <= full_range * 10:
        if trend is PriorTrend.DOWN:
            result.append(CandlestickPattern.HAMMER)
        elif trend is PriorTrend.UP:
            result.append(CandlestickPattern.HANGING_MAN)
    if body > 0 and upper >= body * 2 and lower * 100 <= full_range * 10:
        if trend is PriorTrend.DOWN:
            result.append(CandlestickPattern.INVERTED_HAMMER)
        elif trend is PriorTrend.UP:
            result.append(CandlestickPattern.SHOOTING_STAR)
    return tuple(result)


def _engulfing_pattern(
    previous: DailyBar,
    current: DailyBar,
    trend: PriorTrend,
) -> CandlestickPattern | None:
    previous_direction = _direction(previous)
    current_direction = _direction(current)
    if (
        trend is PriorTrend.DOWN
        and previous_direction is CandleDirection.BEARISH
        and current_direction is CandleDirection.BULLISH
        and current.open <= previous.close
        and current.close >= previous.open
        and (current.open < previous.close or current.close > previous.open)
    ):
        return CandlestickPattern.ENGULFING_BULLISH
    if (
        trend is PriorTrend.UP
        and previous_direction is CandleDirection.BULLISH
        and current_direction is CandleDirection.BEARISH
        and current.open >= previous.close
        and current.close <= previous.open
        and (current.open > previous.close or current.close < previous.open)
    ):
        return CandlestickPattern.ENGULFING_BEARISH
    return None


def _star_pattern(
    first: DailyBar,
    middle: DailyBar,
    last: DailyBar,
    trend: PriorTrend,
) -> CandlestickPattern | None:
    first_body = abs(first.close - first.open)
    middle_body = abs(middle.close - middle.open)
    midpoint_sum = first.open + first.close
    if first_body == 0 or middle_body * 100 > first_body * 30:
        return None
    if (
        trend is PriorTrend.DOWN
        and _direction(first) is CandleDirection.BEARISH
        and _direction(last) is CandleDirection.BULLISH
        and last.close * 2 >= midpoint_sum
    ):
        return CandlestickPattern.MORNING_STAR
    if (
        trend is PriorTrend.UP
        and _direction(first) is CandleDirection.BULLISH
        and _direction(last) is CandleDirection.BEARISH
        and last.close * 2 <= midpoint_sum
    ):
        return CandlestickPattern.EVENING_STAR
    return None


def _split_context_digest(actions: tuple[CorporateAction, ...]) -> Digest | None:
    """Bind an arbitrarily large, ordered split history without materializing it."""

    if not actions:
        return None
    digest = hashlib.sha256()
    digest.update(canonical_json_bytes({"binding_version": "candlestick-split-context/1"}) + b"\n")
    for action in actions:
        digest.update(
            canonical_json_bytes(
                {
                    "action_digest": action.digest,
                    "effective_date": action.effective_date,
                }
            )
            + b"\n"
        )
    return digest.hexdigest()


def _derive_candlestick_bundle_from_view(
    view: EligibleMarketView,
) -> CandlestickEvidenceBundle:
    """Derive bounded candle geometry from a verified decision-time view."""

    _validate_view(view)
    parent_split_actions = tuple(
        item
        for item in view.corporate_actions
        if item.action_type is CorporateActionType.SPLIT
        and item.effective_date <= view.decision_session_date
    )
    latest_split_effective_date = max(
        (item.effective_date for item in parent_split_actions),
        default=None,
    )
    split_context_digest = _split_context_digest(parent_split_actions)
    status = CandlestickEvaluationStatus.EVALUATED
    matches: tuple[CandlestickMatch, ...] = ()
    parent_ordinals = view.bar_ordinals[-REQUIRED_WINDOW_BARS:]
    window = view.bars[-REQUIRED_WINDOW_BARS:]
    if len(window) < REQUIRED_WINDOW_BARS:
        status = CandlestickEvaluationStatus.INSUFFICIENT_HISTORY
    elif any(
        right != left + 1 for left, right in zip(parent_ordinals, parent_ordinals[1:], strict=False)
    ):
        status = CandlestickEvaluationStatus.DISCONTINUOUS_HISTORY
    elif (
        latest_split_effective_date is not None
        and window[0].session_date < latest_split_effective_date <= view.decision_session_date
    ):
        status = CandlestickEvaluationStatus.SPLIT_BARRIER
    elif any(item.high == item.low for item in window[-MAX_CONSTITUENT_BARS:]):
        status = CandlestickEvaluationStatus.ZERO_RANGE
    else:
        with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
            found: list[CandlestickMatch] = []
            one_trend = window[-4:-1]
            one = window[-1:]
            for pattern in _single_patterns(one[0], _trend(one_trend)):
                found.append(_match(pattern, one, one_trend))
            two_trend = window[-5:-2]
            two = window[-2:]
            engulfing = _engulfing_pattern(two[0], two[1], _trend(two_trend))
            if engulfing is not None:
                found.append(_match(engulfing, two, two_trend))
            three_trend = window[-6:-3]
            three = window[-3:]
            star = _star_pattern(three[0], three[1], three[2], _trend(three_trend))
            if star is not None:
                found.append(_match(star, three, three_trend))
        matches = tuple(
            sorted(
                found,
                key=lambda item: (
                    _PATTERN_ORDER[item.pattern],
                    item.constituent_session_dates,
                    item.constituent_bar_digests,
                ),
            )
        )

    parent_available_at = max(
        tuple(item.available_at for item in window)
        + tuple(item.available_at for item in parent_split_actions),
        default=view.data_cutoff_at,
    )
    features = CandlestickFeatureSet(
        instrument_id=view.instrument.instrument_id,
        eligible_data_sha256=view.eligible_data_sha256,
        decision_session_date=view.decision_session_date,
        data_cutoff_at=view.data_cutoff_at,
        decision_at=view.decision_at,
        available_at=parent_available_at,
        status=status,
        parent_bar_ordinals=parent_ordinals,
        parent_bar_digests=tuple(item.digest for item in window),
        split_action_count=len(parent_split_actions),
        latest_split_effective_date=latest_split_effective_date,
        split_context_digest=split_context_digest,
        matches=matches,
    )
    ordinal_binding_digest = canonical_digest(
        {
            "binding_version": "candlestick-ordinals/1",
            "detector_version": CANDLESTICK_DETECTOR_VERSION,
            "eligible_data_sha256": view.eligible_data_sha256,
            "full_content_sha256": view.full_content_sha256,
            "parent_bar_digests": tuple(item.digest for item in window),
            "parent_bar_ordinals": parent_ordinals,
        }
    )
    return CandlestickEvidenceBundle(
        tenant_id=view.tenant_id,
        dataset_id=view.dataset_id,
        manifest_digest=view.manifest_digest,
        binding_digest=view.binding_digest,
        full_content_sha256=view.full_content_sha256,
        eligible_view_sha256=view.eligible_view_sha256,
        ordinal_binding_digest=ordinal_binding_digest,
        feature_digest=features.digest,
        features=features,
    )


def candlestick_bundle_to_untrusted_evidence(
    bundle: CandlestickEvidenceBundle,
) -> UntrustedEvidence:
    """Serialize deterministic features for a data/user prompt boundary."""

    if type(bundle) is not CandlestickEvidenceBundle:
        raise CandlestickEvidenceError("candlestick evidence requires an exact bundle")
    validated = CandlestickEvidenceBundle.model_validate(bundle.model_dump(mode="python"))
    content = canonical_json_bytes(validated.features).decode("ascii")
    if len(content) > MAX_CONTENT_CHARS:
        raise CandlestickEvidenceError("candlestick feature JSON exceeds the evidence limit")
    evidence = UntrustedEvidence(
        source=f"backtest:candlestick/{CANDLESTICK_DETECTOR_VERSION}",
        kind="market_data",
        content=content,
        fetched_at=validated.features.decision_at.astimezone(UTC).isoformat(),
        available_at=validated.features.available_at.astimezone(UTC).isoformat(),
        as_of=validated.features.decision_session_date.isoformat(),
        ref=validated.digest,
        derived=True,
    )
    if evidence.content != content or not hmac.compare_digest(
        evidence.digest or "", validated.feature_digest
    ):
        raise CandlestickEvidenceError("candlestick feature JSON changed at the evidence boundary")
    return evidence


__all__ = [
    "CANDLESTICK_DETECTOR_VERSION",
    "CANDLESTICK_POLICY_DIGEST",
    "CANDLESTICK_SCHEMA_VERSION",
    "CandleDirection",
    "CandleGeometry",
    "CandlestickEvaluationStatus",
    "CandlestickEvidenceBundle",
    "CandlestickEvidenceError",
    "CandlestickFeatureSet",
    "CandlestickMatch",
    "CandlestickPattern",
    "PriorTrend",
    "candlestick_bundle_to_untrusted_evidence",
]
