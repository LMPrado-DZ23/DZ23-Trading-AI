"""Closed contracts for non-live execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Literal

EXECUTION_SCHEMA_VERSION = "tradingagents/execution/1"
_GATE_CAPABILITY = object()


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    mode: Literal["shadow", "paper"]
    ticker: str
    asset_type: str
    action: Literal["BUY", "SELL", "HOLD"]
    quantity: Decimal
    decision_price: Decimal
    decision_at: str
    telemetry_run_id: str
    telemetry_run_key: str
    sizing_sha256: str
    client_order_id: str
    _capability: object = field(repr=False, compare=False)
    schema: str = EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self._capability is not _GATE_CAPABILITY:
            raise ValueError("execution intents must be created by prepare_execution")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("_capability", None)
        value["quantity"] = str(self.quantity)
        value["decision_price"] = str(self.decision_price)
        return value

    @property
    def request_sha256(self) -> str:
        logical_request = self.to_dict()
        logical_request.pop("telemetry_run_id")
        canonical = json.dumps(logical_request, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _authorized_intent(**kwargs: Any) -> ExecutionIntent:
    return ExecutionIntent(**kwargs, _capability=_GATE_CAPABILITY)


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    client_order_id: str
    mode: Literal["shadow", "paper"]
    broker: str
    status: str
    request_sha256: str
    broker_order_id: str | None = None
    raw_status: str | None = None
    schema: str = EXECUTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
