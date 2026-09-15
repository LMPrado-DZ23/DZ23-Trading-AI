"""Liquidação de incidente: uma compensação, uma vez só.

`settle_risk_incident` lia o incidente, criava a compensação no livro e só
depois marcava RESOLVED — sem nada serializando a sequência. Duas chamadas
simultâneas criavam DUAS compensações para a mesma dívida, e um crash entre
o lançamento e a resolução fazia o retry criar outra.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
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


def compensacoes(livro, incident_id):
    return livro.find_incident_compensations(incident_id)


class TestConcorrencia:
    def test_four_simultaneous_settlements_move_money_once(self):
        import threading
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        caixa = livro.cash_available()
        erros = []

        def liquidar():
            try:
                risco.settle_risk_incident(
                    inc["incident_id"], actor=Ator(), amount=Decimal("10"),
                    external_reference="TED", evidence="aporte")
            except Exception as exc:
                erros.append(exc)

        fios = [threading.Thread(target=liquidar) for _ in range(4)]
        for f in fios:
            f.start()
        for f in fios:
            f.join()

        assert len(compensacoes(livro, inc["incident_id"])) == 1, (
            "mais de uma compensação para a mesma dívida")
        assert livro.cash_available() == caixa + Decimal("10")
        registro = next(i for i in risco.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert registro["status"] == "RESOLVED"

    def test_a_second_settlement_after_resolution_moves_nothing(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED", evidence="ok")
        caixa = livro.cash_available()
        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED", evidence="ok")
        assert livro.cash_available() == caixa
        assert len(compensacoes(livro, inc["incident_id"])) == 1


class TestIdempotenciaDuravel:
    def test_the_same_settlement_id_never_pays_twice(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        caixa = livro.cash_available()
        for _ in range(3):
            risco.settle_risk_incident(
                inc["incident_id"], actor=Ator(), amount=Decimal("10"),
                external_reference="TED", evidence="ok",
                settlement_id="stl-fixo")
        assert livro.cash_available() == caixa + Decimal("10")
        assert len(compensacoes(livro, inc["incident_id"])) == 1

    def test_a_retry_with_a_different_amount_is_a_conflict(self):
        from tradingagents.desk.risk import IdempotencyConflict
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED", evidence="ok",
                                   settlement_id="stl-1")
        with pytest.raises(IdempotencyConflict):
            risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                       amount=Decimal("20"),
                                       external_reference="TED", evidence="ok",
                                       settlement_id="stl-1")
        assert len(compensacoes(livro, inc["incident_id"])) == 1

    def test_the_settlement_record_is_persisted(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED", evidence="ok",
                                   settlement_id="stl-9")
        doc = risco.incidentes_doc()
        registro = doc["settlements"]["stl-9"]
        assert registro["status"] == "RESOLVED"
        assert registro["incident_id"] == inc["incident_id"]
        assert registro["compensation_tx_id"]
        assert registro["started_at"] and registro["completed_at"]


class TestRecuperacaoDeCrash:
    def test_a_compensation_written_before_the_crash_is_adopted(self):
        """Crash depois do lançamento, antes de resolver: o retry precisa
        ENCONTRAR a compensação existente, não criar outra."""
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        # simula o estado pós-crash: dinheiro já lançado, incidente aberto
        tx = livro.risk_incident_compensation(
            incident_id=inc["incident_id"], amount=Decimal("10"),
            external_reference="TED", actor="risk-manager-1",
            settlement_id="stl-crash")
        caixa_pos_crash = livro.cash_available()

        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED", evidence="retry",
                                   settlement_id="stl-crash")

        assert livro.cash_available() == caixa_pos_crash, (
            "o retry lançou uma segunda compensação")
        assert len(compensacoes(livro, inc["incident_id"])) == 1
        registro = next(i for i in risco.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert registro["status"] == "RESOLVED"
        assert registro["resolution_refs"]["ledger_tx_ids"] == [tx.tx_id]

    def test_compensating_without_a_ledger_entry_can_be_retried_once(self):
        """Crash depois de marcar COMPENSATING e antes do livro."""
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        risco._marcar_compensando(inc["incident_id"], "stl-b")
        registro = next(i for i in risco.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert registro["status"] == "COMPENSATING"
        assert not compensacoes(livro, inc["incident_id"])

        caixa = livro.cash_available()
        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED", evidence="retomada",
                                   settlement_id="stl-b")
        assert livro.cash_available() == caixa + Decimal("10")
        assert len(compensacoes(livro, inc["incident_id"])) == 1

    def test_the_state_survives_a_restart(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                   amount=Decimal("10"),
                                   external_reference="TED", evidence="ok",
                                   settlement_id="stl-r")
        doc = risco.incidentes_doc()

        m2, risco2, livro2, coord2 = sistema()
        risco2.restaurar_incidentes(doc)
        assert risco2.incidentes_doc()["settlements"]["stl-r"]["status"] == "RESOLVED"
        assert not risco2.incidentes_abertos()


class TestValorExato:
    def test_the_settlement_compensates_exactly_what_is_due(self):
        """Aporte a mais é outra coisa: CAPITAL_CONTRIBUTION separado."""
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        with pytest.raises(ValueError, match="exat"):
            risco.settle_risk_incident(inc["incident_id"], actor=Ator(),
                                       amount=Decimal("15"),
                                       external_reference="TED", evidence="ok")
        assert not compensacoes(livro, inc["incident_id"])


class TestComparacaoTemporalReal:
    def test_the_same_instant_in_different_offsets_is_equal(self):
        from tradingagents.desk.risk import _instante_de
        a = _instante_de("2026-09-14T20:00:00-03:00")
        b = _instante_de("2026-09-14T23:00:00+00:00")
        assert a == b, "comparação por string diria que são diferentes"

    def test_string_compare_would_have_failed_here(self):
        """Prova que a comparação textual daria a resposta ERRADA."""
        from tradingagents.desk.risk import _instante_de
        antes_no_texto = "2026-09-14T20:00:00-03:00"
        depois_no_texto = "2026-09-14T23:00:00+00:00"
        assert antes_no_texto < depois_no_texto      # ordem textual
        assert _instante_de(antes_no_texto) == _instante_de(depois_no_texto), (
            "são o mesmo instante: só a string sugere ordem")

    def test_an_invalid_timestamp_fails_closed(self):
        from tradingagents.desk.risk import _instante_de
        with pytest.raises(ValueError):
            _instante_de("ontem de manhã")


class TestSerializacaoDeVerdade:
    """O registro de liquidação sozinho não basta: sem lock, duas threads
    passam pela checagem ao mesmo tempo e ambas lançam no livro."""

    def test_threads_interleaved_at_the_worst_moment(self):
        import threading
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        caixa = livro.cash_available()
        partida = threading.Barrier(4)
        erros = []

        def liquidar():
            partida.wait()               # todas entram juntas
            try:
                risco.settle_risk_incident(
                    inc["incident_id"], actor=Ator(), amount=Decimal("10"),
                    external_reference="TED", evidence="aporte")
            except Exception as exc:
                erros.append(exc)

        fios = [threading.Thread(target=liquidar) for _ in range(4)]
        for f in fios:
            f.start()
        for f in fios:
            f.join()

        assert len(compensacoes(livro, inc["incident_id"])) == 1, (
            f"{len(compensacoes(livro, inc['incident_id']))} compensações "
            "para uma dívida só")
        assert livro.cash_available() == caixa + Decimal("10"), (
            f"caixa moveu {livro.cash_available() - caixa} em vez de 10")

    def test_the_lifecycle_is_serialised_by_a_lock(self):
        import inspect
        fonte = inspect.getsource(RiskEngine.settle_risk_incident)
        assert "self._lock_settlement" in fonte, (
            "a transição OPEN->COMPENSATING->RESOLVED não está serializada")


class TestOrdemTemporalNoCaminhoReal:
    """A comparação por string diria que 20:00-03:00 vem antes de
    23:00+00:00, quando são o MESMO instante."""

    def test_a_compensation_at_the_same_instant_is_accepted(self):
        m, risco, livro, coord = sistema()
        inc = incidente(risco, "10")
        tx = livro.risk_incident_compensation(
            incident_id=inc["incident_id"], amount=Decimal("10"),
            external_reference="TED", actor="op")
        alvo = next(i for i in risco.incidentes()
                    if i["incident_id"] == inc["incident_id"])
        # mesmo instante, escrito com fusos diferentes
        registro = livro.find_transaction(tx.tx_id)
        instante = registro["at"]
        from datetime import timedelta as _td, timezone

        from tradingagents.desk.risk import _instante_de
        outro_fuso = _instante_de(instante).astimezone(
            timezone(_td(hours=-3))).isoformat()
        alvo["opened_at"] = outro_fuso

        risco.resolve_risk_incident(
            inc["incident_id"], actor=Ator(), evidence="x", reason="",
            resolution_refs={"ledger_tx_ids": [tx.tx_id]})
        assert not risco.incidentes_abertos(), (
            "a comparação textual recusou uma compensação do mesmo instante")


class TestDirecaoQueEnganaAString:
    """O caso em que a comparação textual erra de verdade.

    tx em "20:00-03:00" é 23:00Z; incidente em "22:00+00:00" é 22:00Z. A
    compensação veio DEPOIS. Mas como texto, "20:00-03:00" < "22:00+00:00",
    e a validação recusaria uma prova legítima.
    """

    def test_a_valid_compensation_is_not_refused_by_text_order(self):
        m, risco, livro, coord = sistema()
        incidente_falso = {
            "incident_id": "inc_x", "type": "RESERVATION_DEFICIT",
            "amount": "10", "opened_at": "2026-09-14T22:00:00+00:00",
        }

        class LivroFalso:
            @staticmethod
            def find_transaction(tx_id):
                return {"tx_id": tx_id, "kind": "risk_incident_compensation",
                        "incident_id": "inc_x",
                        "at": "2026-09-14T20:00:00-03:00",   # = 23:00Z
                        "entries": [{"account": "CASH:USDT", "amount": "10"}]}

        risco.carteira = LivroFalso()
        # não pode levantar: a compensação é POSTERIOR ao incidente
        risco.validate_incident_resolution(incidente_falso,
                                           {"ledger_tx_ids": ["tx-1"]})
