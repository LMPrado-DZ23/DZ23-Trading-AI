from datetime import datetime, timezone
from decimal import Decimal

from tradingagents.domain import MarketSnapshot, OrderRequest, Side
from tradingagents.execution import ShadowAdapter


def test_shadow_records_and_marks_without_sending_orders():
    now = datetime.now(timezone.utc)
    snapshot = MarketSnapshot("snap", "BTCUSDT", "paper", Decimal("99"), Decimal("101"), Decimal("100"), Decimal("1"), Decimal("2"), now, now, "test", 1)
    request = OrderRequest("shadow-1", "decision", "run", "strategy", "account", "BTCUSDT", "paper", Side.BUY, "MARKET", Decimal("2"), Decimal("100"), Decimal("98"), Decimal("105"))
    adapter = ShadowAdapter()
    opened = adapter.record(request, snapshot)
    closed = adapter.mark(request.client_order_id, Decimal("103"))
    assert opened.status == "OPEN"
    assert closed.hypothetical_pnl == Decimal("6")
    assert adapter.summary() == {"trades": 1, "closed": 1, "hypothetical_pnl": "6", "orders_sent": 0}


def test_shadow_state_survives_restart(tmp_path):
    now = datetime.now(timezone.utc)
    snapshot = MarketSnapshot("snap-persist", "ETHUSDT", "paper", Decimal("99"), Decimal("101"), Decimal("100"), Decimal("1"), Decimal("2"), now, now, "test", 1)
    request = OrderRequest("shadow-persist", "decision", "run", "strategy", "account", "ETHUSDT", "paper", Side.SELL, "MARKET", Decimal("1"), Decimal("100"), Decimal("101"), Decimal("95"))
    first = ShadowAdapter(state_path=tmp_path / "shadow.json")
    first.record(request, snapshot)
    second = ShadowAdapter(state_path=tmp_path / "shadow.json")
    assert second.summary()["trades"] == 1
    assert second.trades["shadow-persist"].side is Side.SELL
