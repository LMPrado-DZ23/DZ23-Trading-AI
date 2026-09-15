"""Compensação pertence a UM incidente — e o tempo do livro é durável.

A prova aceitava qualquer `capital_adjustment` ainda não usado: um aporte
feito para o incidente A resolvia o B. E o `at` dos lançamentos nem era
exportado, então o histórico ganhava data nova a cada reinício — perdendo
justamente a ordem dos fatos que a auditoria precisa.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode, utcnow
from tradingagents.desk.risk import RiskEngine


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT"})


def sistema(capital="1000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w")
    return m, risco, livro, coord


def incidente(risco, valor="10", reserva="R1"):
    risco.abrir_incidente("RESERVATION_DEFICIT", amount=Decimal(valor),
                          reservation_id=reserva)
    return next(i for i in risco.incidentes_abertos()
                if i["reservation_id"] == reserva)


class TestCompensacaoPertenceAoIncidente:
    def test_a_compensation_carries_its_incident(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco)
        tx = livro.risk_incident_compensation(
            incident_id=inc["incident_id"], amount=Decimal("10"),
            external_reference="TED-1", actor="risk-manager-1")
        assert tx.kind == "risk_incident_compensation"
        assert livro.find_transaction(tx.tx_id)["incident_id"] == inc["incident_id"]

    def test_a_compensation_for_another_incident_is_refused(self):
        """Aporte destinado a A não resolve B."""
        m, risco, livro, coord = sistema()
        a = incidente(risco, "10", "R-A")
        b = incidente(risco, "10", "R-B")
        tx_b = livro.risk_incident_compensation(
            incident_id=b["incident_id"], amount=Decimal("10"),
            external_reference="TED-B", actor="op")
        with pytest.raises(ValueError, match="outro incidente|vinculad"):
            risco.resolve_risk_incident(
                a["incident_id"], actor=Ator(), evidence="TED-B", reason="",
                resolution_refs={"ledger_tx_ids": [tx_b.tx_id]})
        assert len(risco.incidentes_abertos()) == 2

    def test_a_generic_adjustment_no_longer_serves_as_proof(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco)
        tx = livro.capital_adjustment(Decimal("10"), reason="aporte solto",
                                      actor="op", external_reference="x")
        with pytest.raises(ValueError):
            risco.resolve_risk_incident(
                inc["incident_id"], actor=Ator(), evidence="x", reason="",
                resolution_refs={"ledger_tx_ids": [tx.tx_id]})


class TestAporteRetiradaCompensacaoSaoDiferentes:
    def test_a_withdrawal_never_counts_as_coverage(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco)
        saida = livro.capital_withdrawal(Decimal("10"), reason="retirada",
                                         actor="op", external_reference="w")
        with pytest.raises(ValueError):
            risco.resolve_risk_incident(
                inc["incident_id"], actor=Ator(), evidence="w", reason="",
                resolution_refs={"ledger_tx_ids": [saida.tx_id]})

    def test_a_withdrawal_reduces_cash(self):
        m, risco, livro, coord = sistema()
        antes = livro.cash_available()
        livro.capital_withdrawal(Decimal("10"), reason="r", actor="op")
        assert livro.cash_available() == antes - Decimal("10")
        assert livro.is_balanced()

    def test_a_compensation_must_be_positive(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco)
        for valor in (Decimal("0"), Decimal("-5")):
            with pytest.raises(ValueError, match="positiv"):
                livro.risk_incident_compensation(
                    incident_id=inc["incident_id"], amount=valor,
                    external_reference="x", actor="op")


class TestOperacaoAtomica:
    def test_settle_does_everything_as_one_unit(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        caixa = livro.cash_available()
        registro = risco.settle_risk_incident(
            inc["incident_id"], actor=Ator(), amount=Decimal("10"),
            external_reference="TED-99", evidence="aporte confirmado")
        assert registro["status"] == "RESOLVED"
        assert livro.cash_available() == caixa + Decimal("10")
        assert not risco.incidentes_abertos()
        assert registro["resolution_refs"]["ledger_tx_ids"]

    def test_settle_refuses_an_unauthorised_actor(self):
        class Comum:
            actor_id = "risk-manager"
            capabilities = frozenset()

        m, risco, livro, coord = sistema()
        inc = incidente(risco)
        caixa = livro.cash_available()
        with pytest.raises(PermissionError):
            risco.settle_risk_incident(inc["incident_id"], actor=Comum(),
                                       amount=Decimal("10"),
                                       external_reference="x", evidence="y")
        assert livro.cash_available() == caixa, (
            "o dinheiro se moveu mesmo com a autorização recusada")
        assert risco.incidentes_abertos()

    def test_settle_refuses_an_insufficient_amount(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        caixa = livro.cash_available()
        with pytest.raises(ValueError, match="cobre|insuficiente"):
            risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                       amount=Decimal("5"),
                                       external_reference="x", evidence="y")
        assert livro.cash_available() == caixa
        assert risco.incidentes_abertos()


class TestOrdemTemporal:
    def test_a_compensation_older_than_the_incident_is_refused(self):
        """Compensar antes de a violação existir é coincidência, não conserto.

        O incidente é criado com abertura no FUTURO para que a compensação,
        ainda que corretamente vinculada, seja anterior a ele.
        """
        import time
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        tx = livro.risk_incident_compensation(
            incident_id=inc["incident_id"], amount=Decimal("10"),
            external_reference="antiga", actor="op")
        time.sleep(0.01)
        # a violação passa a constar como aberta DEPOIS da compensação
        alvo = next(i for i in risco.incidentes()
                    if i["incident_id"] == inc["incident_id"])
        alvo["opened_at"] = utcnow().isoformat()

        with pytest.raises(ValueError, match="anterior|antes"):
            risco.resolve_risk_incident(
                inc["incident_id"], actor=Ator(), evidence="x", reason="",
                resolution_refs={"ledger_tx_ids": [tx.tx_id]})


class TestTimestampDuravel:
    def test_the_original_time_survives_a_restart(self):
        m, risco, livro, coord = sistema()
        tx = livro.capital_adjustment(Decimal("10"), reason="a", actor="o")
        original = livro.find_transaction(tx.tx_id)["at"]
        assert original, "o lançamento precisa exportar o instante"

        restaurado = Ledger.from_transactions(livro.export_transactions(),
                                              currency="USDT")
        assert restaurado.find_transaction(tx.tx_id)["at"] == original, (
            "o histórico ganhou data nova no reinício")

    def test_the_order_of_events_is_preserved(self):
        m, risco, livro, coord = sistema()
        import time
        a = livro.capital_adjustment(Decimal("1"), reason="1", actor="o")
        time.sleep(0.01)
        b = livro.capital_adjustment(Decimal("1"), reason="2", actor="o")
        restaurado = Ledger.from_transactions(livro.export_transactions(),
                                              currency="USDT")
        ta = restaurado.find_transaction(a.tx_id)["at"]
        tb = restaurado.find_transaction(b.tx_id)["at"]
        assert ta < tb, "a ordem dos fatos se perdeu"


class TestOcorrencias:
    def test_each_excess_is_recorded(self):
        m, risco, livro, coord = sistema()
        risco.abrir_incidente("RESERVATION_DEFICIT", amount=Decimal("10"),
                              reservation_id="R1", order_id="ord-1",
                              evidence="primeiro excesso")
        risco.abrir_incidente("RESERVATION_DEFICIT", amount=Decimal("5"),
                              reservation_id="R1", order_id="ord-2",
                              evidence="segundo excesso")
        inc = risco.incidentes_abertos()[0]
        assert Decimal(inc["amount"]) == Decimal("15"), "o total não somou"
        assert len(inc["occurrences"]) == 2, "a origem de cada excesso sumiu"
        assert {o["amount"] for o in inc["occurrences"]} == {"10", "5"}
        assert all(o["at"] for o in inc["occurrences"])

    def test_the_history_survives_the_resolution(self):
        m, risco, livro, coord = sistema()
        risco.abrir_incidente("RESERVATION_DEFICIT", amount=Decimal("10"),
                              reservation_id="R1", order_id="ord-1")
        inc = risco.incidentes_abertos()[0]
        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED", evidence="ok")
        registro = next(i for i in risco.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert Decimal(registro["amount"]) == Decimal("10")
        assert registro["occurrences"]
        assert registro["resolution_refs"]["ledger_tx_ids"]
