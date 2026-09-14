from decimal import Decimal

from tradingagents.domain import Environment, OrderRequest, Side
from tradingagents.execution import PaperAdapter


def request(client_id: str, side: Side, price: str) -> OrderRequest:
    return OrderRequest(client_id, "d", "r", "s", "paper-account", "BTCUSDT", Environment.PAPER.value, side, "MARKET", Decimal("1"), Decimal(price), Decimal("90"), Decimal("120"))


def test_paper_portfolio_exposes_position_and_realized_pnl():
    adapter = PaperAdapter()
    adapter.submit(request("buy", Side.BUY, "100"), risk_approved=True)
    portfolio = adapter.get_portfolio("paper-account")
    assert portfolio.used_margin == Decimal("100")
    assert portfolio.gross_exposure == Decimal("100")
    adapter.submit(request("sell", Side.SELL, "110"), risk_approved=True)
    closed = adapter.get_portfolio("paper-account")
    assert closed.daily_pnl == Decimal("10")
    assert closed.positions == ()
