"""Uma referência econômica só, e folga financiada de verdade.

Dois defeitos estruturais: depois do restore o `RiskEngine` continuava
olhando o Ledger ANTIGO — duas verdades sobre o mesmo dinheiro — e a folga
de slippage autorizava gasto que nunca foi reservado no caixa.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.intents import ExecutionIntent, Kind, Side
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine, RiskVeto


class Corretora:
    mode = "testnet"

    def __init__(self):
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append((coid, quantity))
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="1000", buffer="0.01"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER,
                    instruments=("BTC/USDT", "ETH/USDT", "SOL/USDT",
                                 "ADA/USDT", "XRP/USDT"),
                    capital=Decimal(capital),
                    slippage_buffer_pct=Decimal(buffer))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def reservar(risco, exposicao="0.25", instrumento="BTC/USDT"):
    p = TradeProposal(agent_id="A", strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal(exposicao),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal("100"), agent_id="A")


def enviar(coord, res, coid="coid-A"):
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                         instrument=res.instrument, quantity=res.quantity,
                         agent_id="A", account="conta-1", client_order_id=coid,
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    return coord.submit_intent(ei)


class TestUmaReferenciaEconomica:
    def test_ledger_and_risk_share_the_same_object_at_boot(self):
        m, risco, livro, coord = sistema()
        assert coord.ledger is risco.carteira

    def test_the_link_survives_a_restore(self):
        """O restore reconstrói o Ledger: sem religar, o risco segue olhando
        o livro ANTIGO — duas verdades sobre o mesmo dinheiro."""
        m, risco, livro, coord = sistema()
        res = reservar(risco, exposicao="0.8")
        rec = enviar(coord, res)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert coord2.ledger is risco2.carteira, (
            "após o restore o risco ficou preso ao livro antigo")

    def test_capital_after_restart_reflects_the_restored_ledger(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco, exposicao="0.8")
        rec = enviar(coord, res)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        investido = livro.invested_cost()
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert risco2.invested_cost() == investido
        assert risco2.available <= m2.capital - investido, (
            "capital ressuscitou porque o risco lia outro livro")

    def test_capital_exhausted_before_the_restart_stays_exhausted(self):
        """O mandato limita 25% por posição, então o capital é esgotado com
        quatro compras — e depois do reinício continua esgotado."""
        m, risco, livro, coord = sistema(buffer="0")
        for i, inst in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT",
                                  "ADA/USDT")):
            res = reservar(risco, instrumento=inst)
            rec = enviar(coord, res, coid=f"coid-{i}")
            coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                               preco=Decimal("100"), taxa=Decimal("0"),
                               fill_id=f"f{i}")
        assert livro.invested_cost() == Decimal("1000")
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema(buffer="0")
        coord2.restaurar_estado(doc)
        assert risco2.invested_cost() == Decimal("1000")
        with pytest.raises(RiskVeto, match="insuficiente|posições"):
            reservar(risco2, instrumento="XRP/USDT")


class TestFolgaFinanciada:
    """A folga autorizava gasto além do reservado sem que esse dinheiro
    estivesse separado: o caixa cobria a diferença sem ninguém decidir."""

    def test_the_reservation_locks_target_plus_buffer(self):
        m, risco, livro, coord = sistema(capital="1000", buffer="0.01")
        res = reservar(risco)
        alvo = res.target_amount
        assert res.buffer_amount == (alvo * Decimal("0.01")).quantize(
            Decimal("0.00000001"))
        assert res.hard_reserved_amount == alvo + res.buffer_amount

    def test_the_ledger_locks_the_hard_coverage(self):
        m, risco, livro, coord = sistema(capital="1000", buffer="0.01")
        res = reservar(risco)
        enviar(coord, res)
        assert livro.reserved_cash() == res.hard_reserved_amount, (
            "o livro travou apenas o alvo: a folga ficou a descoberto")
        assert livro.cash_available() == m.capital - res.hard_reserved_amount

    def test_quantity_is_sized_by_the_target_not_the_buffer(self):
        m, risco, livro, coord = sistema(capital="1000", buffer="0.01")
        res = reservar(risco)
        assert res.quantity == res.target_amount / Decimal("100"), (
            "a folga aumentou a quantidade negociada")

    def test_cost_inside_the_buffer_leaves_reserved_non_negative(self):
        m, risco, livro, coord = sistema(capital="1000", buffer="0.01")
        res = reservar(risco)
        rec = enviar(coord, res)
        custo = res.target_amount + res.buffer_amount / 2
        preco = (custo / res.quantity)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=preco, taxa=Decimal("0"), fill_id="f1")
        assert risco.deficit_total() == Decimal("0")
        assert livro.reserved_cash() >= 0, "RESERVED ficou negativo"

    def test_cost_beyond_the_hard_coverage_is_a_deficit(self):
        m, risco, livro, coord = sistema(capital="1000", buffer="0.01")
        res = reservar(risco)
        rec = enviar(coord, res)
        custo = res.hard_reserved_amount + Decimal("10")
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=custo / res.quantity, taxa=Decimal("0"),
                           fill_id="f1")
        assert risco.deficit_total() > 0
        assert coord.reconciliacao_pendente is True

    def test_insufficient_cash_for_the_buffer_refuses_the_reservation(self):
        """Não se reserva o alvo torcendo para a folga aparecer depois."""
        m, risco, livro, coord = sistema(capital="1000", buffer="0.01")
        primeira = reservar(risco, exposicao="0.9")
        enviar(coord, primeira)
        # sobra pouco: o alvo caberia, a cobertura dura não
        livre = livro.cash_available()
        assert livre > 0
        with pytest.raises(RiskVeto):
            risco.reserve_exato(instrument="ETH/USDT", alvo=livre,
                                agent_id="A")


class TestConsistenciaEntreLivroERisco:
    def test_reserved_matches_on_both_sides(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        enviar(coord, res)
        assert livro.reserved_cash() == risco.reserved_total
        ok, motivo = risco.capital_state_consistent()
        assert ok is True, motivo

    def test_a_mismatch_is_reported(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        enviar(coord, res)
        # o desvio precisa ser na conta DA reserva: não existe mais bolo comum
        livro.release(Decimal("10"), ref="desvio-manual",
                      reservation_id=res.reservation_id)
        ok, motivo = risco.capital_state_consistent()
        assert ok is False and "reserv" in motivo.lower()


class TestDeficitSobreviveAoRestart:
    def test_the_deficit_is_persisted(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = enviar(coord, res)
        custo = res.hard_reserved_amount + Decimal("20")
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=custo / res.quantity, taxa=Decimal("0"),
                           fill_id="f1")
        assert risco.deficit_total() > 0
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert risco2.deficit_total() > 0, (
            "o incidente sumiu no reinício: bastava reiniciar para 'resolver'")
        ok, _ = risco2.capital_state_consistent()
        assert ok is False


class TestCoberturaDuraNoCaminhoNormal:
    """O `reserve()` normal também precisa exigir a cobertura dura — não
    basta o alvo caber. Reservar 250 quando só a folga não cabe deixaria
    2,50 a descoberto desde o primeiro instante."""

    def test_the_target_fitting_is_not_enough(self):
        m, risco, livro, coord = sistema(capital="1000", buffer="0.01")
        # consome 3 × 252,50 = 757,50; sobram 242,50
        for i, inst in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT")):
            res = reservar(risco, instrumento=inst)
            rec = enviar(coord, res, coid=f"c{i}")
            coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                               preco=Decimal("100"), taxa=Decimal("0"),
                               fill_id=f"f{i}")
        # a folga não usada voltou ao caixa no terminal: sobram exatos 250
        livre = risco.available
        assert livre == Decimal("250.00000000"), livre
        # o alvo de 250 CABE; a cobertura dura de 252,50 não
        with pytest.raises(RiskVeto, match="insuficiente"):
            reservar(risco, instrumento="ADA/USDT")

    def test_the_error_names_target_and_buffer(self):
        m, risco, livro, coord = sistema(capital="1000", buffer="0.01")
        for i, inst in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT")):
            res = reservar(risco, instrumento=inst)
            rec = enviar(coord, res, coid=f"c{i}")
            coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                               preco=Decimal("100"), taxa=Decimal("0"),
                               fill_id=f"f{i}")
        try:
            reservar(risco, instrumento="ADA/USDT")
        except RiskVeto as exc:
            assert "folga" in str(exc) and "alvo" in str(exc), (
                f"a mensagem não explica a cobertura: {exc}")
        else:
            pytest.fail("deveria ter recusado")

    def test_exactly_enough_for_the_hard_coverage_is_accepted(self):
        m, risco, livro, coord = sistema(capital="1010", buffer="0.01")
        res = reservar(risco)
        assert res.hard_reserved_amount == res.target_amount + res.buffer_amount
