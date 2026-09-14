from decimal import Decimal

import pytest

from tradingagents.domain import Environment, OrderRequest, Side
from tradingagents.execution import PaperAdapter


def request(client_id: str, side: Side, price: str, quantity: str = "1") -> OrderRequest:
    return OrderRequest(client_id, "d", "r", "s", "paper-account", "BTCUSDT", Environment.PAPER.value, side, "MARKET", Decimal(quantity), Decimal(price), Decimal("90"), Decimal("120"))


def test_paper_portfolio_exposes_position_and_realized_pnl():
    adapter = PaperAdapter()
    adapter.submit(request("buy", Side.BUY, "100"), risk_approved=True)
    portfolio = adapter.get_portfolio("paper-account")
    assert portfolio.used_margin == Decimal("100")
    assert portfolio.gross_exposure == Decimal("100")
    adapter.submit(request("sell", Side.SELL, "110"), risk_approved=True)
    closed = adapter.get_portfolio("paper-account")
    assert closed.daily_pnl == Decimal("10")
    assert closed.balance == Decimal("100010")
    assert closed.positions == ()


def test_paper_rejects_over_close_without_creating_hidden_short():
    adapter = PaperAdapter()
    adapter.submit(request("buy", Side.BUY, "100"), risk_approved=True)
    with pytest.raises(ValueError, match="fechar além"):
        adapter.submit(request("over-close", Side.SELL, "110", "2"), risk_approved=True)
    assert adapter.get_positions("paper-account")[0].quantity == Decimal("1")
