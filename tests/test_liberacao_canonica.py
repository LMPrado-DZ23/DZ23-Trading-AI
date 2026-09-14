"""Liberação de reserva: uma função só, uma vez só.

No caminho de rejeição o livro recebia a reserva de volta DUAS vezes — uma
pelo `ledger.release` explícito e outra dentro de `_liberar_reserva_de`. O
resultado era caixa maior que o capital e RESERVED negativo: dinheiro criado
do nada. E o cancelamento devolvia o ALVO, não a cobertura dura restante.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, OrderState
from tradingagents.desk.intents import ExecutionIntent, Kind, Side
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


class Corretora:
    mode = "testnet"

    def __init__(self, erro=None):
        self.erro = erro
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append(coid)
        if self.erro:
            raise self.erro
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="1000", buffer="0", broker=None):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital),
                    slippage_buffer_pct=Decimal(buffer))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=broker or Corretora())
    return m, risco, livro, coord


def reservar(risco):
    p = TradeProposal(agent_id="A", strategy="s", instrument="BTC/USDT",
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal("0.25"),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal("100"), agent_id="A")


def enviar(coord, res, coid="coid-A"):
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY, instrument="BTC/USDT",
                         quantity=res.quantity, agent_id="A",
                         account="conta-1", client_order_id=coid,
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    return coord.submit_intent(ei)


class TestRejeicaoNaoCriaDinheiro:
    def test_a_rejection_restores_exactly_the_initial_state(self):
        m, risco, livro, coord = sistema(
            broker=Corretora(erro=RuntimeError("rejected")))
        res = reservar(risco)
        rec = enviar(coord, res)
        assert rec.state is OrderState.REJECTED
        assert livro.cash_available() == Decimal("1000"), (
            f"caixa virou {livro.cash_available()}: a reserva voltou duas vezes")
        assert livro.reserved_cash() == Decimal("0")
        assert risco.reserved_total == Decimal("0")
        assert not risco.active_reservations()

    def test_the_same_holds_with_a_funded_buffer(self):
        m, risco, livro, coord = sistema(
            buffer="0.01", broker=Corretora(erro=RuntimeError("rejected")))
        res = reservar(risco)
        enviar(coord, res)
        assert livro.cash_available() == Decimal("1000")
        assert livro.reserved_cash() == Decimal("0"), (
            "sobrou folga presa depois da rejeição")
        assert risco.reserved_total == Decimal("0")

    def test_reserved_never_goes_negative(self):
        m, risco, livro, coord = sistema(
            broker=Corretora(erro=RuntimeError("rejected")))
        res = reservar(risco)
        enviar(coord, res)
        assert livro.reserved_cash() >= 0


class TestCancelamentoDevolveORestante:
    def _com_parcial(self, buffer="0.01"):
        m, risco, livro, coord = sistema(buffer=buffer)
        res = reservar(risco)
        rec = enviar(coord, res)
        # executa 40% a 100
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity * Decimal("0.4"),
                           preco=Decimal("100"), taxa=Decimal("0"), fill_id="f1")
        return m, risco, livro, coord, rec, res

    def test_cancel_after_partial_releases_only_what_is_left(self):
        m, risco, livro, coord, rec, res = self._com_parcial()
        restante = risco.reserva(res.reservation_id).remaining_amount
        caixa_antes = livro.cash_available()
        rec.state = OrderState.CANCEL_PENDING
        coord.confirm_cancel(rec.order_id)
        assert livro.cash_available() == caixa_antes + restante, (
            "a devolução não foi exatamente o restante da cobertura dura")
        assert livro.reserved_cash() == Decimal("0")
        assert risco.reserved_total == Decimal("0")

    def test_cancel_before_any_fill_releases_the_hard_coverage(self):
        m, risco, livro, coord = sistema(buffer="0.01")
        res = reservar(risco)
        rec = enviar(coord, res)
        rec.state = OrderState.CANCEL_PENDING
        coord.confirm_cancel(rec.order_id)
        assert livro.cash_available() == Decimal("1000")
        assert livro.reserved_cash() == Decimal("0")


class TestTerminalizacaoIdempotente:
    def test_releasing_twice_does_not_create_money(self):
        m, risco, livro, coord = sistema(buffer="0.01")
        res = reservar(risco)
        rec = enviar(coord, res)
        coord.liberar_reserva_restante(res.reservation_id, motivo="teste",
                                       order_id=rec.order_id)
        caixa = livro.cash_available()
        coord.liberar_reserva_restante(res.reservation_id, motivo="repetido",
                                       order_id=rec.order_id)
        assert livro.cash_available() == caixa, (
            "a segunda liberação movimentou dinheiro")
        assert livro.reserved_cash() == Decimal("0")

    def test_concurrent_releases_move_money_once(self):
        import threading
        m, risco, livro, coord = sistema(buffer="0.01")
        res = reservar(risco)
        rec = enviar(coord, res)

        def liberar():
            coord.liberar_reserva_restante(res.reservation_id, motivo="c",
                                           order_id=rec.order_id)

        fios = [threading.Thread(target=liberar) for _ in range(4)]
        for f in fios:
            f.start()
        for f in fios:
            f.join()
        assert livro.cash_available() == Decimal("1000")
        assert livro.reserved_cash() == Decimal("0")


class TestInvariantesAposTerminal:
    @pytest.mark.parametrize("caminho", ["rejeicao", "cancelamento",
                                         "cancel_apos_parcial"])
    def test_both_sides_agree_and_stay_non_negative(self, caminho):
        if caminho == "rejeicao":
            m, risco, livro, coord = sistema(
                buffer="0.01", broker=Corretora(erro=RuntimeError("x")))
            enviar(coord, reservar(risco))
        else:
            m, risco, livro, coord = sistema(buffer="0.01")
            res = reservar(risco)
            rec = enviar(coord, res)
            if caminho == "cancel_apos_parcial":
                coord.aplicar_fill(rec, side="buy",
                                   quantidade=res.quantity / 2,
                                   preco=Decimal("100"), taxa=Decimal("0"),
                                   fill_id="f1")
            rec.state = OrderState.CANCEL_PENDING
            coord.confirm_cancel(rec.order_id)
        assert livro.reserved_cash() == risco.reserved_total
        assert livro.reserved_cash() >= 0
        ok, motivo = risco.capital_state_consistent()
        assert ok is True, motivo


class TestLedgerRecusaLiberacaoImpossivel:
    def test_releasing_more_than_reserved_is_refused(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="r")
        with pytest.raises(ValueError, match="reservado"):
            livro.release(Decimal("150"), ref="r")
        assert livro.reserved_cash() == Decimal("100")

    def test_releasing_a_non_positive_amount_is_refused(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="r")
        with pytest.raises(ValueError):
            livro.release(Decimal("0"), ref="r")
