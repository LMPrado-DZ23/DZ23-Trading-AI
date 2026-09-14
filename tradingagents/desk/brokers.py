"""Adaptador de corretora para a mesa: a mesma interface serve PaperBroker e
CCXT (testnet/live), para não existirem dois livros de ordens.

O adaptador NÃO relaxa nada: ele delega à ponte CCXT já auditada, que exige
o gate de capital, modo explícito e — em live — a frase de autorização do
dono. Aqui só se acrescenta a tradução do contrato.
"""
from __future__ import annotations

from decimal import Decimal


class BrokerAdapterError(RuntimeError):
    """Falha na fronteira da corretora — propaga sem inventar resultado."""


class CCXTDeskBroker:
    """Envia ordens reais (testnet por padrão) pelo contrato do executor.

    `final_state` é o estado com a marca de transporte exigida pelo gate de
    capital do Gate A — sem ele a ponte recusa antes de qualquer rede.
    """

    def __init__(self, env: dict, *, final_state: dict, journal_path: str | None = None):
        from tradingagents.brokers.ccxt_bridge import CCXTBridge
        self.bridge = CCXTBridge(env=env, journal_path=journal_path)
        self.final_state = final_state
        self.mode = self.bridge.mode
        self.submitted: dict[str, dict] = {}

    def submit(self, client_order_id: str, instrument: str, side: str,
               quantity: Decimal) -> dict:
        if client_order_id in self.submitted:
            return self.submitted[client_order_id]   # retry não duplica
        try:
            out = self.bridge.submit_order(
                self.final_state, job_id=client_order_id, symbol=instrument,
                side=side, amount=float(quantity))
        except Exception as exc:
            raise BrokerAdapterError(f"{type(exc).__name__}: {exc}"[:300]) from exc
        order = out.get("order") or {}
        result = {
            "id": order.get("id") or out.get("client_order_id"),
            "status": order.get("status") or "acknowledged",
            "filled": Decimal(str(order.get("filled") or 0)),
            "average": Decimal(str(order.get("average") or order.get("price") or 0)),
            "fee": Decimal(str((order.get("fee") or {}).get("cost") or 0)),
        }
        self.submitted[client_order_id] = result
        return result

    def status(self) -> dict:
        return self.bridge.status()


def build_desk_broker(cfg: dict, *, final_state: dict, journal_path: str | None = None,
                      paper_price: Decimal = Decimal("100")):
    """Escolhe a corretora da mesa a partir da configuração salva.

    Sem `TRADINGAGENTS_DESK_BROKER=ccxt` explícito, a mesa opera em PAPER —
    o padrão nunca toca corretora externa por acidente.
    """
    from .execution import PaperBroker
    if (cfg.get("TRADINGAGENTS_DESK_BROKER") or "paper").strip().lower() != "ccxt":
        return PaperBroker(paper_price), "paper"
    env = {k: cfg.get(k, "") for k in
           ("TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE",
            "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "LIVE_TRADING_CONFIRM",
            "LIVE_MAX_ORDER_VALUE", "LIVE_MAX_DAILY_LOSS")}
    broker = CCXTDeskBroker(env, final_state=final_state, journal_path=journal_path)
    return broker, broker.mode


__all__ = ["CCXTDeskBroker", "BrokerAdapterError", "build_desk_broker"]
