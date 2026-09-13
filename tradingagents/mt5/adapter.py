from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from tradingagents.domain.contracts import OrderAck, OrderRequest, PortfolioSnapshot, Position
from tradingagents.domain.enums import Environment, OrderStatus

LIVE_CONFIRM_PHRASE = "EU AUTORIZO OPERAR COM DINHEIRO REAL"


class MT5Adapter:
    """Adapter isolado para a biblioteca MetaTrader5.

    DEMO é permitido quando explicitamente configurado. LIVE possui apenas
    validação de política nesta fase e recusa qualquer envio de ordem.
    """

    def __init__(self, *, mode: Environment = Environment.DEMO, terminal: Any = None,
                 allow_live: bool = False, live_confirmation: str | None = None):
        if mode not in (Environment.DEMO, Environment.LIVE):
            raise ValueError("MT5Adapter aceita DEMO ou LIVE")
        self.mode = mode
        self.allow_live = allow_live
        self._mt5 = terminal
        self._connected = False
        self._live_confirmation = live_confirmation or os.getenv("LIVE_TRADING_CONFIRM", "")

    def healthcheck(self) -> dict:
        return {"status": "READY" if self._connected else "DISCONNECTED", "mode": self.mode.value, "live_enabled": False}

    def initialize(self, **kwargs: Any) -> bool:
        if self._mt5 is None:
            return False
        ok = bool(self._mt5.initialize(**kwargs))
        self._connected = ok
        return ok

    def shutdown(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()
        self._connected = False

    def get_positions(self, account_id: str) -> list[Position]:
        self._require_connected()
        rows = self._mt5.positions_get() or ()
        return [self._position_from_mt5(row, account_id) for row in rows]

    def get_portfolio(self, account_id: str) -> PortfolioSnapshot:
        self._require_connected()
        info = self._mt5.account_info()
        if info is None:
            raise RuntimeError("account_info indisponível")
        positions = tuple(self.get_positions(account_id))
        equity = Decimal(str(getattr(info, "equity", 0)))
        balance = Decimal(str(getattr(info, "balance", 0)))
        return PortfolioSnapshot(
            snapshot_id=f"mt5-{account_id}-{int(datetime.now(timezone.utc).timestamp())}",
            account_id=account_id, equity=equity, balance=balance,
            used_margin=Decimal(str(getattr(info, "margin", 0))),
            available_margin=Decimal(str(getattr(info, "margin_free", 0))),
            daily_pnl=Decimal("0"), drawdown_pct=Decimal("0"),
            gross_exposure=Decimal("0"), net_exposure=Decimal("0"), positions=positions,
            captured_at=datetime.now(timezone.utc),
        )

    def submit(self, request: OrderRequest, *, risk_approved: bool) -> OrderAck:
        if not risk_approved:
            raise PermissionError("ordem MT5 sem aprovação do Risk Engine")
        if request.environment not in (Environment.DEMO, Environment.LIVE):
            raise ValueError("MT5 aceita DEMO ou LIVE")
        if request.environment is Environment.LIVE or self.mode is Environment.LIVE:
            raise PermissionError("LIVE bloqueado nesta entrega; use DEMO/PAPER/SHADOW")
        self._require_connected()
        tick = self._mt5.symbol_info_tick(request.instrument_id)
        if tick is None:
            raise RuntimeError("tick MT5 indisponível")
        price = request.price or Decimal(str(getattr(tick, "ask", 0) if request.side.value == "BUY" else getattr(tick, "bid", 0)))
        if price <= 0:
            raise ValueError("preço MT5 inválido")
        order_type = getattr(self._mt5, "ORDER_TYPE_BUY", 0) if request.side.value == "BUY" else getattr(self._mt5, "ORDER_TYPE_SELL", 1)
        payload = {"action": getattr(self._mt5, "TRADE_ACTION_DEAL", 1), "symbol": request.instrument_id,
                   "volume": float(request.quantity), "type": order_type, "price": float(price),
                   "sl": float(request.stop_loss), "tp": float(request.take_profit),
                   "deviation": 20, "magic": 230923, "comment": request.client_order_id,
                   "type_time": getattr(self._mt5, "ORDER_TIME_GTC", 0),
                   "type_filling": getattr(self._mt5, "ORDER_FILLING_IOC", 1)}
        check = self._mt5.order_check(payload)
        if check is None or int(getattr(check, "retcode", 0)) not in {0, getattr(self._mt5, "TRADE_RETCODE_DONE", 10009), getattr(self._mt5, "TRADE_RETCODE_PLACED", 10008)}:
            raise RuntimeError(f"order_check recusou: {getattr(check, 'comment', 'sem resposta')}")
        result = self._mt5.order_send(payload)
        if result is None:
            raise RuntimeError("order_send sem resposta")
        retcode = int(getattr(result, "retcode", 0))
        accepted = retcode in {getattr(self._mt5, "TRADE_RETCODE_DONE", 10009), getattr(self._mt5, "TRADE_RETCODE_PLACED", 10008)}
        return OrderAck(str(getattr(result, "order", getattr(result, "deal", "unknown"))), request.client_order_id,
                        OrderStatus.ACCEPTED if accepted else OrderStatus.REJECTED,
                        request.quantity if accepted else Decimal("0"), Decimal("0"), price,
                        datetime.now(timezone.utc), str(getattr(result, "comment", "")))

    def cancel(self, order_id: str) -> OrderAck:
        self._require_connected()
        result = self._mt5.order_send({"action": getattr(self._mt5, "TRADE_ACTION_REMOVE", 8), "order": int(order_id)})
        if result is None:
            raise RuntimeError("cancelamento MT5 sem resposta")
        ok = int(getattr(result, "retcode", 0)) in {getattr(self._mt5, "TRADE_RETCODE_DONE", 10009), getattr(self._mt5, "TRADE_RETCODE_PLACED", 10008)}
        return OrderAck(order_id, order_id, OrderStatus.CANCELLED if ok else OrderStatus.UNKNOWN, Decimal("0"), Decimal("0"), None, datetime.now(timezone.utc), str(getattr(result, "comment", "")))

    def reconcile(self, account_id: str) -> dict:
        self._require_connected()
        positions = self._mt5.positions_get() or ()
        orders = self._mt5.orders_get() or ()
        return {"status": "OBSERVED", "account_id": account_id, "positions": len(positions), "orders": len(orders)}

    def reconciliation_snapshot(self, account_id: str) -> dict:
        self._require_connected()
        portfolio = self.get_portfolio(account_id)
        orders = self._mt5.orders_get() or ()
        deals = self._mt5.history_deals_get() or ()
        return {
            "balance": str(portfolio.balance), "equity": str(portfolio.equity), "available_margin": str(portfolio.available_margin),
            "orders": [{"id": str(getattr(item, "ticket", "unknown")), "status": str(getattr(item, "state", "UNKNOWN")), "quantity": str(getattr(item, "volume_current", getattr(item, "volume_initial", 0)))} for item in orders],
            "fills": [{"id": str(getattr(item, "ticket", "unknown")), "quantity": str(getattr(item, "volume", 0)), "price": str(getattr(item, "price", 0))} for item in deals],
            "positions": [{"id": position.position_id, "instrument_id": position.instrument_id, "quantity": str(position.quantity), "average_price": str(position.average_price)} for position in portfolio.positions],
        }

    def _require_connected(self) -> None:
        if not self._connected or self._mt5 is None:
            raise RuntimeError("terminal MT5 desconectado")

    @staticmethod
    def _position_from_mt5(row: Any, account_id: str) -> Position:
        from tradingagents.domain.enums import Side
        side = Side.BUY if int(getattr(row, "type", 0)) == 0 else Side.SELL
        return Position(
            position_id=str(getattr(row, "ticket", "unknown")), account_id=account_id,
            instrument_id=str(getattr(row, "symbol", "unknown")), side=side,
            quantity=Decimal(str(getattr(row, "volume", 0))),
            average_price=Decimal(str(getattr(row, "price_open", 0))),
            unrealized_pnl=Decimal(str(getattr(row, "profit", 0))),
        )
