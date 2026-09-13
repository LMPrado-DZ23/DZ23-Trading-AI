from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from tradingagents.domain.contracts import OrderAck, OrderRequest, PortfolioSnapshot, Position
from tradingagents.domain.enums import Environment, OrderStatus, Side


class PaperAdapter:
    def __init__(self, *, account_id: str = "paper-account", starting_equity: Decimal = Decimal("100000"), slippage_bps: Decimal = Decimal("0")):
        self.account_id = account_id
        self.equity = starting_equity
        self.slippage_bps = slippage_bps
        self.orders: dict[str, OrderAck] = {}
        self.positions: dict[str, Position] = {}

    def healthcheck(self) -> dict:
        return {"status": "READY", "environment": Environment.PAPER.value, "account_id": self.account_id}

    def get_positions(self, account_id: str) -> list[Position]:
        if account_id != self.account_id:
            return []
        return list(self.positions.values())

    def get_portfolio(self, account_id: str) -> PortfolioSnapshot:
        if account_id != self.account_id:
            raise ValueError("conta paper desconhecida")
        return PortfolioSnapshot(
            snapshot_id=f"portfolio-{uuid4().hex}", account_id=self.account_id,
            equity=self.equity, balance=self.equity, used_margin=Decimal("0"),
            available_margin=self.equity, daily_pnl=Decimal("0"), drawdown_pct=Decimal("0"),
            gross_exposure=Decimal("0"), net_exposure=Decimal("0"),
            positions=tuple(self.positions.values()), captured_at=datetime.now(timezone.utc),
        )

    def submit(self, request: OrderRequest, *, risk_approved: bool) -> OrderAck:
        if request.environment is not Environment.PAPER:
            raise ValueError("PaperAdapter aceita somente PAPER")
        if not risk_approved:
            raise PermissionError("ordem paper sem aprovação do Risk Engine")
        if request.client_order_id in self.orders:
            return self.orders[request.client_order_id]
        direction = Decimal("1") if request.side is Side.BUY else Decimal("-1")
        price = request.price or Decimal("0")
        if price <= 0:
            raise ValueError("PaperAdapter exige preço positivo para fill determinístico")
        slippage = price * self.slippage_bps / Decimal("10000")
        fill_price = price + (direction * slippage)
        ack = OrderAck(
            order_id=f"paper-{uuid4().hex}", client_order_id=request.client_order_id,
            status=OrderStatus.FILLED, accepted_quantity=request.quantity,
            filled_quantity=request.quantity, average_price=fill_price,
            created_at=datetime.now(timezone.utc), message="paper fill determinístico",
        )
        self.orders[request.client_order_id] = ack
        position = self.positions.get(request.instrument_id)
        signed_qty = direction * request.quantity
        if position and position.side is request.side:
            qty = position.quantity + request.quantity
            avg = ((position.average_price * position.quantity) + (fill_price * request.quantity)) / qty
            self.positions[request.instrument_id] = Position(position.position_id, self.account_id, request.instrument_id, position.side, qty, avg)
        elif position:
            remaining = position.quantity - request.quantity
            if remaining > 0:
                self.positions[request.instrument_id] = Position(position.position_id, self.account_id, request.instrument_id, position.side, remaining, position.average_price)
            else:
                self.positions.pop(request.instrument_id, None)
        else:
            self.positions[request.instrument_id] = Position(f"paper-pos-{uuid4().hex}", self.account_id, request.instrument_id, request.side, abs(signed_qty), fill_price)
        return ack

    def cancel(self, order_id: str) -> OrderAck:
        for order in self.orders.values():
            if order.order_id == order_id:
                return order
        raise KeyError(order_id)

    def reconcile(self, account_id: str) -> dict:
        return {"status": "MATCHED", "account_id": account_id, "orders": len(self.orders), "positions": len(self.positions)}
