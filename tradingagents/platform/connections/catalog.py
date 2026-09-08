"""Immutable allowlist and safety capabilities for supported spot exchanges."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum


class BrokerEnvironment(StrEnum):
    TESTNET = "testnet"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class ExchangeCapability:
    slug: str
    display_name: str
    ccxt_id: str
    has_real_testnet: bool
    credential_fields: tuple[str, ...]
    live_origins: tuple[str, ...]
    testnet_origins: tuple[str, ...]
    permission_probe: bool
    spot_only: bool = True
    withdrawals_forbidden: bool = True
    margin_forbidden: bool = True
    derivatives_forbidden: bool = True


_CATALOG = {
    "binance": ExchangeCapability(
        "binance",
        "Binance",
        "binance",
        True,
        ("api_key", "secret"),
        ("https://api.binance.com",),
        ("https://testnet.binance.vision",),
        False,
    ),
    "bybit": ExchangeCapability(
        "bybit",
        "Bybit",
        "bybit",
        True,
        ("api_key", "secret"),
        ("https://api.bybit.com",),
        ("https://api-testnet.bybit.com",),
        False,
    ),
    "okx": ExchangeCapability(
        "okx",
        "OKX",
        "okx",
        True,
        ("api_key", "secret", "passphrase"),
        ("https://www.okx.com",),
        ("https://www.okx.com",),
        False,
    ),
    "coinbase": ExchangeCapability(
        "coinbase",
        "Coinbase Exchange",
        "coinbaseexchange",
        True,
        ("api_key", "secret", "passphrase"),
        ("https://api.exchange.coinbase.com",),
        ("https://api-public.sandbox.exchange.coinbase.com",),
        False,
    ),
    "kraken": ExchangeCapability(
        "kraken",
        "Kraken",
        "kraken",
        False,
        ("api_key", "secret"),
        ("https://api.kraken.com",),
        (),
        False,
    ),
    "gateio": ExchangeCapability(
        "gateio",
        "Gate.io",
        "gate",
        True,
        ("api_key", "secret"),
        ("https://api.gateio.ws",),
        ("https://api-testnet.gateapi.io",),
        False,
    ),
    "kucoin": ExchangeCapability(
        "kucoin",
        "KuCoin",
        "kucoin",
        False,
        ("api_key", "secret", "passphrase"),
        ("https://api.kucoin.com",),
        (),
        False,
    ),
    "mercadobitcoin": ExchangeCapability(
        "mercadobitcoin",
        "Mercado Bitcoin",
        "mercado",
        False,
        ("api_key", "secret"),
        ("https://api.mercadobitcoin.net",),
        (),
        False,
    ),
    "bitget": ExchangeCapability(
        "bitget",
        "Bitget",
        "bitget",
        False,
        ("api_key", "secret", "passphrase"),
        ("https://api.bitget.com",),
        (),
        False,
    ),
}


def exchange_catalog() -> tuple[ExchangeCapability, ...]:
    return tuple(_CATALOG.values())


def exchange_capability(slug: str) -> ExchangeCapability:
    normalized = slug.strip().lower()
    try:
        return _CATALOG[normalized]
    except KeyError as exc:
        raise ValueError("exchange is not in the approved DZ23 catalog") from exc


def capability_digest(capability: ExchangeCapability) -> str:
    payload = {
        "schema": "exchange-capability/1",
        "slug": capability.slug,
        "ccxt_id": capability.ccxt_id,
        "has_real_testnet": capability.has_real_testnet,
        "credential_fields": capability.credential_fields,
        "live_origins": capability.live_origins,
        "testnet_origins": capability.testnet_origins,
        "permission_probe": capability.permission_probe,
        "spot_only": capability.spot_only,
        "withdrawals_forbidden": capability.withdrawals_forbidden,
        "margin_forbidden": capability.margin_forbidden,
        "derivatives_forbidden": capability.derivatives_forbidden,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
