from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.domain import (
    MarketSnapshot,
    OrderRequest,
    PortfolioSnapshot,
    Side,
    TradeDecision,
)
from tradingagents.execution import ExecutionRouter, PaperAdapter
from tradingagents.risk import RiskEngine
from tradingagents.telemetry import AuditLog, EventBus


def make_decision(*, stale: bool = False):
    now = datetime.now(timezone.utc)
    observed = now - timedelta(seconds=60 if stale else 0)
    return TradeDecision("dec-router", "run-router", "strategy", "crypto", "BTCUSDT", "paper", "paper-account", Side.BUY, "MARKET", Decimal("100"), Decimal("98"), Decimal("105"), Decimal("10"), Decimal("0.001"), Decimal("1"), "GTC", now + timedelta(minutes=5), Decimal("0.8"), "TREND", ("evidence-1",), (), now), MarketSnapshot("snap-router", "BTCUSDT", "paper", Decimal("99.9"), Decimal("100.1"), Decimal("100"), Decimal("1"), Decimal("0.2"), observed, now, "test", 1)


def make_portfolio():
    return PortfolioSnapshot("portfolio-router", "paper-account", Decimal("10000"), Decimal("10000"), Decimal("0"), Decimal("10000"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))


def make_request():
    return OrderRequest("coid-router", "dec-router", "run-router", "strategy", "paper-account", "BTCUSDT", "paper", Side.BUY, "MARKET", Decimal("1"), Decimal("100"), Decimal("98"), Decimal("105"))


def router(log, bus):
    return ExecutionRouter(risk_engine=RiskEngine(), audit_log=log, event_bus=bus, reconciliation_guard=lambda: True)


def test_router_requires_risk_and_publishes_events(tmp_path):
    log = AuditLog(tmp_path / "events.jsonl")
    bus = EventBus()
    decision, snapshot = make_decision()
    result = router(log, bus).submit(decision, make_portfolio(), snapshot, PaperAdapter(), request=make_request())
    assert result.risk.status.value == "APPROVE"
    assert result.order is not None
    assert {event.event.event_type for event in bus.replay()} == {"risk.decision.created", "order.submitted", "order.accepted"}
    log.verify_chain()


def test_router_blocks_stale_data_without_calling_adapter(tmp_path):
    log = AuditLog(tmp_path / "events.jsonl")
    bus = EventBus()
    decision, snapshot = make_decision(stale=True)
    result = router(log, bus).submit(decision, make_portfolio(), snapshot, PaperAdapter(), request=make_request())
    assert result.order is None
    assert result.risk.status.value == "REJECT"
    assert "STALE_FEED" in result.risk.reason_codes


def test_router_is_idempotent(tmp_path):
    log = AuditLog(tmp_path / "events.jsonl")
    bus = EventBus()
    adapter = PaperAdapter()
    execution = router(log, bus)
    decision, snapshot = make_decision()
    first = execution.submit(decision, make_portfolio(), snapshot, adapter, request=make_request())
    second = execution.submit(decision, make_portfolio(), snapshot, adapter, request=make_request())
    assert first.order == second.order
    assert len(adapter.orders) == 1


def test_router_without_reconciliation_guard_is_fail_closed(tmp_path):
    router_without_guard = ExecutionRouter(risk_engine=RiskEngine(), audit_log=AuditLog(tmp_path / "events.jsonl"), event_bus=EventBus())
    decision, snapshot = make_decision()
    with pytest.raises(PermissionError, match="reconciliação"):
        router_without_guard.submit(decision, make_portfolio(), snapshot, PaperAdapter(), request=make_request())
