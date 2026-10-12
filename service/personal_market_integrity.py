"""Fail-closed quote validation for research/paper; never places orders."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum


class QuoteFailure(str, Enum):
    INVALID_SOURCE = "INVALID_SOURCE"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    STALE_QUOTE = "STALE_QUOTE"
    INVALID_PRICE = "INVALID_PRICE"
    CROSSED_BOOK = "CROSSED_BOOK"
    EXCESSIVE_SPREAD = "EXCESSIVE_SPREAD"


@dataclass(frozen=True)
class Quote:
    exchange: str
    symbol: str
    exchange_time: datetime
    received_time: datetime
    bid: Decimal
    ask: Decimal
    source: str


@dataclass(frozen=True)
class QuoteGatePolicy:
    max_age: timedelta = timedelta(seconds=10)
    max_future_skew: timedelta = timedelta(seconds=2)
    max_spread_bps: Decimal = Decimal("100")


@dataclass(frozen=True)
class QuoteCheck:
    eligible: bool
    reasons: tuple[QuoteFailure, ...]
    spread_bps: Decimal | None


def _valid_utc(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _price(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    return number


_DEFAULT_POLICY = QuoteGatePolicy()


def check_quote(quote: Quote, now: datetime, policy: QuoteGatePolicy | None = None) -> QuoteCheck:
    """Validate inputs only; passing this check never authorizes a trade."""
    if policy is None:
        policy = _DEFAULT_POLICY
    reasons: list[QuoteFailure] = []
    if not isinstance(quote.exchange, str) or not quote.exchange.strip() or not isinstance(quote.source, str) or not quote.source.strip():
        reasons.append(QuoteFailure.INVALID_SOURCE)
    if not isinstance(quote.symbol, str) or not quote.symbol.strip():
        reasons.append(QuoteFailure.INVALID_SYMBOL)
    if not all(_valid_utc(t) for t in (now, quote.exchange_time, quote.received_time)):
        reasons.append(QuoteFailure.INVALID_TIMESTAMP)
    else:
        now_utc = now.astimezone(timezone.utc)
        sent = quote.exchange_time.astimezone(timezone.utc)
        received = quote.received_time.astimezone(timezone.utc)
        if sent > now_utc + policy.max_future_skew or received > now_utc + policy.max_future_skew or sent > received + policy.max_future_skew:
            reasons.append(QuoteFailure.FUTURE_TIMESTAMP)
        if now_utc - sent > policy.max_age or now_utc - received > policy.max_age:
            reasons.append(QuoteFailure.STALE_QUOTE)
    bid, ask = _price(quote.bid), _price(quote.ask)
    spread: Decimal | None = None
    if bid is None or ask is None:
        reasons.append(QuoteFailure.INVALID_PRICE)
    elif bid > ask:
        reasons.append(QuoteFailure.CROSSED_BOOK)
    else:
        spread = (ask - bid) * Decimal(10000) / ((ask + bid) / 2)
        if spread > policy.max_spread_bps:
            reasons.append(QuoteFailure.EXCESSIVE_SPREAD)
    return QuoteCheck(not reasons, tuple(reasons), spread)
