"""Déficit tem UMA fonte de verdade.

`RESERVATION_DEFICIT` vivia em dois lugares: `_deficits` e `RiskIncident`.
Resolver fechava só o incidente, e `capital_state_consistent` continuava
consultando `_deficits` — um déficit marcado RESOLVED bloqueava a mesa para
sempre, sem caminho de saída.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.intents import ExecutionIntent, Kind, Side
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


class AtorAutorizado:
    """Contexto de autorização real — não uma string."""
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT"})


class AtorComum:
    actor_id = "risk-manager"          # o NOME não concede poder
    capabilities = frozenset()


def sistema(capital="1000", buffer="0"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital),
                    slippage_buffer_pct=Decimal(buffer))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def deficit_de(coord, risco, excesso="10"):
    p = TradeProposal(agent_id="A", strategy="s", instrument="BTC/USDT",
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal("0.10"),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    res = risco.reserve(p, Decimal("100"), agent_id="A")
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY, instrument="BTC/USDT",
                         quantity=res.quantity, agent_id="A",
                         account="conta-1", client_order_id="c1",
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    rec = coord.submit_intent(ei)
    preco = (res.hard_reserved_amount + Decimal(excesso)) / res.quantity
    coord.aplicar_fill(rec, side="buy", quantidade=res.quantity, preco=preco,
                       taxa=Decimal("0"), fill_id="f1")
    return res


def compensar(livro, valor):
    """Registra a compensação econômica de verdade — texto não resolve mais."""
    return livro.capital_adjustment(Decimal(str(valor)),
                                    reason="aporte para cobrir déficit",
                                    actor="risk-manager-1",
                                    external_reference="TED-teste").tx_id


class TestUmaFonteDeVerdade:
    def test_resolving_the_incident_unblocks_the_desk(self):
        """O caminho de saída precisa existir: sem ele, um déficit resolvido
        travava a mesa para sempre."""
        m, risco, livro, coord = sistema()
        deficit_de(coord, risco)
        inc = next(i for i in risco.incidentes_abertos()
                   if i["type"] == "RESERVATION_DEFICIT")
        tx = compensar(livro, "10")

        risco.resolve_risk_incident(
            inc["incident_id"], actor=AtorAutorizado(),
            evidence="aporte de 10 conferido no extrato", reason="compensação registrada",
            resolution_refs={"ledger_tx_ids": [tx]})

        assert not risco.incidentes_abertos()
        assert risco.deficit_total() == Decimal("0"), (
            "o déficit resolvido continuou contando como ativo")
        ok, motivo = risco.capital_state_consistent()
        assert ok is True, motivo

    def test_the_history_keeps_the_number(self):
        m, risco, livro, coord = sistema()
        deficit_de(coord, risco, excesso="10")
        historico_antes = risco.historical_deficit_total()
        assert historico_antes > 0
        inc = next(i for i in risco.incidentes_abertos()
                   if i["type"] == "RESERVATION_DEFICIT")
        tx = compensar(livro, "10")
        risco.resolve_risk_incident(
            inc["incident_id"], actor=AtorAutorizado(),
            evidence="compensado", reason="ok",
            resolution_refs={"ledger_tx_ids": [tx]})
        assert risco.historical_deficit_total() == historico_antes, (
            "o histórico sumiu ao resolver: aconteceu é diferente de continua")
        assert risco.deficit_total() == Decimal("0")

    def test_active_and_historical_are_different_questions(self):
        m, risco, livro, coord = sistema()
        deficit_de(coord, risco)
        assert risco.deficit_total() == risco.historical_deficit_total()
        inc = risco.incidentes_abertos()[0]
        tx = compensar(livro, "10")
        risco.resolve_risk_incident(
            inc["incident_id"], actor=AtorAutorizado(),
            evidence="x", reason="y",
            resolution_refs={"ledger_tx_ids": [tx]})
        assert risco.deficit_total() == Decimal("0")
        assert risco.historical_deficit_total() > 0

    def test_there_is_no_parallel_deficit_store(self):
        import inspect
        fonte = inspect.getsource(RiskEngine.capital_state_consistent)
        assert "_deficits" not in fonte, (
            "a consistência ainda consulta o estado paralelo")


class TestResolucaoExigeEstadoCorrigido:
    def test_a_mismatch_still_open_refuses_resolution(self):
        """Evidência textual é trilha de auditoria, não prova de conserto."""
        m, risco, livro, coord = sistema()
        p = TradeProposal(agent_id="A", strategy="s", instrument="BTC/USDT",
                          action=Action.BUY, reason="t",
                          desired_exposure_pct=Decimal("0.10"),
                          confidence=Decimal("0.8"),
                          valid_until=utcnow() + timedelta(minutes=5))
        res = risco.reserve(p, Decimal("100"), agent_id="A")
        # reserva no risco sem lastro no livro => MISSING_LEDGER_RESERVATION
        risco.abrir_incidente("COVERAGE_LEDGER_MISMATCH", amount=Decimal("5"),
                              reservation_id=res.reservation_id)
        inc = risco.incidentes_abertos()[0]
        with pytest.raises(ValueError, match="estado|invariante|corrig"):
            risco.resolve_risk_incident(inc["incident_id"],
                                        actor=AtorAutorizado(),
                                        evidence="conferido", reason="")

    def test_resolution_works_once_the_state_is_fixed(self):
        m, risco, livro, coord = sistema()
        deficit_de(coord, risco)
        inc = risco.incidentes_abertos()[0]
        tx = compensar(livro, "10")
        risco.resolve_risk_incident(
            inc["incident_id"], actor=AtorAutorizado(),
            evidence="extrato confere", reason="ok",
            resolution_refs={"ledger_tx_ids": [tx]})
        assert not risco.incidentes_abertos()


class TestAutorizacaoNaoEString:
    def test_the_name_risk_manager_grants_nothing(self):
        m, risco, livro, coord = sistema()
        deficit_de(coord, risco)
        inc = risco.incidentes_abertos()[0]
        with pytest.raises(PermissionError, match="RESOLVE_RISK_INCIDENT"):
            risco.resolve_risk_incident(inc["incident_id"], actor=AtorComum(),
                                        evidence="conferido", reason="")

    def test_a_plain_string_actor_is_refused(self):
        m, risco, livro, coord = sistema()
        deficit_de(coord, risco)
        inc = risco.incidentes_abertos()[0]
        with pytest.raises(PermissionError):
            risco.resolve_risk_incident(inc["incident_id"],
                                        actor="risk-manager",
                                        evidence="conferido", reason="")

    def test_the_authorised_actor_is_recorded(self):
        m, risco, livro, coord = sistema()
        deficit_de(coord, risco)
        inc = risco.incidentes_abertos()[0]
        tx = compensar(livro, "10")
        risco.resolve_risk_incident(
            inc["incident_id"], actor=AtorAutorizado(),
            evidence="ok", reason="",
            resolution_refs={"ledger_tx_ids": [tx]})
        registro = next(i for i in risco.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert registro["resolved_by"] == "risk-manager-1"


class TestPersistencia:
    def test_open_stays_open_and_resolved_stays_resolved(self):
        m, risco, livro, coord = sistema()
        deficit_de(coord, risco)
        aberto = risco.incidentes_abertos()[0]
        tx = compensar(livro, "10")
        risco.resolve_risk_incident(aberto["incident_id"],
                                    actor=AtorAutorizado(),
                                    evidence="ok", reason="",
                                    resolution_refs={"ledger_tx_ids": [tx]})
        risco.abrir_incidente("COVERAGE_LEDGER_MISMATCH", amount=Decimal("3"),
                              reservation_id="R-outra")
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        estados = {i["type"]: i["status"] for i in risco2.incidentes()}
        assert estados["RESERVATION_DEFICIT"] == "RESOLVED", (
            "reiniciar reabriu um incidente já resolvido")
        assert estados["COVERAGE_LEDGER_MISMATCH"] == "OPEN"
        assert risco2.deficit_total() == Decimal("0")
        assert risco2.historical_deficit_total() > 0


class TestMigracaoDeDeficitAntigo:
    def test_a_legacy_deficit_becomes_an_open_incident(self):
        m, risco, livro, coord = sistema()
        # snapshot antigo: só `deficits`, sem incidente correspondente
        risco.restaurar_incidentes({"deficits": {"R-legado": "25"},
                                    "conclusoes": {}, "incidentes": {}})
        abertos = risco.incidentes_abertos()
        assert any(i["type"] == "RESERVATION_DEFICIT"
                   and i["reservation_id"] == "R-legado" for i in abertos), (
            "déficit antigo foi ignorado na migração")
        assert risco.deficit_total() == Decimal("25")

    def test_a_legacy_deficit_with_an_incident_is_not_duplicated(self):
        m, risco, livro, coord = sistema()
        risco.abrir_incidente("RESERVATION_DEFICIT", amount=Decimal("25"),
                              reservation_id="R-legado")
        doc = risco.incidentes_doc()
        m2, risco2, livro2, coord2 = sistema()
        risco2.restaurar_incidentes(doc)
        deficits = [i for i in risco2.incidentes()
                    if i["type"] == "RESERVATION_DEFICIT"]
        assert len(deficits) == 1, "a migração duplicou o incidente"
        assert risco2.deficit_total() == Decimal("25")
