from decimal import Decimal
from types import SimpleNamespace

from tradingagents.domain import Environment, OrderRequest, Side
from tradingagents.execution import PaperAdapter
from tradingagents.mt5 import MT5Adapter


def test_paper_snapshot_contains_account_entities():
    adapter = PaperAdapter()
    request = OrderRequest("snapshot", "d", "r", "s", "paper-account", "BTCUSDT", "paper", Side.BUY, "MARKET", Decimal("1"), Decimal("100"), Decimal("98"), Decimal("105"))
    adapter.submit(request, risk_approved=True)
    snapshot = adapter.reconciliation_snapshot()
    assert snapshot["orders"] and snapshot["fills"] and snapshot["positions"]


def test_mt5_snapshot_uses_account_and_deals():
    fake = SimpleNamespace(
        initialize=lambda **_: True,
        shutdown=lambda: None,
        account_info=lambda: SimpleNamespace(equity=100, balance=100, margin=0, margin_free=100),
        positions_get=lambda: (), orders_get=lambda: (), history_deals_get=lambda: (),
    )
    adapter = MT5Adapter(mode=Environment.DEMO, terminal=fake)
    assert adapter.initialize()
    snapshot = adapter.reconciliation_snapshot("demo")
    assert snapshot["balance"] == "100"
    assert snapshot["orders"] == []
