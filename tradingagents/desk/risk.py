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

    def __init__(self, mandate: DeskMandate, carteira=None):
        """`carteira` é o Ledger — a AUTORIDADE econômica.

        Sem ele o risco só conhece reservas e usa o capital do mandato como
        referência, o que ignora o dinheiro já transformado em posição. O
        risco aplica política; não mantém uma segunda contabilidade.
        """
        self.mandate = mandate
        self.carteira = carteira
        self._lock = threading.Lock()
        self._reservations: dict[str, RiskReservation] = {}
        self._concluidas: dict[str, dict] = {}
        self._deficits: dict[str, Decimal] = {}

    # -- consultas -----------------------------------------------------
    @property
    def reserved_total(self) -> Decimal:
        # O que ainda está travado é o RESTANTE, não o valor original.
        return sum((r.remaining_amount for r in self._reservations.values()),
                   Decimal("0"))

    @property
    def available(self) -> Decimal:
        """Capital que pode ser alocado AGORA.

        Antes era `capital - reservas`: depois do fill terminal a reserva
        some e os mesmos 250 voltavam a parecer livres, com a posição ainda
        aberta — o mesmo dinheiro podia ser alocado indefinidamente. Agora o
        caixa vem do livro, que já desconta o que virou posição.

        O capital do mandato continua valendo como TETO de política: lucro
        realizado não amplia sozinho o limite estrutural da mesa.
        """
        if self.carteira is None:
            return self.mandate.capital - self.reserved_total
        caixa = self.carteira.cash_available()
        return caixa - self.reserved_total + self._reservado_no_livro()

    def _reservado_no_livro(self) -> Decimal:
        """O livro já move o reservado para fora do caixa; sem isto a reserva
        seria descontada duas vezes."""
        if self.carteira is None:
            return Decimal("0")
        return self.carteira.reserved_cash()

    def attach_portfolio_ledger(self, carteira) -> None:
        """Religa o risco à carteira econômica, validando o contrato.

        Setter livre deixaria passar um objeto sem a visão de capital — e o
        erro só apareceria como número errado, não como falha.
        """
        for metodo in ("cash_available", "reserved_cash", "invested_cost",
                       "open_positions_detail"):
            if not callable(getattr(carteira, metodo, None)):
                raise TypeError(
                    f"carteira sem {metodo}(): não serve como fonte econômica")
        self.carteira = carteira

    def invested_cost(self) -> Decimal:
        if self.carteira is None:
            return Decimal("0")
        return self.carteira.invested_cost()

    def gross_exposure(self) -> Decimal:
        """Exposição bruta: posição aberta + reservas ainda executáveis.

        Uma posição totalmente preenchida não some do risco só porque a
        reserva dela acabou.
        """
        return self.invested_cost() + self.reserved_total

    def open_instruments(self) -> set[str]:
        """Instrumentos com exposição viva: reservas E posições abertas.

        Derivar só das reservas fazia o instrumento sumir da lista assim que
        o fill terminava — e a política de 'uma entrada por instrumento'
        deixava de valer exatamente quando havia posição de verdade.
        """
        abertos = {r.instrument for r in self._reservations.values()}
        if self.carteira is not None:
            abertos |= {i for i, d in self.carteira.open_positions_detail().items()
                        if Decimal(str(d["quantidade"])) > 0}
        return abertos

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
            # Posições ABERTAS contam para o limite: uma posição preenchida
            # sem reserva continua sendo uma posição.
            if len(self.open_instruments()) >= self.mandate.max_open_positions:
                raise RiskVeto("limite de posições simultâneas atingido")
            if proposal.instrument in self.open_instruments():
                raise RiskVeto(f"já há reserva ativa para {proposal.instrument}")
            amount = (self.mandate.capital * pct).quantize(Decimal("0.01"), ROUND_DOWN)
            if amount <= 0:
                raise RiskVeto("valor calculado nulo")
            # COBERTURA DURA = alvo + folga. A folga precisa caber no caixa
            # agora: reservar só o alvo torcendo para os centavos aparecerem
            # depois é o que deixava a folga a descoberto.
            folga = (amount * getattr(self.mandate, "slippage_buffer_pct",
                                      Decimal("0"))).quantize(
                Decimal("0.00000001"))
            dura = amount + folga
            if dura > self.available:
                raise RiskVeto(
                    f"capital insuficiente: cobertura necessária {dura} "
                    f"(alvo {amount} + folga {folga}), disponível {self.available}")
            # A quantidade sai do ALVO: folga não aumenta o que se negocia.
            qty = (amount / price).quantize(Decimal("0.00000001"), ROUND_DOWN)
            if qty <= 0:
                raise RiskVeto("quantidade abaixo do mínimo negociável")
            res = RiskReservation(
                reservation_id=uuid.uuid4().hex[:16], account=self.mandate.account,
                instrument=proposal.instrument, amount=amount, quantity=qty,
                buffer_amount=folga,
                mandate_version=self.mandate.version, expires_at=default_expiry(10),
                agent_id=agent_id or proposal.agent_id)
            self._reservations[res.reservation_id] = res
            return res

    def reserve_exato(self, *, instrument: str, alvo: Decimal,
                      agent_id: str = "") -> RiskReservation:
        """Reserva um notional explícito — usada quando o alvo não vem de
        percentual do mandato. Aplica a MESMA regra de cobertura dura."""
        with self._lock:
            folga = (alvo * getattr(self.mandate, "slippage_buffer_pct",
                                    Decimal("0"))).quantize(Decimal("0.00000001"))
            if alvo + folga > self.available:
                raise RiskVeto(
                    f"capital insuficiente: cobertura necessária {alvo + folga}, "
                    f"disponível {self.available}")
            res = RiskReservation(
                reservation_id=uuid.uuid4().hex[:16],
                account=self.mandate.account, instrument=instrument,
                amount=alvo, quantity=Decimal("1"), buffer_amount=folga,
                mandate_version=self.mandate.version,
                expires_at=default_expiry(10), agent_id=agent_id)
            self._reservations[res.reservation_id] = res
            return res

    def consume(self, reservation_id: str, amount: Decimal,
                quantidade: Decimal | None = None) -> Decimal:
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
            if valor > reserva.remaining_amount:
                raise RiskVeto(
                    f"consumo de {valor} excede o restante da reserva "
                    f"({reserva.remaining_amount})")
            qtd = Decimal(str(quantidade)) if quantidade is not None else None
            if qtd is not None:
                if qtd <= 0:
                    raise RiskVeto("quantidade consumida precisa ser positiva")
                if reserva.filled_quantity + qtd > reserva.original_quantity:
                    raise RiskVeto(
                        f"quantidade {qtd} excede a quantidade restante da "
                        f"reserva ({reserva.remaining_quantity})")
            # O ORIGINAL nunca é tocado: só os acumulados crescem. Valor e
            # quantidade avançam JUNTOS, no mesmo lock — estado parcial entre
            # os dois deixaria a cobertura inconsistente.
            from dataclasses import replace
            consumido = reserva.consumed_amount + valor
            preenchida = reserva.filled_quantity + (qtd or Decimal("0"))
            atualizada = replace(reserva, consumed_amount=consumido,
                                 filled_quantity=preenchida)
            if atualizada.remaining_amount <= 0:
                del self._reservations[reservation_id]
                return Decimal("0")
            self._reservations[reservation_id] = atualizada
            return atualizada.remaining_amount

    # ------------------------------------------------ custo real vs cobertura
    def registrar_execucao(self, reservation_id: str, *, custo: Decimal,
                           quantidade: Decimal, terminal: bool) -> dict:
        """Confronta o CUSTO REAL com a cobertura e conclui a reserva.

        Consumir proporcionalmente à quantidade estima a alocação; não prova
        cobertura. O custo real é quantidade × preço executado + taxa, e com
        slippage ele pode passar do reservado. Quando passa, a execução NÃO
        é desfeita — a corretora já executou — mas o déficit é registrado e o
        risco novo é travado.
        """
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            if reserva is None:
                # Reserva já concluída: um custo extra aqui é déficit puro.
                self._deficits[reservation_id] = (
                    self._deficits.get(reservation_id, Decimal("0")) + custo)
                return {"estado": "DEFICIT", "deficit": str(custo),
                        "sobra": "0"}

            # A folga JÁ está dentro de remaining_amount, porque foi
            # separada no caixa na hora da reserva. Somá-la de novo aqui
            # autorizaria gasto não financiado.
            limite = reserva.remaining_amount
            deficit = custo - limite if custo > limite else Decimal("0")
            consumido = min(custo, limite)

            from dataclasses import replace
            atualizada = replace(
                reserva,
                consumed_amount=reserva.consumed_amount + consumido,
                filled_quantity=reserva.filled_quantity + quantidade)
            if deficit > 0:
                self._deficits[reservation_id] = (
                    self._deficits.get(reservation_id, Decimal("0")) + deficit)

            if terminal or atualizada.remaining_quantity <= 0:
                sobra = atualizada.remaining_amount
                # Conclusão fica registrada: apagar a reserva sem deixar
                # rastro tiraria a única evidência do consumo terminal.
                self._concluidas[reservation_id] = {
                    "reservation_id": reservation_id,
                    "estado": "DEFICIT" if deficit > 0 else (
                        "CONSUMED" if sobra <= 0 else "RELEASED"),
                    "original_amount": str(reserva.original_amount),
                    "consumed_amount": str(atualizada.consumed_amount),
                    "sobra_liberada": str(max(sobra, Decimal("0"))),
                    "original_quantity": str(reserva.original_quantity),
                    "filled_quantity": str(atualizada.filled_quantity),
                    "deficit": str(deficit),
                }
                del self._reservations[reservation_id]
                return {"estado": self._concluidas[reservation_id]["estado"],
                        "deficit": str(deficit), "sobra": str(max(sobra, Decimal("0")))}

            self._reservations[reservation_id] = atualizada
            return {"estado": "PARTIALLY_CONSUMED", "deficit": str(deficit),
                    "sobra": "0"}

    def incidentes_doc(self) -> dict:
        """Incidentes para persistência — reiniciar não resolve déficit."""
        return {"deficits": {k: str(v) for k, v in self._deficits.items()},
                "conclusoes": self._concluidas}

    def restaurar_incidentes(self, doc: dict | None) -> None:
        if not doc:
            return
        for k, v in (doc.get("deficits") or {}).items():
            self._deficits[k] = Decimal(str(v))
        self._concluidas.update(doc.get("conclusoes") or {})

    def terminalizar(self, reservation_id: str, *, motivo: str) -> Decimal:
        """Encerra a reserva e devolve QUANTO deve voltar ao caixa.

        Atômico: dois eventos de cancelamento simultâneos não podem devolver
        o dinheiro duas vezes. Quem chega depois recebe zero.
        """
        with self._lock:
            reserva = self._reservations.pop(reservation_id, None)
            if reserva is None:
                return Decimal("0")     # já terminalizada: no-op seguro
            restante = reserva.remaining_amount
            self._concluidas[reservation_id] = {
                "reservation_id": reservation_id,
                "estado": "RELEASED",
                "motivo": motivo[:200],
                "original_amount": str(reserva.original_amount),
                "hard_reserved_amount": str(reserva.hard_reserved_amount),
                "consumed_amount": str(reserva.consumed_amount),
                "sobra_liberada": str(max(restante, Decimal("0"))),
                "original_quantity": str(reserva.original_quantity),
                "filled_quantity": str(reserva.filled_quantity),
                "deficit": "0",
            }
            return max(restante, Decimal("0"))

    def registrar_divergencia_de_cobertura(self, reservation_id: str, *,
                                           faltante: Decimal) -> None:
        """O risco tinha reserva que o livro não travou.

        Não é déficit de execução (nada foi gasto a mais), mas é
        inconsistência entre as duas visões e precisa aparecer.
        """
        doc = self._concluidas.get(reservation_id) or {}
        doc["cobertura_nao_travada"] = str(faltante)
        doc["estado"] = "DIVERGENTE"
        self._concluidas[reservation_id] = doc

    def conclusao(self, reservation_id: str) -> dict | None:
        """Como esta reserva terminou — evidência para auditoria."""
        return self._concluidas.get(reservation_id)

    def deficit_total(self) -> Decimal:
        """Quanto foi executado ALÉM da cobertura reservada."""
        return sum(self._deficits.values(), Decimal("0"))

    def capital_state_consistent(self) -> tuple[bool, str]:
        """O estado de capital fecha com a política?

        Livro balanceado prova aritmética; isto prova POLÍTICA.
        """
        deficit = self.deficit_total()
        if deficit > 0:
            return False, (f"déficit de cobertura de {deficit}: houve execução "
                           "mais cara que o capital reservado")
        if self.carteira is not None:
            reservado_livro = self.carteira.reserved_cash()
            if reservado_livro < 0:
                return False, f"reservado negativo no livro ({reservado_livro})"
            # POR RESERVA, não só pelo total: R1=50/R2=150 soma igual a
            # R1=100/R2=100, e a conferência agregada passaria com o dinheiro
            # de uma ordem no lugar da outra.
            with self._lock:
                vivas = dict(self._reservations)
            for rid, reserva in vivas.items():
                no_livro = self.carteira.reserved_cash(rid)
                if no_livro != reserva.remaining_amount:
                    return False, (
                        f"RESERVATION_LEDGER_MISMATCH reservation_id={rid}: "
                        f"livro {no_livro} vs risco {reserva.remaining_amount}")
            if reservado_livro != self.reserved_total:
                return False, (
                    f"reservado divergente: livro {reservado_livro} vs risco "
                    f"{self.reserved_total}")
        if self.carteira is not None and self.carteira.cash_available() < 0:
            return False, (f"caixa negativo ({self.carteira.cash_available()}): "
                           "há dinheiro comprometido além do disponível")
        if self.reserved_total < 0:
            return False, "reservado negativo"
        return True, "capital consistente"

    def bind(self, reservation_id: str, client_order_id: str) -> None:
        """Vincula a reserva a UMA ordem. Idempotente para o mesmo vínculo.

        Dentro do mesmo lock do consumo: dois workers tentando vincular a
        mesma reserva a ordens diferentes não podem vencer os dois, senão o
        mesmo capital financiaria duas ordens na corretora.
        """
        if not client_order_id:
            raise RiskVeto("vínculo exige identificador de ordem")
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            if reserva is None:
                raise RiskVeto(
                    f"reserva {reservation_id} não está viva: nada a vincular")
            atual = reserva.bound_client_order_id
            if atual and atual != client_order_id:
                raise RiskVeto(
                    f"reserva {reservation_id} já está vinculada à ordem "
                    f"{atual}: uma reserva financia uma ordem só")
            if atual == client_order_id:
                return                      # idempotente
            from dataclasses import replace
            self._reservations[reservation_id] = replace(
                reserva, bound_client_order_id=client_order_id)

    def vinculo(self, reservation_id: str) -> str:
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            return reserva.bound_client_order_id if reserva else ""

    def reserva(self, reservation_id: str):
        """A reserva viva, com original/consumido/restante."""
        with self._lock:
            return self._reservations.get(reservation_id)

    def reservation_amount(self, reservation_id: str) -> Decimal:
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            return reserva.remaining_amount if reserva else Decimal("0")

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
                     # Acumulados: sem eles o reinício devolveria a reserva
                     # ao valor cheio, e o consumo já feito sumiria.
                     "consumed_amount": str(res.consumed_amount),
                     "filled_quantity": str(res.filled_quantity),
                     # Derivados, para quem lê o documento não precisar
                     # recalcular (e errar) a conta.
                     "bound_client_order_id": res.bound_client_order_id,
                     "buffer_amount": str(res.buffer_amount),
                     "remaining_amount": str(res.remaining_amount),
                     "remaining_quantity": str(res.remaining_quantity),
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
                        consumed_amount=Decimal(str(
                            doc.get("consumed_amount") or 0)),
                        filled_quantity=Decimal(str(
                            doc.get("filled_quantity") or 0)),
                        bound_client_order_id=doc.get(
                            "bound_client_order_id") or "",
                        buffer_amount=Decimal(str(doc.get("buffer_amount") or 0)),
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
