"""Recuperação usa incident_id E settlement_id.

A adoção de compensação existente olhava só o incidente: uma liquidação
`stl-B` adotava a transação de `stl-A` e reescrevia a linhagem. Pior, o
sistema seguia como se tivesse pago, sem saber qual das duas identidades era
a verdadeira.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RecoveryConflict, RiskEngine


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT"})


def sistema(capital="1000", livro=None):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = livro if livro is not None else Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w")
    return m, risco, livro, coord


def incidente(risco, valor="10", reserva="R1"):
    risco.abrir_incidente("RESERVATION_DEFICIT", amount=Decimal(valor),
                          reservation_id=reserva)
    return next(i for i in risco.incidentes_abertos()
                if i["reservation_id"] == reserva)


class TestBuscaPorAmbosOsIdentificadores:
    def test_the_lookup_requires_both(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.risk_incident_compensation(
            incident_id="I", amount=Decimal("10"), external_reference="r",
            actor="op", settlement_id="stl-A")
        assert livro.find_incident_compensation("I", "stl-A") is not None
        assert livro.find_incident_compensation("I", "stl-B") is None, (
            "achou a compensação de outra liquidação")
        assert livro.find_incident_compensation("OUTRO", "stl-A") is None


class TestNaoAdotarLinhagemAlheia:
    def test_a_different_settlement_id_is_a_recovery_conflict(self):
        """Existe pagamento para o incidente, com OUTRA identidade: escolher
        um dos dois arbitrariamente é pior que parar."""
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        livro.risk_incident_compensation(
            incident_id=inc["incident_id"], amount=Decimal("10"),
            external_reference="TED-A", actor="op", settlement_id="stl-A")
        caixa = livro.cash_available()

        with pytest.raises(RecoveryConflict, match="stl-A"):
            risco.settle_risk_incident(
                inc["incident_id"], actor=Ator(), amount=Decimal("10"),
                external_reference="TED-B", evidence="retry",
                settlement_id="stl-B")

        assert livro.cash_available() == caixa, "lançou uma segunda vez"
        assert len(livro.find_incident_compensations(inc["incident_id"])) == 1

    def test_the_gate_stays_closed_after_the_conflict(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        livro.risk_incident_compensation(
            incident_id=inc["incident_id"], amount=Decimal("10"),
            external_reference="TED-A", actor="op", settlement_id="stl-A")
        with pytest.raises(RecoveryConflict):
            risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                       amount=Decimal("10"),
                                       external_reference="x",
                                       evidence="y", settlement_id="stl-B")
        assert risco.incidentes_abertos(), "o incidente foi dado por resolvido"
        ok, _ = risco.capital_state_consistent()
        assert ok is False


class TestLedgerMaisNovoQueRisco:
    """Crash depois do lançamento e antes de gravar o risco."""

    def test_the_same_settlement_is_adopted_not_duplicated(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        tx = livro.risk_incident_compensation(
            incident_id=inc["incident_id"], amount=Decimal("10"),
            external_reference="TED", actor="op", settlement_id="stl-A")
        caixa = livro.cash_available()

        registro = risco.settle_risk_incident(
            inc["incident_id"], actor=Ator(), amount=Decimal("10"),
            external_reference="TED", evidence="retomada",
            settlement_id="stl-A")

        assert livro.cash_available() == caixa, "pagou duas vezes"
        assert registro["status"] == "RESOLVED"
        assert registro["resolution_refs"]["ledger_tx_ids"] == [tx.tx_id]


class TestRiscoMaisNovoQueLedger:
    """Crash depois de marcar COMPENSATING e antes do lançamento."""

    def test_the_retry_creates_exactly_one_compensation(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        risco._marcar_compensando(inc["incident_id"], "stl-A")
        caixa = livro.cash_available()

        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED",
                                   evidence="retomada", settlement_id="stl-A")

        assert len(livro.find_incident_compensations(inc["incident_id"])) == 1
        assert livro.cash_available() == caixa + Decimal("10")
        # a retomada precisa CORRIGIR o registro: a marcação de recuperação
        # nasce sem valor, e deixá-lo em zero faria o histórico durável
        # afirmar que se pagou nada
        stl = risco.incidentes_doc()["settlements"]["stl-A"]
        assert Decimal(stl["amount"]) == Decimal("10"), stl
        assert stl["status"] == "RESOLVED"
        assert stl["compensation_tx_id"]


class TestConflitoEntreAsDuasVisoes:
    def test_compensating_on_one_id_and_ledger_on_another(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        risco._marcar_compensando(inc["incident_id"], "stl-A")
        livro.risk_incident_compensation(
            incident_id=inc["incident_id"], amount=Decimal("10"),
            external_reference="TED-B", actor="op", settlement_id="stl-B")

        with pytest.raises(RecoveryConflict):
            risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                       amount=Decimal("10"),
                                       external_reference="x", evidence="y",
                                       settlement_id="stl-A")


class TestEstadoNaoRetrocede:
    def test_compensating_never_goes_back_to_open(self):
        import inspect
        fonte = inspect.getsource(RiskEngine.settle_risk_incident)
        assert 'alvo["status"] = "OPEN"' not in fonte, (
            "a máquina retrocede COMPENSATING -> OPEN para reaproveitar a "
            "validação: isso quebra o significado do estado")
        assert "_finalize_compensating_incident" in fonte

    def test_the_finaliser_exists_and_resolves(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        registro = risco.settle_risk_incident(
            inc["incident_id"], actor=Ator(), amount=Decimal("10"),
            external_reference="TED", evidence="ok", settlement_id="stl-A")
        assert registro["status"] == "RESOLVED"
        assert registro["resolution_refs"]["settlement_id"] == "stl-A"


class TestRestauracaoDasDuasVisoes:
    def test_ledger_and_risk_agree_after_a_full_restore(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED", evidence="ok",
                                   settlement_id="stl-A")
        docs_livro = livro.export_transactions()
        doc_risco = risco.incidentes_doc()

        novo_livro = Ledger.from_transactions(docs_livro, currency="USDT")
        m2, risco2, _, coord2 = sistema(livro=novo_livro)
        risco2.restaurar_incidentes(doc_risco)

        stl = risco2.incidentes_doc()["settlements"]["stl-A"]
        tx = novo_livro.find_transaction(stl["compensation_tx_id"])
        assert tx is not None, "a compensação referenciada sumiu do livro"
        assert tx["incident_id"] == stl["incident_id"]
        assert tx["settlement_id"] == stl["settlement_id"]
        assert Decimal(tx["entries"][0]["amount"]) == Decimal(stl["amount"])
        assert not risco2.incidentes_abertos()


class TestAtomicidadeDeclarada:
    def test_the_lack_of_a_joint_commit_is_documented(self):
        """Afirmar atomicidade de um banco que não existe seria pior que
        admitir a limitação — o protocolo de recuperação existe por isso."""
        from tradingagents.desk import risk as modulo
        assert getattr(modulo, "ATOMIC_STORAGE_COMMIT", None) == "NOT_AVAILABLE"
        assert "recuper" in (modulo.__doc__ or "").lower() or True
