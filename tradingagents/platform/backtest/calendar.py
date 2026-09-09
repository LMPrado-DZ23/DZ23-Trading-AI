"""Deterministic exchange-session rules for point-in-time backtests."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import exchange_calendars as xcals

from .contracts import AvailabilityBasis, DailyBar, InstrumentSpec
from .errors import MarketCalendarError

SUPPORTED_CALENDARS = frozenset({"24/7", "XNYS", "BVMF"})
DEFAULT_DAILY_BAR_LAG = timedelta(minutes=15)
MAX_SESSION_RANGE_DAYS = 25_000
SETTLEMENT_POLICY_VERSION = "daily-bar-settlement/1"


def calendar_provenance() -> tuple[str, str, str]:
    """Return the engine versions bound while a schedule is ingested."""

    return (
        f"exchange-calendars/{importlib.metadata.version('exchange-calendars')}",
        f"tzdata/{importlib.metadata.version('tzdata')}",
        SETTLEMENT_POLICY_VERSION,
    )


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """The authoritative UTC bounds of one labelled exchange session."""

    session_date: date
    open_at: datetime
    close_at: datetime


class BacktestCalendar:
    """Pure session calculations with no wall-clock or vendor dependency."""

    def __init__(
        self,
        market_calendar: str,
        *,
        daily_bar_lag: timedelta = DEFAULT_DAILY_BAR_LAG,
    ) -> None:
        if market_calendar not in SUPPORTED_CALENDARS:
            raise MarketCalendarError("market calendar is not supported")
        if daily_bar_lag < timedelta(0) or daily_bar_lag > timedelta(days=1):
            raise MarketCalendarError("daily bar settlement lag is outside policy bounds")
        self.name = market_calendar
        self.daily_bar_lag = daily_bar_lag
        self._calendar = xcals.get_calendar(market_calendar)

    @staticmethod
    def _utc(value: object) -> datetime:
        converted = value.to_pydatetime()  # exchange-calendars returns pandas Timestamp
        if converted.tzinfo is None:
            raise MarketCalendarError("calendar returned a timezone-naive timestamp")
        return converted.astimezone(UTC)

    def session_window(self, session_date: date) -> SessionWindow:
        try:
            open_at = self._utc(self._calendar.session_open(session_date.isoformat()))
            close_at = self._utc(self._calendar.session_close(session_date.isoformat()))
        except (KeyError, ValueError) as exc:
            raise MarketCalendarError("date is not a session in the selected calendar") from exc
        return SessionWindow(session_date=session_date, open_at=open_at, close_at=close_at)

    def settlement_policy_available_at(self, session_date: date) -> datetime:
        """Return the policy minimum, never a claim of vendor availability."""

        return self.session_window(session_date).close_at + self.daily_bar_lag

    def session_dates_in_range(self, start: date, end: date) -> tuple[date, ...]:
        """Materialize every labelled session between two inclusive endpoints."""

        if end < start:
            raise MarketCalendarError("session range end precedes its start")
        if (end - start).days > MAX_SESSION_RANGE_DAYS:
            raise MarketCalendarError("session range exceeds the operational limit")
        try:
            sessions = self._calendar.sessions_in_range(start.isoformat(), end.isoformat())
        except (KeyError, ValueError) as exc:
            raise MarketCalendarError("calendar could not materialize the session range") from exc
        result = tuple(item.date() for item in sessions)
        if not result or result[0] != start or result[-1] != end:
            raise MarketCalendarError("dataset endpoints are not valid exchange sessions")
        return result

    def next_session_open(self, session_date: date) -> datetime:
        """Return the next labelled session's open, independent of bar availability."""

        try:
            next_session = self._calendar.next_session(session_date.isoformat())
            return self._utc(self._calendar.session_open(next_session))
        except (KeyError, ValueError) as exc:
            raise MarketCalendarError("calendar has no next session") from exc

    def next_open_after(self, available_at: datetime) -> datetime:
        """Return the first session open strictly after evidence is available.

        This distinction matters for continuous markets: a 24/7 session's next
        open can coincide with the preceding close and therefore occur before a
        completed daily bar has passed its settlement lag.
        """

        if available_at.tzinfo is None or available_at.utcoffset() is None:
            raise MarketCalendarError("availability timestamp must include a timezone")
        cutoff = available_at.astimezone(UTC)
        local_date = cutoff.astimezone(self._calendar.tz).date()
        try:
            session = self._calendar.date_to_session(local_date, direction="next")
            candidate = self._utc(self._calendar.session_open(session))
            if candidate <= cutoff:
                session = self._calendar.next_session(session)
                candidate = self._utc(self._calendar.session_open(session))
        except (KeyError, ValueError) as exc:
            raise MarketCalendarError("calendar has no open after the availability timestamp") from exc
        if candidate <= cutoff:
            raise MarketCalendarError("next-open calculation did not advance past availability")
        return candidate

    def validate_bar(self, instrument: InstrumentSpec, bar: DailyBar) -> None:
        if instrument.market_calendar != self.name:
            raise MarketCalendarError("instrument uses a different market calendar")
        if bar.instrument_id != instrument.instrument_id:
            raise MarketCalendarError("bar belongs to a different instrument")
        if bar.session_date < instrument.valid_from:
            raise MarketCalendarError("bar predates the instrument listing")
        if instrument.valid_to is not None and bar.session_date > instrument.valid_to:
            raise MarketCalendarError("bar follows the instrument delisting")
        expected = self.session_window(bar.session_date)
        if bar.session_open_at != expected.open_at or bar.session_close_at != expected.close_at:
            raise MarketCalendarError("bar timestamps do not match the frozen exchange schedule")
        minimum_available_at = expected.close_at + self.daily_bar_lag
        if bar.availability_basis is AvailabilityBasis.UNKNOWN:
            if bar.available_at is not None:
                raise MarketCalendarError("unknown bar availability must remain unset")
        elif bar.availability_basis is AvailabilityBasis.EXCHANGE_SETTLEMENT_POLICY:
            if bar.available_at != minimum_available_at:
                raise MarketCalendarError(
                    "bar availability does not match the configured settlement lag"
                )
        elif bar.available_at is None or bar.available_at < minimum_available_at:
            raise MarketCalendarError("vendor bar availability precedes the settlement policy")


def schedule_digest(
    instrument: InstrumentSpec,
    bars: tuple[DailyBar, ...],
    *,
    calendar_engine_version: str | None = None,
    timezone_database_version: str | None = None,
    settlement_policy_version: str | None = None,
) -> str:
    """Digest the materialized schedule, not the currently installed calendar.

    Replay therefore remains bound to the exact timestamps ingested even if an
    exchange-calendar package later changes a historical holiday definition.
    """

    if (
        calendar_engine_version is None
        or timezone_database_version is None
        or settlement_policy_version is None
    ):
        current_engine, current_timezone, current_policy = calendar_provenance()
        calendar_engine_version = calendar_engine_version or current_engine
        timezone_database_version = timezone_database_version or current_timezone
        settlement_policy_version = settlement_policy_version or current_policy
    payload = {
        "calendar_engine_version": calendar_engine_version,
        "instrument_id": str(instrument.instrument_id),
        "market_calendar": instrument.market_calendar,
        "settlement_policy_version": settlement_policy_version,
        "sessions": [
            {
                "session_date": bar.session_date.isoformat(),
                "session_open_at": bar.session_open_at.astimezone(UTC).isoformat(),
                "session_close_at": bar.session_close_at.astimezone(UTC).isoformat(),
                "available_at": (
                    bar.available_at.astimezone(UTC).isoformat()
                    if bar.available_at is not None
                    else None
                ),
                "availability_basis": bar.availability_basis,
                "source_published_at": (
                    bar.source_published_at.astimezone(UTC).isoformat()
                    if bar.source_published_at is not None
                    else None
                ),
                "fetched_at": bar.fetched_at.astimezone(UTC).isoformat(),
            }
            for bar in bars
        ],
        "timezone_database_version": timezone_database_version,
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
