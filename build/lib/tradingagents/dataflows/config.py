"""Context-local compatibility configuration for direct dataflow callers.

Graph execution passes an explicit immutable config. These functions remain for
the public low-level API without reintroducing process-global mutable state.
"""

from contextvars import ContextVar
from copy import deepcopy

from tradingagents.runtime import build_runtime_config

_config_context: ContextVar[dict | None] = ContextVar(
    "tradingagents_dataflow_config", default=None
)


def reset_config() -> None:
    _config_context.set(None)


def set_config(config: dict):
    """Update the configuration for the current execution context.

    Dict-valued keys (e.g. ``data_vendors``) are merged one level deep so a
    partial update like ``{"data_vendors": {"core_stock_apis": "alpha_vantage"}}``
    keeps the other nested keys from the default; scalar keys are replaced.
    """
    current = get_config()
    incoming = deepcopy(config)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            current[key].update(value)
        else:
            current[key] = value
    _config_context.set(current)


def get_config() -> dict:
    """Get the current configuration."""
    current = _config_context.get()
    return deepcopy(current) if current is not None else build_runtime_config()
