"""Execução mais cara que a reserva.

A cobertura era consumida proporcionalmente à QUANTIDADE, mas o custo real é
quantidade × preço executado + taxa. Com slippage, a compra custava mais que
o reservado e o sistema seguia como se nada fosse: o caixa ficava negativo e
o livro continuava perfeitamente balanceado, porque partidas dobradas
provam aritmética, não política de risco.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, ExecutionRefused
from tradingagents.desk.intents import ExecutionIntent, Kind, Side
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


class Corretora:
    mode = "testnet"

    def __init__(self):
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append((coid, quantity))
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="1000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def ordem(coord, risco, coid="coid-A", exposicao="0.25"):
    p = TradeProposal(agent_id="A", strategy="s", instrument="BTC/USDT",
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal(exposicao),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    res = risco.reserve(p, Decimal("100"), agent_id="A")
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY, instrument="BTC/USDT",
                         quantity=res.quantity, agent_id="A",
                         account="conta-1", client_order_id=coid,
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    return coord.submit_intent(ei), res


class TestExecucaoMaisCaraQueAReserva:
    def test_overcost_is_registered_as_an_incident(self):
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        # reservado a 100/unidade; executa a 105
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("105"), taxa=Decimal("1"),
                           fill_id="f1")
        assert risco.deficit_total() > 0, (
            "custo acima da reserva não virou incidente")
        assert coord.reconciliacao_pendente is True, (
            "déficit de cobertura não travou risco novo")

    def test_the_real_fill_is_not_undone(self):
        """A corretora já executou: a realidade externa é adotada."""
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("105"), taxa=Decimal("1"),
                           fill_id="f1")
        assert livro.position_quantity("BTC/USDT") == res.quantity
        assert livro.is_balanced(), "o livro precisa refletir a verdade"

    def test_a_balanced_book_does_not_mean_consistent_risk(self):
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("105"), taxa=Decimal("1"),
                           fill_id="f1")
        assert livro.is_balanced() is True
        ok, motivo = risco.capital_state_consistent()
        assert ok is False and "déficit" in motivo.lower()

    def test_new_entry_is_blocked_after_a_deficit(self):
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("105"), taxa=Decimal("1"),
                           fill_id="f1")
        # a recusa precisa vir do PORTÃO, não do veto de instrumento repetido
        from tradingagents.desk.intents import ExecutionIntent as _EI
        ei = _EI(kind=Kind.ENTRY, side=Side.BUY, instrument="BTC/USDT",
                 quantity=Decimal("1"), agent_id="A", account="conta-1",
                 client_order_id="coid-B", reservation_id="qualquer",
                 reserved_amount=Decimal("100"))
        with pytest.raises(ExecutionRefused, match="reconciliação pendente"):
            coord.submit_intent(ei)


class TestCustoDentroDaReserva:
    def test_cheaper_execution_returns_the_unused_part(self):
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        # reservado 250 para 2.5 a 100; executa a 96 => custa 240
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("96"), taxa=Decimal("0"),
                           fill_id="f1")
        assert risco.deficit_total() == Decimal("0")
        assert coord.reconciliacao_pendente is False
        sobra = res.original_amount - (res.quantity * Decimal("96"))
        assert livro.cash_available() == m.capital - (
            res.quantity * Decimal("96")), (
            f"a sobra de {sobra} não voltou ao caixa")

    def test_exact_cost_leaves_nothing_over(self):
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        assert risco.deficit_total() == Decimal("0")
        assert livro.cash_available() == m.capital - res.original_amount

    def test_fees_are_part_of_the_cost(self):
        """Taxa alta o bastante causa déficit mesmo com preço no esperado."""
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("5"),
                           fill_id="f1")
        assert risco.deficit_total() > 0, "a taxa sumiu do custo"


class TestUltimoDeltaPassaPeloRisco:
    def test_the_terminal_fill_advances_the_quantity_to_the_end(self):
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        base = res.original_quantity
        coord.aplicar_fill(rec, side="buy", quantidade=base * Decimal("0.2"),
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        viva = risco.reserva(res.reservation_id)
        assert viva.filled_quantity == base * Decimal("0.2")

        coord.aplicar_fill(rec, side="buy", quantidade=base * Decimal("0.8"),
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f2")
        conclusao = risco.conclusao(res.reservation_id)
        assert conclusao is not None, "o consumo terminal não foi registrado"
        assert Decimal(conclusao["filled_quantity"]) == base, (
            "o último delta não passou pelo RiskEngine")
        assert conclusao["estado"] in ("CONSUMED", "RELEASED")

    def test_a_partial_then_expensive_terminal_is_caught(self):
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        base = res.original_quantity
        coord.aplicar_fill(rec, side="buy", quantidade=base * Decimal("0.4"),
                           preco=Decimal("90"), taxa=Decimal("0"),
                           fill_id="f1")
        assert risco.deficit_total() == Decimal("0")
        coord.aplicar_fill(rec, side="buy", quantidade=base * Decimal("0.6"),
                           preco=Decimal("130"), taxa=Decimal("0"),
                           fill_id="f2")
        assert risco.deficit_total() > 0, (
            "o encarecimento do fill terminal passou despercebido")


class TestFolgaDeclarada:
    """A folga existe porque o dimensionamento reserva quantidade × preço,
    mas a execução ainda paga taxa. Sem folga, toda compra travaria a mesa;
    com folga implícita, um slippage grave passaria despercebido."""

    def test_the_buffer_is_declared_in_the_policy(self):
        m, risco, livro, coord = sistema()
        assert hasattr(m, "slippage_buffer_pct")
        assert m.slippage_buffer_pct > 0

    def test_a_fee_inside_the_buffer_does_not_raise_a_deficit(self):
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        taxa = res.original_amount * Decimal("0.002")     # 0,2%
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=taxa, fill_id="f1")
        assert risco.deficit_total() == Decimal("0")
        assert coord.reconciliacao_pendente is False

    def test_a_cost_beyond_the_buffer_raises_a_deficit(self):
        m, risco, livro, coord = sistema()
        rec, res = ordem(coord, risco)
        taxa = res.original_amount * Decimal("0.05")      # 5%
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=taxa, fill_id="f1")
        assert risco.deficit_total() > 0
        assert coord.reconciliacao_pendente is True

    def test_a_zero_buffer_policy_is_strict(self):
        m, risco, livro, coord = sistema()
        object.__setattr__(coord.mandate, "slippage_buffer_pct", Decimal("0"))
        rec, res = ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("0.01"),
                           fill_id="f1")
        assert risco.deficit_total() > 0, (
            "com folga zero, qualquer centavo além do reservado é déficit")
