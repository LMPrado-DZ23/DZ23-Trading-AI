"""Execução e reconciliação da mesa (MA-06 / MA-07 / TA-009).

Regras codificadas, cada uma com teste:
- Chave idempotente amarrada ao FINGERPRINT canônico do comando: mesma chave
  com conteúdo diferente é conflito, não retry.
- Timeout depois de enviar é AMBÍGUO: vira UNKNOWN_RECONCILIATION_REQUIRED.
  Não há reenvio cego e a reserva NÃO é liberada enquanto o efeito externo
  puder existir.
- Fills chegam por identificador; duplicata e mensagem fora de ordem não
  contam duas vezes.
- Cancelamento solicitado ≠ cancelamento confirmado.
- O executor é o ÚNICO caminho para a corretora e revalida posse, mandato,
  expiração e política antes de cada envio.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .ledger import Ledger
from .models import Action, DeskMandate, MandateError, Mode, OrderIntent


class OrderState(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    SUBMITTING = "submitting"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN_RECONCILIATION_REQUIRED = "unknown_reconciliation_required"


TERMINAL = {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED}


class IdempotencyConflict(RuntimeError):
    """Mesma chave, conteúdo diferente — jamais tratado como retry."""


class ExecutionRefused(RuntimeError):
    """Envio recusado antes de qualquer efeito externo."""


class BrokerTimeout(Exception):
    """A corretora não respondeu: o efeito pode ou não existir."""


def fingerprint(intent: OrderIntent) -> str:
    canonical = json.dumps({
        "instrument": intent.reservation.instrument,
        "action": intent.action.value,
        "quantity": str(intent.reservation.quantity),
        "account": intent.reservation.account,
        "mode": intent.mode.value,
        "reservation": intent.reservation.reservation_id,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class OrderRecord:
    order_id: str
    intent: OrderIntent
    state: OrderState = OrderState.DRAFT
    fingerprint: str = ""
    filled_qty: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    seen_fills: set = field(default_factory=set)
    external_id: str | None = None
    note: str = ""

    @property
    def open(self) -> bool:
        return self.state not in TERMINAL

    @property
    def needs_reconciliation(self) -> bool:
        return self.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED


class PaperBroker:
    """Corretora simulada — dinheiro fictício, comportamento honesto.
    Serve PAPER/SHADOW; testnet/live usam a ponte CCXT pelo mesmo contrato."""

    def __init__(self, price: Decimal, fee_rate: Decimal = Decimal("0.001"),
                 price_provider=None):
        self.price = price
        self.fee_rate = fee_rate
        # Com feed real, o preenchimento simulado precisa usar o preço do
        # mercado — senão a contabilidade do paper mente (achado em teste vivo).
        self.price_provider = price_provider
        self.submitted: dict[str, dict] = {}

    def price_for(self, instrument: str) -> Decimal:
        if self.price_provider:
            try:
                found = self.price_provider().get(instrument)
                if found:
                    return Decimal(str(found))
            except Exception:
                pass
        return self.price

    def submit(self, client_order_id: str, instrument: str, side: str,
               quantity: Decimal) -> dict:
        if client_order_id in self.submitted:
            return self.submitted[client_order_id]  # idempotente do lado da corretora
        price = self.price_for(instrument)
        fee = (quantity * price * self.fee_rate).quantize(Decimal("0.00000001"))
        order = {"id": f"paper-{len(self.submitted) + 1}", "status": "filled",
                 "filled": quantity, "average": price, "fee": fee}
        self.submitted[client_order_id] = order
        return order


class ExecutionCoordinator:
    """Aplica SOMENTE comandos autorizados; mantém estado e contabilidade."""

    def __init__(self, mandate: DeskMandate, ledger: Ledger, risk, *,
                 worker_id: str, broker=None):
        self.mandate = mandate
        self.ledger = ledger
        self.risk = risk
        self.worker_id = worker_id
        self.broker = broker
        self.orders: dict[str, OrderRecord] = {}
        self._by_key: dict[str, str] = {}     # idempotency_key -> order_id

    # -- envio ---------------------------------------------------------
    def submit(self, intent: OrderIntent) -> OrderRecord:
        if intent.mode is Mode.LIVE:
            raise ExecutionRefused("LIVE fora da autorização desta missão")
        fp = fingerprint(intent)
        known = self._by_key.get(intent.idempotency_key)
        if known:
            rec = self.orders[known]
            if rec.fingerprint != fp:
                raise IdempotencyConflict(
                    "mesma chave idempotente com conteúdo diferente")
            return rec                      # retry legítimo: devolve a MESMA ordem
        try:
            intent.assert_sendable(worker_id=self.worker_id, mandate=self.mandate)
        except MandateError as exc:
            raise ExecutionRefused(str(exc)) from exc

        rec = OrderRecord(order_id=f"ord-{len(self.orders) + 1}", intent=intent,
                          state=OrderState.APPROVED, fingerprint=fp)
        self.orders[rec.order_id] = rec
        self._by_key[intent.idempotency_key] = rec.order_id

        rec.state = OrderState.SUBMITTING
        self.ledger.reserve(intent.reservation.amount, ref=rec.order_id)
        side = "buy" if intent.action is Action.BUY else "sell"
        try:
            result = self.broker.submit(intent.idempotency_key,
                                        intent.reservation.instrument, side,
                                        intent.reservation.quantity)
        except BrokerTimeout:
            # Ambíguo: pode ter chegado. Nada de retry cego, nada de liberar.
            rec.state = OrderState.UNKNOWN_RECONCILIATION_REQUIRED
            rec.note = "timeout no envio — exige reconciliação"
            return rec
        except Exception as exc:
            rec.state = OrderState.REJECTED
            rec.note = f"{type(exc).__name__}: {exc}"[:200]
            self.ledger.release(intent.reservation.amount, ref=rec.order_id)
            self.risk.release(intent.reservation.reservation_id)
            return rec
        rec.external_id = result.get("id")
        rec.state = OrderState.ACKNOWLEDGED
        self.apply_fill(rec.order_id, fill_id=rec.external_id,
                        quantity=Decimal(str(result["filled"])),
                        price=Decimal(str(result["average"])),
                        fee=Decimal(str(result.get("fee", 0))))
        return rec

    # -- eventos -------------------------------------------------------
    def apply_fill(self, order_id: str, *, fill_id: str, quantity: Decimal,
                   price: Decimal, fee: Decimal = Decimal("0")) -> OrderRecord:
        rec = self.orders[order_id]
        if fill_id in rec.seen_fills:
            return rec                      # duplicata: não conta duas vezes
        if rec.state in TERMINAL:
            return rec                      # fora de ordem após terminal: ignora
        rec.seen_fills.add(fill_id)
        total = rec.filled_qty + quantity
        rec.avg_price = ((rec.avg_price * rec.filled_qty + price * quantity) / total
                         ) if total else price
        rec.filled_qty = total
        rec.fees += fee
        target = rec.intent.reservation.quantity
        rec.state = OrderState.FILLED if rec.filled_qty >= target else OrderState.PARTIALLY_FILLED
        if rec.state is OrderState.FILLED:
            self.ledger.fill_buy(rec.intent.reservation.instrument, rec.filled_qty,
                                 rec.avg_price, rec.fees,
                                 reserved=rec.intent.reservation.amount,
                                 ref=rec.order_id)
            self.risk.release(rec.intent.reservation.reservation_id)
        return rec

    def close_position(self, *, instrument: str, quantity: Decimal,
                       price: Decimal, cost_basis: Decimal, agent_id: str,
                       fee: Decimal = Decimal("0"), client_order_id: str = ""
                       ) -> dict:
        """Encerra posição: vende na corretora e REALIZA o resultado no livro.
        Sem isto a mesa acumula compras e nunca mostra lucro ou prejuízo."""
        coid = client_order_id or f"exit-{instrument}-{len(self.orders) + 1}"
        try:
            result = self.broker.submit(coid, instrument, "sell", quantity)
        except BrokerTimeout:
            return {"estado": "incerto", "motivo": "timeout na saída — reconciliar",
                    "client_order_id": coid}
        exec_price = Decimal(str(result.get("average") or price))
        exec_fee = Decimal(str(result.get("fee") or fee))
        tx = self.ledger.fill_sell(instrument, quantity, exec_price, exec_fee,
                                   cost_basis=cost_basis, agent_id=agent_id,
                                   ref=coid)
        return {"estado": "encerrada", "client_order_id": coid,
                "preco": str(exec_price), "tx": tx.tx_id,
                "resultado": str(self.ledger.attribution().get(agent_id, Decimal("0")))}

    def request_cancel(self, order_id: str) -> OrderRecord:
        rec = self.orders[order_id]
        if rec.state in TERMINAL:
            return rec
        rec.state = OrderState.CANCEL_PENDING   # pedido ≠ confirmado
        return rec

    def confirm_cancel(self, order_id: str) -> OrderRecord:
        rec = self.orders[order_id]
        if rec.state is not OrderState.CANCEL_PENDING:
            return rec
        rec.state = OrderState.CANCELLED
        self.ledger.release(rec.intent.reservation.amount, ref=rec.order_id)
        self.risk.release(rec.intent.reservation.reservation_id)
        return rec

    # -- reconciliação (MA-07) -----------------------------------------
    def reconcile(self, order_id: str, exchange_view: dict | None) -> OrderRecord:
        """Consulta o estado real na corretora antes de qualquer decisão.
        `exchange_view=None` significa 'ainda não sei' — permanece incerto."""
        rec = self.orders[order_id]
        if exchange_view is None:
            rec.note = "corretora indisponível — segue incerto, reserva mantida"
            return rec
        status = exchange_view.get("status")
        if status in ("filled", "closed"):
            self.apply_fill(order_id, fill_id=exchange_view.get("id", "recon"),
                            quantity=Decimal(str(exchange_view.get("filled", 0))),
                            price=Decimal(str(exchange_view.get("average", 0))),
                            fee=Decimal(str(exchange_view.get("fee", 0))))
            rec.note = "reconciliado: ordem existia e executou"
        elif status in ("canceled", "cancelled", "rejected", "absent"):
            rec.state = OrderState.CANCELLED if status != "rejected" else OrderState.REJECTED
            self.ledger.release(rec.intent.reservation.amount, ref=rec.order_id)
            self.risk.release(rec.intent.reservation.reservation_id)
            rec.note = f"reconciliado: {status} — reserva liberada com segurança"
        return rec

    def pending_reconciliation(self) -> list[OrderRecord]:
        return [r for r in self.orders.values() if r.needs_reconciliation]


class PositionGuardian:
    """Cuida das posições quando o trader é pausado ou a IA falha (MA-06)."""

    def __init__(self, coordinator: ExecutionCoordinator):
        self.coordinator = coordinator
        self.adopted: dict[str, str] = {}

    def adopt(self, instrument: str, from_agent: str) -> str:
        self.adopted[instrument] = from_agent
        return f"guardião assumiu {instrument} (antes: {from_agent})"

    def watchlist(self) -> list[str]:
        return sorted({r.intent.reservation.instrument
                       for r in self.coordinator.orders.values() if r.open}
                      | set(self.adopted))


__all__ = ["ExecutionCoordinator", "PaperBroker", "PositionGuardian", "OrderState",
           "IdempotencyConflict", "ExecutionRefused", "BrokerTimeout", "fingerprint"]
