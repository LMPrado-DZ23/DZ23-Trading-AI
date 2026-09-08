"""Immutable, point-in-time execution context for a TradingAgents run.

The context is created once at the entry boundary and its canonical projection is
bound to checkpoints and tool execution.  Agents cannot move the data cutoff by
supplying a different date in a tool call.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from tradingagents.default_config import DEFAULT_CONFIG

WORKFLOW_SCHEMA_VERSION = "tradingagents/workflow/3"


def _deep_merge(base: dict[str, Any], overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (overrides or {}).items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def build_runtime_config(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return an isolated effective config; never mutate process-global state."""
    return _deep_merge(DEFAULT_CONFIG, overrides)


def freeze_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """Recursively freeze a config snapshot used by one graph instance."""

    def freeze(value: Any) -> Any:
        if isinstance(value, Mapping):
            return MappingProxyType({str(k): freeze(v) for k, v in value.items()})
        if isinstance(value, list):
            return tuple(freeze(v) for v in value)
        if isinstance(value, tuple):
            return tuple(freeze(v) for v in value)
        return deepcopy(value)

    return freeze(config)


def thaw_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Create a serializable copy of a frozen config."""

    def thaw(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(k): thaw(v) for k, v in value.items()}
        if isinstance(value, tuple):
            return [thaw(v) for v in value]
        return deepcopy(value)

    return thaw(config)


def config_digest(config: Mapping[str, Any]) -> str:
    canonical = json.dumps(thaw_config(config), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RunContext:
    ticker: str
    trade_date: date
    asset_type: str
    decision_at: datetime
    market_timezone: str
    workflow_schema_version: str
    config_sha256: str
    run_mode: str
    historical: bool

    @classmethod
    def create(
        cls,
        ticker: str,
        trade_date: str | date,
        asset_type: str,
        config: Mapping[str, Any],
        *,
        decision_at: datetime | None = None,
        now: datetime | None = None,
        run_mode: str = "analysis",
    ) -> RunContext:
        clean_ticker = str(ticker).strip()
        if not clean_ticker:
            raise ValueError("ticker must not be empty")
        parsed_date = (
            trade_date if isinstance(trade_date, date) and not isinstance(trade_date, datetime)
            else date.fromisoformat(str(trade_date))
        )
        if asset_type not in {"stock", "crypto", "forex", "index"}:
            raise ValueError(f"unsupported asset_type {asset_type!r}")
        if run_mode not in {"analysis", "backtest", "paper", "shadow"}:
            raise ValueError(f"unsupported run_mode {run_mode!r}")

        if asset_type == "crypto":
            tz_name = str(config.get("crypto_timezone", "UTC"))
            close_text = str(config.get("crypto_session_close", "23:59:59"))
        else:
            tz_name = str(config.get("market_timezone", "America/New_York"))
            close_text = str(config.get("market_session_close", "16:00:00"))
        tz = ZoneInfo(tz_name)
        close_time = time.fromisoformat(close_text)
        logical_cutoff = datetime.combine(parsed_date, close_time, tzinfo=tz)

        if decision_at is not None:
            if decision_at.tzinfo is None or decision_at.utcoffset() is None:
                raise ValueError("decision_at must be timezone-aware")
            if decision_at.astimezone(tz).date() != parsed_date:
                raise ValueError("decision_at must belong to trade_date in market timezone")
            logical_cutoff = decision_at.astimezone(tz)

        observed_now = now or datetime.now(timezone.utc)
        if observed_now.tzinfo is None or observed_now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        return cls(
            ticker=clean_ticker,
            trade_date=parsed_date,
            asset_type=asset_type,
            decision_at=logical_cutoff,
            market_timezone=tz_name,
            workflow_schema_version=WORKFLOW_SCHEMA_VERSION,
            config_sha256=config_digest(config),
            run_mode=run_mode,
            historical=(
                run_mode == "backtest" or logical_cutoff < observed_now.astimezone(tz)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "trade_date": self.trade_date.isoformat(),
            "asset_type": self.asset_type,
            "decision_at": self.decision_at.isoformat(),
            "market_timezone": self.market_timezone,
            "workflow_schema_version": self.workflow_schema_version,
            "config_sha256": self.config_sha256,
            "run_mode": self.run_mode,
            "historical": self.historical,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunContext:
        required = {
            "ticker", "trade_date", "asset_type", "decision_at", "market_timezone",
            "workflow_schema_version", "config_sha256", "run_mode", "historical",
        }
        if set(value) != required:
            raise ValueError("run context has missing or unknown fields")
        decision_at = datetime.fromisoformat(str(value["decision_at"]))
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("run context decision_at must be timezone-aware")
        if value["workflow_schema_version"] != WORKFLOW_SCHEMA_VERSION:
            raise ValueError("run context workflow schema is incompatible")
        if not isinstance(value["historical"], bool):
            raise ValueError("run context historical must be a boolean")
        if value["run_mode"] not in {"analysis", "backtest", "paper", "shadow"}:
            raise ValueError("run context mode is invalid")
        return cls(
            ticker=str(value["ticker"]),
            trade_date=date.fromisoformat(str(value["trade_date"])),
            asset_type=str(value["asset_type"]),
            decision_at=decision_at,
            market_timezone=str(value["market_timezone"]),
            workflow_schema_version=str(value["workflow_schema_version"]),
            config_sha256=str(value["config_sha256"]),
            run_mode=str(value["run_mode"]),
            historical=value["historical"],
        )


def runtime_from_runnable(config: Mapping[str, Any] | None) -> tuple[dict[str, Any], RunContext | None]:
    """Extract graph-bound config/context from a LangChain RunnableConfig."""
    configurable = (config or {}).get("configurable", {})
    runtime_config = build_runtime_config(configurable.get("tradingagents_config"))
    raw_context = configurable.get("tradingagents_run_context")
    return runtime_config, RunContext.from_dict(raw_context) if raw_context else None
