"""Personal exchange inventory; no API keys, sessions or order placement."""
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class PersonalExchange:
    slug: str
    name: str
    ccxt_id: str | None
    market: Literal["spot", "experimental"]
    sandbox: Literal["exchange", "internal", "unverified"]
    live_enabled: bool = False

EXCHANGES: tuple[PersonalExchange, ...] = (
    PersonalExchange("binance", "Binance", "binance", "spot", "exchange"),
    PersonalExchange("okx", "OKX", "okx", "spot", "exchange"),
    PersonalExchange("mercado_bitcoin", "Mercado Bitcoin", "mercado", "spot", "internal"),
    PersonalExchange("kraken", "Kraken", "kraken", "spot", "internal"),
    PersonalExchange("coinbase", "Coinbase", "coinbase", "spot", "internal"),
    PersonalExchange("iq_option", "IQ Option", None, "experimental", "unverified"),
)
_BY_SLUG = {exchange.slug: exchange for exchange in EXCHANGES}

def get_personal_exchange(slug: str) -> PersonalExchange:
    """Resolve only an explicitly approved exchange."""
    return _BY_SLUG[slug]

def public_catalog() -> list[dict[str, object]]:
    """Display-safe metadata; nothing here enables an exchange adapter."""
    return [
        {"id": x.slug, "name": x.name, "market": x.market,
         "simulation": x.sandbox, "live_enabled": x.live_enabled,
         "integration_status": "PLANNED_NOT_VALIDATED"}
        for x in EXCHANGES
    ]
