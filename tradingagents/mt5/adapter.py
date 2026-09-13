from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from tradingagents.domain.contracts import OrderAck, OrderRequest, PortfolioSnapshot, Position
from tradingagents.domain.enums import Environment

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
        raise NotImplementedError("envio DEMO será implementado após contrato de request e mock de retcode")

    def cancel(self, order_id: str) -> OrderAck:
        self._require_connected()
        raise NotImplementedError("cancelamento MT5 será implementado junto da reconciliação")

    def reconcile(self, account_id: str) -> dict:
        self._require_connected()
        positions = self._mt5.positions_get() or ()
        orders = self._mt5.orders_get() or ()
        return {"status": "OBSERVED", "account_id": account_id, "positions": len(positions), "orders": len(orders)}

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
