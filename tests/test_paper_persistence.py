from decimal import Decimal

from tradingagents.domain import OrderRequest, Side
from tradingagents.execution import PaperAdapter


def test_paper_state_survives_restart(tmp_path):
    path = tmp_path / "paper-state.json"
    request = OrderRequest("paper-persist", "decision", "run", "strategy", "paper-account", "BTCUSDT", "paper", Side.BUY, "MARKET", Decimal("2"), Decimal("100"), Decimal("98"), Decimal("105"))
    first = PaperAdapter(state_path=path)
    ack = first.submit(request, risk_approved=True)
    second = PaperAdapter(state_path=path)
    assert second.orders["paper-persist"] == ack
    assert second.positions["BTCUSDT"].quantity == Decimal("2")
    assert second.reconcile("paper-account")["status"] == "MATCHED"


def test_paper_mark_to_market_reports_unrealized_pnl():
    request = OrderRequest("paper-mark", "decision", "run", "strategy", "paper-account", "BTCUSDT", "paper", Side.BUY, "MARKET", Decimal("2"), Decimal("100"), Decimal("98"), Decimal("105"))
    adapter = PaperAdapter()
    adapter.submit(request, risk_approved=True)
    metrics = adapter.mark_to_market({"BTCUSDT": Decimal("103")})
    assert metrics["unrealized_pnl"] == Decimal("6")
    assert metrics["equity"] == Decimal("100006")
