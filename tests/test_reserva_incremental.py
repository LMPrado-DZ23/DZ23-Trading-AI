"""Consumo incremental de reserva: livro e risco contam o mesmo dinheiro.

O livro consumia a reserva proporcionalmente a cada fill; o RiskEngine
mantinha o valor INTEGRAL até o fill terminal. Entre o primeiro parcial e o
último, `reserved_total` e `available` mentiam — e é `available` que decide
se a próxima ordem cabe.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, OrderState
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


class Corretora:
    mode = "testnet"

    def __init__(self):
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append(coid)
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital))
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def reservar(risco, instrumento="BTC/USDT", agente="A", preco="100",
             exposicao="0.15"):
    from datetime import timedelta
    p = TradeProposal(agent_id=agente, strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal(exposicao),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal(preco), agent_id=agente)


def ordem_de(coord, risco, res):
    from tradingagents.desk.intents import ExecutionIntent, Kind, Side
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                         instrument=res.instrument, quantity=res.quantity,
                         agent_id=res.agent_id, account="conta-1",
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    return coord.submit_intent(ei)


class TestConsumoProporcionalNoRisco:
    def test_partial_fill_reduces_the_reserved_total(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)                 # 1500 reservados, 15 unidades
        rec = ordem_de(coord, risco, res)
        antes = risco.reserved_total
        assert antes == res.amount

        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity / 2,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        assert risco.reserved_total < antes, (
            "o risco manteve a reserva integral depois de metade executada")
        assert risco.reserved_total == antes / 2

    def test_available_reflects_the_consumption(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = ordem_de(coord, risco, res)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity / 2,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        assert risco.available == m.capital - risco.reserved_total

    def test_the_invariant_holds_after_each_fill(self):
        """reserved_total == soma das reservas vivas, sempre."""
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = ordem_de(coord, risco, res)
        for i, fatia in enumerate(("0.2", "0.3", "0.5")):
            coord.aplicar_fill(rec, side="buy",
                               quantidade=res.quantity * Decimal(fatia),
                               preco=Decimal("100"), taxa=Decimal("0"),
                               fill_id=f"f{i}")
            soma = sum((Decimal(str(r["amount"]))
                        for r in risco.active_reservations()), Decimal("0"))
            assert risco.reserved_total == soma, (
                f"invariante quebrada após o fill {i}")

    def test_terminal_fill_releases_what_remains(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = ordem_de(coord, risco, res)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        assert rec.state is OrderState.FILLED
        assert risco.reserved_total == Decimal("0")
        assert not risco.active_reservations()

    def test_capital_is_never_freed_twice(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = ordem_de(coord, risco, res)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity / 2,
                           preco=Decimal("100"), taxa=Decimal("0"), fill_id="f1")
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity / 2,
                           preco=Decimal("100"), taxa=Decimal("0"), fill_id="f2")
        assert risco.available == m.capital, (
            "capital final diferente do inicial: houve dupla liberação ou "
            "sobra presa")


class TestConsumoNaoAbreBrechaDeRisco:
    def test_consumption_does_not_let_a_second_order_slip_in(self):
        """O capital liberado por um parcial é REAL (virou posição), então
        pode ser reusado — mas apenas o que já foi consumido."""
        m, risco, livro, coord = sistema(capital="1000")
        # o mandato limita a exposição por ordem, então o valor reservado é
        # derivado do que o RISCO decidiu — não de uma premissa minha
        res = reservar(risco, exposicao="0.9")
        rec = ordem_de(coord, risco, res)
        reservado = res.amount
        assert risco.available == m.capital - reservado

        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity / 3,
                           preco=Decimal("100"), taxa=Decimal("0"), fill_id="f1")
        # o produto quantiza o consumo em 8 casas; comparar na mesma
        # precisão em vez de exigir aritmética exata de terço
        consumido = (reservado / 3).quantize(Decimal("0.00000001"))
        assert risco.reserved_total == reservado - consumido
        assert risco.available == m.capital - (reservado - consumido), (
            "o capital que virou posição precisa voltar a ficar disponível — "
            "nem mais, nem menos")
        # e a invariante continua valendo
        soma = sum((Decimal(str(r["amount"]))
                    for r in risco.active_reservations()), Decimal("0"))
        assert risco.reserved_total == soma

    def test_a_reservation_cannot_be_consumed_beyond_its_amount(self):
        from tradingagents.desk.risk import RiskVeto
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        with pytest.raises(RiskVeto, match="excede"):
            risco.consume(res.reservation_id, res.amount * 2)

    def test_consuming_an_unknown_reservation_is_refused(self):
        from tradingagents.desk.risk import RiskVeto
        m, risco, livro, coord = sistema()
        with pytest.raises(RiskVeto, match="reserva"):
            risco.consume("res-inexistente", Decimal("10"))


class TestSnapshotRefleteOConsumo:
    def test_restored_state_keeps_the_partial_consumption(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = ordem_de(coord, risco, res)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity / 2,
                           preco=Decimal("100"), taxa=Decimal("0"), fill_id="f1")
        parcial = risco.reserved_total
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert risco2.reserved_total == parcial, (
            "o consumo parcial não sobreviveu ao reinício")
        assert risco2.available == m2.capital - parcial
