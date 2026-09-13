from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.domain import (
    Environment,
    MarketSnapshot,
    OrderRequest,
    PortfolioSnapshot,
    Side,
    Strategy,
    StrategyStatus,
    TradeDecision,
)
from tradingagents.execution import PaperAdapter, reconcile_ids
from tradingagents.lifecycle import StrategyRegistry
from tradingagents.mt5 import MT5Adapter
from tradingagents.risk import RiskEngine
from tradingagents.telemetry import AuditEvent, AuditLog


def snapshot(age=0):
    now = datetime.now(timezone.utc)
    return MarketSnapshot("snap-1", "BTCUSDT", "paper", Decimal("100"), Decimal("100.1"), Decimal("100"), Decimal("10"), Decimal("0.1"), now - timedelta(seconds=age), now, "test", 1)


def portfolio():
    return PortfolioSnapshot("port-1", "paper-account", Decimal("10000"), Decimal("10000"), Decimal("0"), Decimal("10000"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))


def decision(risk=Decimal("0.001")):
    return TradeDecision("dec-1", "run-1", "strat-1", "crypto", "BTCUSDT", "paper", "paper-account", Side.BUY, "MARKET", Decimal("100"), Decimal("98"), Decimal("105"), Decimal("10"), risk, Decimal("5"), "GTC", datetime.now(timezone.utc) + timedelta(minutes=5), Decimal("0.8"), "TREND", ("ev-1",), (), datetime.now(timezone.utc))


def test_risk_approves_valid_decision():
    result = RiskEngine().evaluate(decision(), portfolio(), snapshot())
    assert result.status.value == "APPROVE"


def test_risk_rejects_stale_feed():
    result = RiskEngine().evaluate(decision(), portfolio(), snapshot(age=60))
    assert result.status.value == "REJECT"
    assert "STALE_FEED" in result.reason_codes


def test_paper_is_idempotent_and_requires_risk():
    adapter = PaperAdapter()
    request = OrderRequest("coid-1", "dec-1", "run-1", "strat-1", "paper-account", "BTCUSDT", "paper", Side.BUY, "MARKET", Decimal("2"), Decimal("100"), Decimal("98"), Decimal("105"))
    with pytest.raises(PermissionError):
        adapter.submit(request, risk_approved=False)
    first = adapter.submit(request, risk_approved=True)
    second = adapter.submit(request, risk_approved=True)
    assert first == second
    assert len(adapter.orders) == 1


def test_audit_log_hash_chain_and_duplicate(tmp_path):
    log = AuditLog(tmp_path / "events.jsonl")
    first = AuditEvent.create(event_id="e1", event_type="signal.created", sequence=1, aggregate_type="signal", aggregate_id="s1", run_id="r1", actor="agent", payload={"x": 1})
    second = AuditEvent.create(event_id="e2", event_type="risk.decision.created", sequence=2, aggregate_type="decision", aggregate_id="d1", run_id="r1", actor="risk", payload={"ok": True})
    log.append(first)
    log.append(second)
    log.append(first)
    assert len(log.read()) == 2
    log.verify_chain()


def test_strategy_registry_rejects_invalid_transition():
    registry = StrategyRegistry()
    registry.register(Strategy("s1", "Momentum", "1", "crypto", StrategyStatus.RESEARCH, "hash"))
    registry.transition("s1", StrategyStatus.BACKTEST)
    with pytest.raises(ValueError):
        registry.transition("s1", StrategyStatus.CHAMPION)


def test_reconciliation_detects_divergence():
    result = reconcile_ids({"local"}, {"remote"})
    assert not result.is_safe
    assert result.status == "DIVERGED"


def test_mt5_live_is_always_blocked():
    adapter = MT5Adapter(mode=Environment.LIVE, terminal=object(), allow_live=True)
    with pytest.raises(ValueError):
        OrderRequest("coid", "dec", "run", "strategy", "account", "EURUSD", "mt5", Side.BUY, "MARKET", Decimal("1"), Decimal("1"), Decimal("0.9"), Decimal("1.2"), environment=Environment.LIVE)
    assert adapter.healthcheck()["live_enabled"] is False
