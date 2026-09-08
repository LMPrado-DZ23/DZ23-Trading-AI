"""Task-local dataflow configuration.

The original module kept one mutable process-global dictionary. Concurrent
tenant jobs could therefore route each other's vendor requests. ContextVar
keeps compatibility with the existing tool functions while isolating asyncio
tasks and worker threads.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

import tradingagents.default_config as default_config

_config: dict[str, Any] | None = None
_config_var: ContextVar[dict[str, Any] | None] = ContextVar(
    "tradingagents_dataflow_config", default=None
)


def initialize_config() -> None:
    global _config
    if _config is None:
        _config = deepcopy(default_config.DEFAULT_CONFIG)


def _merged(config: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    result = deepcopy(base if base is not None else default_config.DEFAULT_CONFIG)
    for key, value in deepcopy(config).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(value)
        else:
            result[key] = value
    return result


def set_config(config: dict[str, Any]) -> None:
    """Set legacy defaults and the current context.

    Platform workers never rely on this fallback: every run is wrapped in
    :func:`use_config`. The legacy global remains only for CLI/library callers
    and compatibility with the upstream configuration surface.
    """

    global _config
    current = _config_var.get()
    merged = _merged(config, current if current is not None else _config)
    _config = deepcopy(merged)
    _config_var.set(merged)


def get_config() -> dict[str, Any]:
    """Return a defensive copy of the current task-local configuration."""

    current = _config_var.get()
    return deepcopy(
        current
        if current is not None
        else _config
        if _config is not None
        else default_config.DEFAULT_CONFIG
    )


def reset_config() -> None:
    """Reset legacy and task-local configuration to a clean default snapshot."""

    global _config
    clean = deepcopy(default_config.DEFAULT_CONFIG)
    _config = deepcopy(clean)
    _config_var.set(clean)


@contextmanager
def use_config(config: dict[str, Any]):
    """Activate an immutable snapshot for one graph execution and restore it."""

    token = _config_var.set(_merged(config))
    try:
        yield
    finally:
        _config_var.reset(token)


initialize_config()
