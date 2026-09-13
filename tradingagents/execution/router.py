from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from tradingagents.domain.contracts import (
    MarketSnapshot,
    OrderAck,
    OrderRequest,
    PortfolioSnapshot,
    RiskDecision,
    TradeDecision,
)
from tradingagents.domain.enums import RiskStatus
from tradingagents.risk import RiskEngine
from tradingagents.telemetry import AuditEvent, AuditLog, EventBus

from .kill_switch import KillSwitch
from .protocol import BrokerAdapter


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    risk: RiskDecision
    order: OrderAck | None


class ExecutionRouter:
    """Única porta de execução: risco aprovado, auditoria e adapter."""

    def __init__(self, *, risk_engine: RiskEngine, audit_log: AuditLog, event_bus: EventBus, kill_switch: KillSwitch | None = None):
        self.risk_engine = risk_engine
        self.audit_log = audit_log
        self.event_bus = event_bus
        self.kill_switch = kill_switch or KillSwitch()
        self._client_orders: dict[str, OrderAck] = {}

    def submit(self, decision: TradeDecision, portfolio: PortfolioSnapshot, snapshot: MarketSnapshot, adapter: BrokerAdapter, *, request: OrderRequest, now: datetime | None = None) -> ExecutionResult:
        current = now or datetime.now(timezone.utc)
        self.kill_switch.assert_allowed()
        risk = self.risk_engine.evaluate(decision, portfolio, snapshot, now=current)
        self._publish("risk.decision.created", decision.run_id, decision.decision_id, {"status": risk.status.value, "reason_codes": risk.reason_codes, "risk_decision_id": risk.risk_decision_id})
        if risk.status is not RiskStatus.APPROVE:
            self._publish("order.rejected", decision.run_id, request.client_order_id, {"reason": "RISK_REJECTED", "reason_codes": risk.reason_codes})
            return ExecutionResult(risk, None)
        if request.client_order_id in self._client_orders:
            return ExecutionResult(risk, self._client_orders[request.client_order_id])
        self._publish("order.submitted", decision.run_id, request.client_order_id, {"instrument_id": request.instrument_id, "side": request.side.value, "quantity": str(request.quantity), "environment": request.environment.value})
        ack = adapter.submit(request, risk_approved=True)
        self._client_orders[request.client_order_id] = ack
        self._publish("order.accepted" if ack.status.value in {"ACCEPTED", "FILLED"} else "order.rejected", decision.run_id, request.client_order_id, {"order_id": ack.order_id, "status": ack.status.value, "filled_quantity": str(ack.filled_quantity), "average_price": str(ack.average_price or Decimal("0"))})
        return ExecutionResult(risk, ack)

    def _publish(self, event_type: str, run_id: str, aggregate_id: str, payload: dict[str, Any]) -> None:
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
        event = AuditEvent.create(event_id=f"{event_type}:{run_id}:{aggregate_id}:{digest}", event_type=event_type, sequence=0, aggregate_type="execution", aggregate_id=aggregate_id, run_id=run_id, actor="execution-router", payload=payload)
        self.audit_log.append(event)
        self.event_bus.publish(event)
