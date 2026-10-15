from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from tradingagents.domain.contracts import OrderAck, OrderRequest, PortfolioSnapshot, Position
from tradingagents.domain.enums import Environment, OrderStatus, Side


class PaperAdapter:
    def __init__(self, *, account_id: str = "paper-account", starting_equity: Decimal = Decimal("100000"), slippage_bps: Decimal = Decimal("0"), state_path: str | Path | None = None):
        self.account_id = account_id
        self.equity = starting_equity
        self.starting_equity = starting_equity
        self.cash = starting_equity
        self.realized_pnl = Decimal("0")
        self.slippage_bps = slippage_bps
        self.state_path = Path(state_path) if state_path else None
        self.orders: dict[str, OrderAck] = {}
        self.positions: dict[str, Position] = {}
        if self.state_path:
            self._load()

    def healthcheck(self) -> dict:
        return {"status": "READY", "environment": Environment.PAPER.value, "account_id": self.account_id}

    def get_positions(self, account_id: str) -> list[Position]:
        if account_id != self.account_id:
            return []
        return list(self.positions.values())

    def get_portfolio(self, account_id: str) -> PortfolioSnapshot:
        if account_id != self.account_id:
            raise ValueError("conta paper desconhecida")
        gross = sum((position.average_price * position.quantity for position in self.positions.values()), Decimal("0"))
        return PortfolioSnapshot(
            snapshot_id=f"portfolio-{uuid4().hex}", account_id=self.account_id,
            equity=self.cash + gross, balance=self.cash, used_margin=gross,
            available_margin=max(self.cash, Decimal("0")), daily_pnl=self.realized_pnl, drawdown_pct=max((self.starting_equity - (self.cash + gross)) / self.starting_equity * Decimal("100"), Decimal("0")),
            gross_exposure=gross, net_exposure=sum((position.average_price * position.quantity * (Decimal("1") if position.side is Side.BUY else Decimal("-1") ) for position in self.positions.values()), Decimal("0")),
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
        position = self.positions.get(request.instrument_id)
        if position is None and request.side is Side.SELL:
            raise ValueError("PaperAdapter não permite abrir posição vendida; short não habilitado")
        if position and position.side is not request.side and request.quantity > position.quantity:
            raise ValueError("PaperAdapter não permite fechar além da posição; short não habilitado")
        ack = OrderAck(
            order_id=f"paper-{uuid4().hex}", client_order_id=request.client_order_id,
            status=OrderStatus.FILLED, accepted_quantity=request.quantity,
            filled_quantity=request.quantity, average_price=fill_price,
            created_at=datetime.now(timezone.utc), message="paper fill determinístico",
        )
        self.orders[request.client_order_id] = ack
        signed_qty = direction * request.quantity
        self.cash -= fill_price * signed_qty
        if position and position.side is request.side:
            qty = position.quantity + request.quantity
            avg = ((position.average_price * position.quantity) + (fill_price * request.quantity)) / qty
            self.positions[request.instrument_id] = Position(position.position_id, self.account_id, request.instrument_id, position.side, qty, avg)
        elif position:
            closed = min(position.quantity, request.quantity)
            self.realized_pnl += (fill_price - position.average_price) * closed * (Decimal("1") if position.side is Side.BUY else Decimal("-1"))
            remaining = position.quantity - request.quantity
            if remaining > 0:
                self.positions[request.instrument_id] = Position(position.position_id, self.account_id, request.instrument_id, position.side, remaining, position.average_price)
            else:
                self.positions.pop(request.instrument_id, None)
        else:
            self.positions[request.instrument_id] = Position(f"paper-pos-{uuid4().hex}", self.account_id, request.instrument_id, request.side, abs(signed_qty), fill_price)
        self._save()
        return ack

    def cancel(self, order_id: str) -> OrderAck:
        for order in self.orders.values():
            if order.order_id == order_id:
                return order
        raise KeyError(order_id)

    def reconcile(self, account_id: str) -> dict:
        return {"status": "MATCHED", "account_id": account_id, "orders": len(self.orders), "positions": len(self.positions)}

    def reconciliation_snapshot(self) -> dict:
        portfolio = self.get_portfolio(self.account_id)
        return {
            "balance": str(portfolio.balance), "equity": str(portfolio.equity), "available_margin": str(portfolio.available_margin),
            "orders": [{"id": ack.order_id, "client_order_id": ack.client_order_id, "status": ack.status.value, "quantity": str(ack.filled_quantity)} for ack in self.orders.values()],
            "fills": [{"id": ack.order_id, "quantity": str(ack.filled_quantity), "price": str(ack.average_price or 0)} for ack in self.orders.values() if ack.filled_quantity > 0],
            "positions": [{"id": position.position_id, "instrument_id": position.instrument_id, "quantity": str(position.quantity), "average_price": str(position.average_price)} for position in self.positions.values()],
        }

    def mark_to_market(self, prices: dict[str, Decimal]) -> dict[str, Decimal]:
        unrealized = Decimal("0")
        gross = Decimal("0")
        marked_value = Decimal("0")
        for instrument_id, position in self.positions.items():
            price = prices.get(instrument_id)
            if price is None or price <= 0:
                price = position.average_price
            direction = Decimal("1") if position.side is Side.BUY else Decimal("-1")
            unrealized += (price - position.average_price) * position.quantity * direction
            gross += price * position.quantity
            marked_value += price * position.quantity
        equity = self.cash + marked_value
        return {"equity": equity, "unrealized_pnl": unrealized, "gross_exposure": gross, "positions": Decimal(str(len(self.positions)))}

    def _save(self) -> None:
        if not self.state_path:
            return
        payload = {
            "account_id": self.account_id,
            "equity": str(self.equity),
            "starting_equity": str(self.starting_equity),
            "cash": str(self.cash),
            "realized_pnl": str(self.realized_pnl),
            "slippage_bps": str(self.slippage_bps),
            "orders": {key: {**value.__dict__} if hasattr(value, "__dict__") else {"order_id": value.order_id, "client_order_id": value.client_order_id, "status": value.status.value, "accepted_quantity": str(value.accepted_quantity), "filled_quantity": str(value.filled_quantity), "average_price": str(value.average_price) if value.average_price is not None else None, "created_at": value.created_at.isoformat(), "message": value.message} for key, value in self.orders.items()},
            "positions": {key: {"position_id": value.position_id, "account_id": value.account_id, "instrument_id": value.instrument_id, "side": value.side.value, "quantity": str(value.quantity), "average_price": str(value.average_price), "unrealized_pnl": str(value.unrealized_pnl), "realized_pnl": str(value.realized_pnl)} for key, value in self.positions.items()},
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    def _load(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        if data.get("account_id") != self.account_id:
            raise ValueError("estado Paper pertence a outra conta")
        self.equity = Decimal(data.get("equity", str(self.equity)))
        self.starting_equity = Decimal(data.get("starting_equity", str(self.starting_equity)))
        self.cash = Decimal(data.get("cash", str(self.equity)))
        self.realized_pnl = Decimal(data.get("realized_pnl", "0"))
        self.slippage_bps = Decimal(data.get("slippage_bps", str(self.slippage_bps)))
        self.orders = {key: OrderAck(item["order_id"], item["client_order_id"], OrderStatus(item["status"]), Decimal(item["accepted_quantity"]), Decimal(item["filled_quantity"]), Decimal(item["average_price"]) if item.get("average_price") is not None else None, datetime.fromisoformat(item["created_at"]), item.get("message", "")) for key, item in data.get("orders", {}).items()}
        self.positions = {key: Position(item["position_id"], item["account_id"], item["instrument_id"], Side(item["side"]), Decimal(item["quantity"]), Decimal(item["average_price"]), Decimal(item.get("unrealized_pnl", "0")), Decimal(item.get("realized_pnl", "0"))) for key, item in data.get("positions", {}).items()}
