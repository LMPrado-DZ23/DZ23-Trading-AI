from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace

from service.personal_market_integrity import Quote, QuoteFailure, QuoteGatePolicy, check_quote

NOW = datetime(2026, 10, 12, 20, tzinfo=timezone.utc)
BASE = Quote("binance", "BTC/USDT", NOW - timedelta(milliseconds=100), NOW, Decimal("100"), Decimal("100.1"), "binance:bookTicker")


def test_fresh_quote_eligible():
    result = check_quote(BASE, NOW)
    assert result.eligible and result.reasons == () and result.spread_bps is not None


def test_stale_quote_rejected():
    assert QuoteFailure.STALE_QUOTE in check_quote(replace(BASE, exchange_time=NOW - timedelta(seconds=20)), NOW).reasons


def test_future_quote_rejected():
    assert QuoteFailure.FUTURE_TIMESTAMP in check_quote(replace(BASE, exchange_time=NOW + timedelta(seconds=5)), NOW).reasons


def test_naive_datetime_rejected():
    assert QuoteFailure.INVALID_TIMESTAMP in check_quote(replace(BASE, received_time=NOW.replace(tzinfo=None)), NOW).reasons


def test_crossed_book_rejected():
    assert QuoteFailure.CROSSED_BOOK in check_quote(replace(BASE, bid=Decimal("101"), ask=Decimal("100")), NOW).reasons


def test_invalid_prices_rejected():
    for price in [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")]:
        assert QuoteFailure.INVALID_PRICE in check_quote(replace(BASE, bid=price), NOW).reasons


def test_large_spread_rejected():
    assert QuoteFailure.EXCESSIVE_SPREAD in check_quote(replace(BASE, ask=Decimal("110")), NOW, QuoteGatePolicy(max_spread_bps=Decimal("50"))).reasons


def test_blank_source_rejected():
    assert QuoteFailure.INVALID_SOURCE in check_quote(replace(BASE, source=""), NOW).reasons


def test_timestamp_offset_equivalence():
    quote = replace(BASE, exchange_time=BASE.exchange_time.astimezone(timezone(timedelta(hours=-3))))
    assert check_quote(quote, NOW).eligible
