from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from tradingagents.domain.contracts import MarketSnapshot, OrderRequest
from tradingagents.domain.enums import Side


@dataclass(frozen=True, slots=True)
class ShadowTrade:
    shadow_id: str
    client_order_id: str
    instrument_id: str
    side: Side
    quantity: Decimal
    reference_price: Decimal
    created_at: datetime
    status: str = "OPEN"
    exit_price: Decimal | None = None

    @property
    def hypothetical_pnl(self) -> Decimal:
        if self.exit_price is None:
            return Decimal("0")
        direction = Decimal("1") if self.side is Side.BUY else Decimal("-1")
        return (self.exit_price - self.reference_price) * self.quantity * direction


class ShadowAdapter:
    """Registra intenções sem chamar broker; útil antes de Paper/Demo."""

    def __init__(self) -> None:
        self.trades: dict[str, ShadowTrade] = {}

    def record(self, request: OrderRequest, snapshot: MarketSnapshot) -> ShadowTrade:
        if request.client_order_id in self.trades:
            return self.trades[request.client_order_id]
        price = request.price or snapshot.last
        if price <= 0:
            raise ValueError("shadow exige preço de referência positivo")
        trade = ShadowTrade(f"shadow-{request.client_order_id}", request.client_order_id, request.instrument_id, request.side, request.quantity, price, datetime.now(timezone.utc))
        self.trades[request.client_order_id] = trade
        return trade

    def mark(self, client_order_id: str, exit_price: Decimal) -> ShadowTrade:
        if client_order_id not in self.trades:
            raise KeyError(client_order_id)
        if exit_price <= 0:
            raise ValueError("preço de saída inválido")
        old = self.trades[client_order_id]
        trade = ShadowTrade(old.shadow_id, old.client_order_id, old.instrument_id, old.side, old.quantity, old.reference_price, old.created_at, "CLOSED", exit_price)
        self.trades[client_order_id] = trade
        return trade

    def summary(self) -> dict:
        closed = [trade for trade in self.trades.values() if trade.status == "CLOSED"]
        pnl = sum((trade.hypothetical_pnl for trade in closed), Decimal("0"))
        return {"trades": len(self.trades), "closed": len(closed), "hypothetical_pnl": str(pnl), "orders_sent": 0}
