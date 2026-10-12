"""Dynamic CCXT exchange discovery for personal research, without trading permissions.

The runtime installed CCXT release is the source of truth. Never expose a
private API key or enable execution from this registry.
"""
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ExchangeDiscovery:
    id: str
    configured: bool
    execution_enabled: bool
    status: str


def discover_exchange_ids(exchange_ids: Iterable[str]) -> tuple[str, ...]:
    """Normalize an explicit exchange allowlist, not user-supplied class names."""
    result = []
    for raw in exchange_ids:
        if not isinstance(raw, str) or not raw.isascii() or not raw.isidentifier():
            continue
        if raw not in result:
            result.append(raw)
    return tuple(sorted(result))


def discover_ccxt_catalog(
    installed_ids: Iterable[str],
    *,
    enabled_ids: Iterable[str] = (),
) -> list[ExchangeDiscovery]:
    """Installed adapters are NOT considered tested or authorized for LIVE.

    enabled_ids means the owner selected them for future validation only.
    """
    installed = discover_exchange_ids(installed_ids)
    selected = set(discover_exchange_ids(enabled_ids))
    return [
        ExchangeDiscovery(
            id=exchange_id,
            configured=exchange_id in selected,
            execution_enabled=False,
            status=("SELECTED_NOT_VERIFIED" if exchange_id in selected
                    else "AVAILABLE_NOT_CONFIGURED"),
        )
        for exchange_id in installed
    ]


def installed_ccxt_exchange_ids(loader: Callable[[], object] | None = None) -> tuple[str, ...]:
    """Lazy import avoids boot failure if ccxt is unavailable.

    A caller must not interpret the inventory as a connectivity check.
    """
    if loader is None:
        import ccxt
        module = ccxt
    else:
        module = loader()
    exchanges = getattr(module, "exchanges", ())
    return discover_exchange_ids(exchanges)
