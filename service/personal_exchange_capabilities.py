"""Static CCXT capability inspection; no network or order execution.

Treat advertised capabilities as hints, not evidence of a working exchange API.
"""
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ExchangeCapabilities:
    exchange_id: str
    has_public_ticker: bool
    has_order_book: bool
    has_ohlcv: bool
    has_private_balance: bool
    has_private_order: bool
    status: str = "UNVERIFIED"


def inspect_capabilities(exchange_id: str, capabilities: Mapping[str, object]) -> ExchangeCapabilities:
    """Map CCXT has flags without claiming an exchange was tested.

    CCXT capability flags may be True, False, or 'emulated'. Emulated is
    intentionally not equivalent to native API support.
    """
    def native(name: str) -> bool:
        return capabilities.get(name) is True

    return ExchangeCapabilities(
        exchange_id=exchange_id,
        has_public_ticker=native("fetchTicker"),
        has_order_book=native("fetchOrderBook"),
        has_ohlcv=native("fetchOHLCV"),
        has_private_balance=native("fetchBalance"),
        has_private_order=native("createOrder"),
    )


def report_capabilities(exchange_id: str, capabilities: Mapping[str, object]) -> dict[str, object]:
    x = inspect_capabilities(exchange_id, capabilities)
    return {
        "id": x.exchange_id,
        "ticker": x.has_public_ticker,
        "order_book": x.has_order_book,
        "ohlcv": x.has_ohlcv,
        "balance": x.has_private_balance,
        "order_api": x.has_private_order,
        "verified": False,
        "live_enabled": False,
        "status": x.status,
    }
