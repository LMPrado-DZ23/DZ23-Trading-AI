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
    testnet_enabled: bool = False
    requires_password: bool = False
    read_only_enabled: bool = True
    live_enabled: bool = False

EXCHANGES: tuple[PersonalExchange, ...] = (
    PersonalExchange("binance", "Binance", "binance", "spot", "exchange", True),
    PersonalExchange("bybit", "Bybit", "bybit", "spot", "exchange", True),
    PersonalExchange("okx", "OKX", "okx", "spot", "exchange", True, True),
    PersonalExchange("coinbase", "Coinbase Exchange", "coinbaseexchange",
                     "spot", "exchange", True, True),
    PersonalExchange("gateio", "Gate.io", "gate", "spot", "exchange", True),
    PersonalExchange("kraken", "Kraken", "kraken", "spot", "internal"),
    PersonalExchange("kucoin", "KuCoin", "kucoin", "spot", "internal",
                     False, True),
    PersonalExchange("mercadobitcoin", "Mercado Bitcoin", "mercado",
                     "spot", "internal"),
    PersonalExchange("bitget", "Bitget", "bitget", "spot", "internal",
                     False, True),
    PersonalExchange("iq_option", "IQ Option", None, "experimental",
                     "unverified", read_only_enabled=False),
)
_BY_SLUG = {exchange.slug: exchange for exchange in EXCHANGES}
_BY_SLUG["mercado_bitcoin"] = _BY_SLUG["mercadobitcoin"]  # legado

def get_personal_exchange(slug: str) -> PersonalExchange:
    """Resolve only an explicitly approved exchange."""
    return _BY_SLUG[slug]


def required_execution_credentials(slug: str) -> tuple[str, ...]:
    """Campos mínimos para autenticar o adaptador de execução.

    Mantém o checklist alinhado ao contrato ``requiredCredentials`` do CCXT
    sem construir um cliente de rede. ``password`` é a passphrase criada na
    corretora; hoje OKX, Coinbase Exchange, KuCoin e Bitget a exigem.
    """
    exchange = get_personal_exchange(slug)
    if exchange.ccxt_id is None:
        return ()
    campos = ["EXCHANGE_API_KEY", "EXCHANGE_API_SECRET"]
    if exchange.requires_password:
        campos.append("EXCHANGE_API_PASSWORD")
    return tuple(campos)

def public_catalog() -> list[dict[str, object]]:
    """Display-safe metadata; nothing here enables an exchange adapter."""
    return [
        {"id": x.slug, "name": x.name, "market": x.market,
         "simulation": x.sandbox, "live_enabled": x.live_enabled,
         "testnet_enabled": x.testnet_enabled,
         "read_only_enabled": x.read_only_enabled,
         "requires_password": x.requires_password,
         "required_execution_credentials": list(
             required_execution_credentials(x.slug)),
         "integration_status": (
             "EXPERIMENTAL_NOT_ELIGIBLE" if x.market == "experimental"
             else "AVAILABLE_TESTNET" if x.testnet_enabled
             else "AVAILABLE_READ_ONLY")}
        for x in EXCHANGES
    ]
