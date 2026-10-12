"""Modelo de capital: dinheiro investido não ressuscita.

`available = capital - reservas` ignora o capital que já virou POSIÇÃO.
Depois do fill terminal a reserva some, e os mesmos 250 voltam a parecer
livres — com a posição ainda aberta. Isso permite alocar o mesmo dinheiro
indefinidamente.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine, RiskVeto


class Corretora:
    mode = "testnet"

    def __init__(self, preco=Decimal("100")):
        self.preco = preco
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append((coid, side, quantity))
        return {"id": f"ex-{coid}", "status": "filled", "filled": quantity,
                "average": self.preco, "fee": Decimal("0"),
                "trades": [{"id": f"t-{coid}", "amount": quantity,
                            "price": self.preco, "fee": {"cost": Decimal("0")}}]}


def sistema(capital="1000", instrumentos=("BTC/USDT", "ETH/USDT", "SOL/USDT",
                                          "ADA/USDT", "XRP/USDT"),
            max_posicoes=10):
    # folga ZERO: estes cenários medem capital exato, e a folga de execução
    # é política declarada — com ela, 4 × 250 não caberia em 1000
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=instrumentos,
                    capital=Decimal(capital), max_open_positions=max_posicoes,
                    slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def comprar(coord, risco, instrumento="BTC/USDT", agente="A", preco="100",
            exposicao="0.25"):
    from tradingagents.desk.intents import ExecutionIntent, Kind, Side
    p = TradeProposal(agent_id=agente, strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal(exposicao),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    res = risco.reserve(p, Decimal(preco), agent_id=agente)
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY, instrument=instrumento,
                         quantity=res.quantity, agent_id=agente,
                         account="conta-1", reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    return coord.submit_intent(ei), res


class TestCapitalInvestidoNaoRessuscita:
    def test_a_filled_buy_leaves_the_money_invested_not_available(self):
        m, risco, livro, coord = sistema()
        rec, res = comprar(coord, risco)
        assert rec.filled_qty > 0, "o cenário precisa de um fill terminal"
        assert risco.reserved_total == Decimal("0"), "a reserva foi consumida"
        assert livro.invested_cost() == res.amount, (
            "o custo da posição aberta não aparece como investido")
        assert risco.available <= m.capital - res.amount, (
            f"capital ressuscitou: available={risco.available} com "
            f"{res.amount} ainda em posição aberta")

    def test_cash_available_dropped_by_the_purchase(self):
        m, risco, livro, coord = sistema()
        _, res = comprar(coord, risco)
        assert livro.cash_available() == m.capital - res.amount

    def test_the_capital_identity_holds(self):
        """caixa + reservado + investido = patrimônio (sem resultado ainda)."""
        m, risco, livro, coord = sistema()
        comprar(coord, risco)
        soma = (livro.cash_available() + livro.reserved_cash()
                + livro.invested_cost())
        assert soma == m.capital, f"identidade quebrada: {soma} != {m.capital}"


class TestNaoReciclarOMesmoDinheiro:
    def test_the_fifth_buy_is_refused(self):
        m, risco, livro, coord = sistema(capital="1000")
        instrumentos = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT"]
        for inst in instrumentos:
            comprar(coord, risco, instrumento=inst)
        assert livro.invested_cost() == Decimal("1000")
        assert livro.cash_available() == Decimal("0")
        with pytest.raises((RiskVeto, Exception)):
            comprar(coord, risco, instrumento="XRP/USDT")
        assert len(coord.broker.enviadas) == 4, (
            "uma quinta ordem foi enviada com o capital já todo investido")


class TestVendaDevolveCapital:
    def test_selling_returns_proceeds_to_cash(self):
        m, risco, livro, coord = sistema()
        _, res = comprar(coord, risco)
        investido = livro.invested_cost()
        coord.broker.preco = Decimal("120")
        coord.close_position(instrument="BTC/USDT",
                             quantity=livro.position_quantity("BTC/USDT"),
                             price=Decimal("120"), cost_basis=investido,
                             agent_id="A")
        assert livro.invested_cost() == Decimal("0")
        assert livro.cash_available() > m.capital, (
            "a venda com lucro não aumentou o caixa disponível")
        assert risco.available > m.capital - res.amount


class TestPosicaoAbertaContaComoExposicao:
    def test_open_positions_count_towards_the_position_limit(self):
        m, risco, livro, coord = sistema(capital="10000", max_posicoes=2)
        comprar(coord, risco, instrumento="BTC/USDT", exposicao="0.05")
        comprar(coord, risco, instrumento="ETH/USDT", exposicao="0.05")
        assert risco.reserved_total == Decimal("0"), "as reservas já sumiram"
        with pytest.raises(RiskVeto, match="posições"):
            comprar(coord, risco, instrumento="SOL/USDT", exposicao="0.05")

    def test_open_instruments_include_filled_positions(self):
        m, risco, livro, coord = sistema(capital="10000")
        comprar(coord, risco, instrumento="BTC/USDT", exposicao="0.05")
        assert "BTC/USDT" in risco.open_instruments(), (
            "instrumento sumiu da lista aberta quando a reserva foi consumida")

    def test_a_second_entry_in_the_same_instrument_is_refused(self):
        m, risco, livro, coord = sistema(capital="10000")
        comprar(coord, risco, instrumento="BTC/USDT", exposicao="0.05")
        with pytest.raises(RiskVeto, match="BTC/USDT"):
            comprar(coord, risco, instrumento="BTC/USDT", exposicao="0.05")


class TestReservaGuardaOriginal:
    """`amount` mudava de significado após o consumo: antes era o valor
    original, depois virava o saldo. Com isso o consumo proporcional passava
    a ser calculado sobre uma base que já encolheu."""

    def _reserva(self, risco, exposicao="0.10"):
        p = TradeProposal(agent_id="A", strategy="s", instrument="BTC/USDT",
                          action=Action.BUY, reason="t",
                          desired_exposure_pct=Decimal(exposicao),
                          confidence=Decimal("0.8"),
                          valid_until=utcnow() + timedelta(minutes=5))
        return risco.reserve(p, Decimal("100"), agent_id="A")

    def test_original_is_preserved_after_consumption(self):
        m, risco, livro, coord = sistema(capital="1000")
        res = self._reserva(risco)
        original = res.original_amount
        risco.consume(res.reservation_id, original / 2)
        atual = risco.reserva(res.reservation_id)
        assert atual.original_amount == original, "o valor original foi perdido"
        assert atual.consumed_amount == original / 2
        assert atual.remaining_amount == original / 2

    def test_the_reservation_invariant_holds(self):
        m, risco, livro, coord = sistema(capital="1000")
        res = self._reserva(risco)
        for fatia in ("0.2", "0.3"):
            risco.consume(res.reservation_id,
                          res.original_amount * Decimal(fatia))
            atual = risco.reserva(res.reservation_id)
            # A invariante é sobre a COBERTURA DURA (alvo + folga), que é o
            # dinheiro efetivamente travado.
            assert (atual.consumed_amount + atual.remaining_amount
                    == atual.hard_reserved_amount), (
                "cobertura dura != consumido + restante")

    def test_fifty_then_twentyfive_percent(self):
        m, risco, livro, coord = sistema(capital="1000")
        res = self._reserva(risco)
        base = res.original_amount
        risco.consume(res.reservation_id, base * Decimal("0.5"))
        assert risco.reserva(res.reservation_id).remaining_amount == base / 2
        risco.consume(res.reservation_id, base * Decimal("0.25"))
        atual = risco.reserva(res.reservation_id)
        assert atual.consumed_amount == base * Decimal("0.75")
        assert atual.remaining_amount == base * Decimal("0.25"), (
            "o segundo consumo usou o saldo como base em vez do original")

    def test_state_survives_a_restart(self):
        m, risco, livro, coord = sistema(capital="1000")
        res = self._reserva(risco)
        base = res.original_amount
        risco.consume(res.reservation_id, base * Decimal("0.2"))
        risco.consume(res.reservation_id, base * Decimal("0.3"))
        docs = risco.active_reservations()

        m2, risco2, livro2, coord2 = sistema(capital="1000")
        risco2.restore_reservations(docs)
        atual = risco2.reserva(res.reservation_id)
        assert atual.original_amount == base
        assert atual.consumed_amount == base * Decimal("0.5")
        assert atual.remaining_amount == base * Decimal("0.5")
