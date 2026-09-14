"""Arbitragem de carteira e risco determinístico (MA-05).

O RiskEngine é código puro com poder de veto: nenhum LLM aumenta limite,
aprova a própria reserva ou altera modo. Reserva é transacional — o mesmo
capital não pode ser prometido duas vezes.
"""
from __future__ import annotations

import threading
import uuid
from decimal import ROUND_DOWN, Decimal

from .models import (
    Action,
    DeskMandate,
    MandateError,
    OrderIntent,
    PortfolioDecision,
    RiskReservation,
    TradeProposal,
    default_expiry,
    utcnow,
)
from .sizing import limite_de_correlacao


class RiskVeto(RuntimeError):
    """Recusa determinística — não negociável por agente algum."""


class PortfolioAllocator:
    """Arbitra propostas concorrentes antes de qualquer reserva."""

    policy = "dz23.allocator/1"

    def arbitrate(self, proposals, *, open_positions=(), owner_by_instrument=None,
                  exposicoes: dict | None = None) -> PortfolioDecision:
        owner_by_instrument = owner_by_instrument or {}
        exposicoes = dict(exposicoes) if exposicoes is not None else {}
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
            # CORRELAÇÃO: BTC e ETH andam juntos. Comprar os dois não é
            # diversificar — é dobrar a mesma aposta, e perder o dobro junto.
            if exposicoes:
                cabe, motivo = limite_de_correlacao(
                    p.instrument, p.desired_exposure_pct, exposicoes)
                if not cabe:
                    vetoed.append((p, motivo))
                    continue
                exposicoes[p.instrument] = p.desired_exposure_pct
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

    def consume(self, reservation_id: str, amount: Decimal) -> Decimal:
        """Consome PARTE de uma reserva, devolvendo o saldo restante.

        O livro consumia a reserva proporcionalmente a cada fill enquanto o
        risco mantinha o valor integral até o terminal: entre o primeiro
        parcial e o último, `reserved_total` e `available` mentiam — e é
        `available` que decide se a próxima ordem cabe.

        Consumir MAIS que o reservado é recusado: seria admitir que a ordem
        executou além da cobertura, o que só pode ser erro de contabilidade.
        """
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            if reserva is None:
                raise RiskVeto(
                    f"reserva {reservation_id} não está viva: nada a consumir")
            valor = Decimal(str(amount))
            if valor <= 0:
                raise RiskVeto("consumo precisa ser positivo")
            if valor > reserva.amount:
                raise RiskVeto(
                    f"consumo de {valor} excede o reservado ({reserva.amount})")
            restante = reserva.amount - valor
            if restante <= 0:
                del self._reservations[reservation_id]
                return Decimal("0")
            # RiskReservation é imutável por desenho: substitui-se o registro
            # inteiro, em vez de mutar um valor financeiro no lugar.
            from dataclasses import replace
            self._reservations[reservation_id] = replace(reserva, amount=restante)
            return restante

    def reservation_amount(self, reservation_id: str) -> Decimal:
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            return reserva.amount if reserva else Decimal("0")

    def active_reservations(self) -> list[dict]:
        """Reservas vivas, para persistência.

        O snapshot chamava este método sem ele existir: `getattr` devolvia
        nada e a lista persistida era SEMPRE vazia. Depois de um reinício, o
        capital reservado de uma ordem incerta voltava a parecer disponível.
        """
        with self._lock:
            return [{"reservation_id": res.reservation_id,
                     "account": res.account,
                     "agent_id": res.agent_id,
                     "instrument": res.instrument,
                     "amount": str(res.amount),
                     "quantity": str(res.quantity),
                     "mandate_version": res.mandate_version,
                     "expires_at": res.expires_at.isoformat()
                     if res.expires_at else None}
                    for res in self._reservations.values()]

    def restore_reservations(self, docs: list[dict]) -> int:
        """Reidrata reservas. Capital reservado não pode ressuscitar como
        disponível só porque o processo reiniciou."""
        from .models import RiskReservation
        restauradas = 0
        with self._lock:
            for doc in docs or []:
                try:
                    from datetime import datetime as _dt
                    expira = doc.get("expires_at")
                    res = RiskReservation(
                        reservation_id=doc["reservation_id"],
                        account=doc.get("account", ""),
                        instrument=doc["instrument"],
                        amount=Decimal(str(doc["amount"])),
                        quantity=Decimal(str(doc.get("quantity") or 0)),
                        mandate_version=doc.get("mandate_version", 1),
                        expires_at=_dt.fromisoformat(expira) if expira else None,
                        agent_id=doc.get("agent_id", ""))
                except (KeyError, ValueError, TypeError):
                    continue
                self._reservations[res.reservation_id] = res
                restauradas += 1
        return restauradas

    def release(self, reservation_id: str) -> bool:
        """Libera capital. Só deve ser chamada quando nenhum efeito externo
        pode mais existir (cenário 09: timeout NÃO libera)."""
        with self._lock:
            return self._reservations.pop(reservation_id, None) is not None

    # -- intenção ------------------------------------------------------
    def intent(self, reservation: RiskReservation, action: Action, mode,
               worker_id: str, *, idempotency_key: str = "",
               provenance: dict | None = None) -> OrderIntent:
        if reservation.reservation_id not in self._reservations:
            raise RiskVeto("reserva inexistente ou já liberada")
        if utcnow() >= reservation.expires_at:
            raise RiskVeto("reserva expirada")
        self.mandate.assert_active(instrument=reservation.instrument)
        return OrderIntent(intent_id=uuid.uuid4().hex[:16], reservation=reservation,
                           action=action, mode=mode, owner_worker=worker_id,
                           expires_at=default_expiry(5),
                           idempotency_key=idempotency_key or uuid.uuid4().hex,
                           provenance=provenance)


__all__ = ["PortfolioAllocator", "RiskEngine", "RiskVeto", "MandateError"]
