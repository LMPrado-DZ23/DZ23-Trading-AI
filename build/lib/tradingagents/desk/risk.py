"""Arbitragem de carteira e risco determinístico (MA-05).

O RiskEngine é código puro com poder de veto: nenhum LLM aumenta limite,
aprova a própria reserva ou altera modo. Reserva é transacional — o mesmo
capital não pode ser prometido duas vezes.
"""
from __future__ import annotations

import threading
import uuid
from decimal import Decimal, ROUND_DOWN

from .models import (Action, DeskMandate, MandateError, OrderIntent,
                     PortfolioDecision, RiskReservation, TradeProposal,
                     default_expiry, utcnow)


class RiskVeto(RuntimeError):
    """Recusa determinística — não negociável por agente algum."""


class PortfolioAllocator:
    """Arbitra propostas concorrentes antes de qualquer reserva."""

    policy = "dz23.allocator/1"

    def arbitrate(self, proposals, *, open_positions=(), owner_by_instrument=None
                  ) -> PortfolioDecision:
        owner_by_instrument = owner_by_instrument or {}
        admitted, vetoed, claimed = [], [], {}
        for p in sorted(proposals, key=lambda x: (-x.confidence, x.agent_id)):
            if not p.is_actionable():
                vetoed.append((p, f"não acionável ({p.action.value})"))
                continue
            owner = owner_by_instrument.get(p.instrument)
            if owner and owner != p.agent_id:
                # Um dono operacional por conta/instrumento na v1.
                vetoed.append((p, f"{p.instrument} já é operado por {owner}"))
                continue
            if p.instrument in claimed:
                winner = claimed[p.instrument]
                if winner.action != p.action:
                    # Nunca abrir compra e venda por discordância de agentes.
                    vetoed.append((p, f"conflito com {winner.agent_id}: direção oposta vetada"))
                else:
                    vetoed.append((p, f"{winner.agent_id} venceu a disputa por confiança"))
                continue
            if p.instrument in open_positions:
                vetoed.append((p, "já existe posição aberta neste instrumento"))
                continue
            claimed[p.instrument] = p
            admitted.append(p)
        return PortfolioDecision(tuple(admitted), tuple(vetoed), self.policy)


class RiskEngine:
    """Calcula quantidade, aplica limites e trava capital de forma atômica."""

    def __init__(self, mandate: DeskMandate):
        self.mandate = mandate
        self._lock = threading.Lock()
        self._reservations: dict[str, RiskReservation] = {}

    # -- consultas -----------------------------------------------------
    @property
    def reserved_total(self) -> Decimal:
        return sum((r.amount for r in self._reservations.values()), Decimal("0"))

    @property
    def available(self) -> Decimal:
        return self.mandate.capital - self.reserved_total

    def open_instruments(self) -> set[str]:
        return {r.instrument for r in self._reservations.values()}

    # -- reserva -------------------------------------------------------
    def reserve(self, proposal: TradeProposal, price: Decimal, *,
                agent_id: str | None = None) -> RiskReservation:
        if price <= 0 or not price.is_finite():
            raise RiskVeto("preço inválido")
        self.mandate.assert_active(instrument=proposal.instrument)
        if proposal.action is Action.SELL:
            raise RiskVeto("venda a descoberto não existe em spot; use redução de posição")
        pct = min(proposal.desired_exposure_pct, self.mandate.max_position_pct)
        if pct <= 0:
            raise RiskVeto("exposição desejada nula")
        with self._lock:  # atômico: duas propostas não travam o mesmo capital
            if len(self._reservations) >= self.mandate.max_open_positions:
                raise RiskVeto("limite de posições simultâneas atingido")
            if proposal.instrument in self.open_instruments():
                raise RiskVeto(f"já há reserva ativa para {proposal.instrument}")
            amount = (self.mandate.capital * pct).quantize(Decimal("0.01"), ROUND_DOWN)
            if amount <= 0:
                raise RiskVeto("valor calculado nulo")
            if amount > self.available:
                raise RiskVeto(
                    f"capital insuficiente: pedido {amount}, disponível {self.available}")
            qty = (amount / price).quantize(Decimal("0.00000001"), ROUND_DOWN)
            if qty <= 0:
                raise RiskVeto("quantidade abaixo do mínimo negociável")
            res = RiskReservation(
                reservation_id=uuid.uuid4().hex[:16], account=self.mandate.account,
                instrument=proposal.instrument, amount=amount, quantity=qty,
                mandate_version=self.mandate.version, expires_at=default_expiry(10),
                agent_id=agent_id or proposal.agent_id)
            self._reservations[res.reservation_id] = res
            return res

    def release(self, reservation_id: str) -> bool:
        """Libera capital. Só deve ser chamada quando nenhum efeito externo
        pode mais existir (cenário 09: timeout NÃO libera)."""
        with self._lock:
            return self._reservations.pop(reservation_id, None) is not None

    # -- intenção ------------------------------------------------------
    def intent(self, reservation: RiskReservation, action: Action, mode,
               worker_id: str, *, idempotency_key: str = "") -> OrderIntent:
        if reservation.reservation_id not in self._reservations:
            raise RiskVeto("reserva inexistente ou já liberada")
        if utcnow() >= reservation.expires_at:
            raise RiskVeto("reserva expirada")
        self.mandate.assert_active(instrument=reservation.instrument)
        return OrderIntent(intent_id=uuid.uuid4().hex[:16], reservation=reservation,
                           action=action, mode=mode, owner_worker=worker_id,
                           expires_at=default_expiry(5),
                           idempotency_key=idempotency_key or uuid.uuid4().hex)


__all__ = ["PortfolioAllocator", "RiskEngine", "RiskVeto", "MandateError"]
