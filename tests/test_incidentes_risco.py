"""Incidente financeiro é entidade própria, não nota de rodapé.

A divergência de cobertura era gravada só na CONCLUSÃO da reserva, e
`capital_state_consistent()` não olhava conclusões. Depois da
terminalização, uma violação financeira real sumia da decisão de risco — e
o sistema voltava a operar como se nada tivesse acontecido.
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
        self.enviadas.append(coid)
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="1000", buffer="0"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER,
                    instruments=("BTC/USDT", "ETH/USDT", "SOL/USDT"),
                    capital=Decimal(capital),
                    slippage_buffer_pct=Decimal(buffer))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def reservar(risco, instrumento="BTC/USDT", pct="0.10"):
    p = TradeProposal(agent_id="A", strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal(pct),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal("100"), agent_id="A")


def enviar(coord, res, coid="c1"):
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                         instrument=res.instrument, quantity=res.quantity,
                         agent_id="A", account="conta-1", client_order_id=coid,
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    return coord.submit_intent(ei)


def divergencia(coord, risco, livro):
    """Risco com reserva viva que o livro não travou."""
    res = reservar(risco)
    coord.liberar_reserva_restante(res.reservation_id, motivo="teste")
    return res


class TestDivergenciaAbreIncidente:
    def test_a_coverage_mismatch_opens_an_incident(self):
        m, risco, livro, coord = sistema()
        res = divergencia(coord, risco, livro)
        abertos = risco.incidentes_abertos()
        assert abertos, "a divergência não abriu incidente"
        inc = abertos[0]
        assert inc["type"] == "COVERAGE_LEDGER_MISMATCH"
        assert inc["reservation_id"] == res.reservation_id
        assert inc["status"] == "OPEN"
        assert Decimal(inc["amount"]) > 0

    def test_the_conclusion_is_preserved_alongside(self):
        """Conclusão responde 'como terminou'; incidente responde 'há
        violação em aberto'. São perguntas diferentes."""
        m, risco, livro, coord = sistema()
        res = divergencia(coord, risco, livro)
        assert risco.conclusao(res.reservation_id) is not None
        assert risco.incidentes_abertos()

    def test_an_open_incident_blocks_capital_consistency(self):
        m, risco, livro, coord = sistema()
        divergencia(coord, risco, livro)
        ok, motivo = risco.capital_state_consistent()
        assert ok is False, "violação financeira sumiu da decisão de risco"
        assert "COVERAGE_LEDGER_MISMATCH" in motivo
        assert "inc_" in motivo, f"o motivo não identifica o incidente: {motivo}"

    def test_a_deficit_is_the_same_kind_of_incident(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = enviar(coord, res)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("150"), taxa=Decimal("0"),
                           fill_id="f1")
        tipos = {i["type"] for i in risco.incidentes_abertos()}
        assert "RESERVATION_DEFICIT" in tipos, tipos


class TestIncidenteBloqueiaExecucao:
    def test_a_new_order_is_refused_while_an_incident_is_open(self):
        m, risco, livro, coord = sistema()
        divergencia(coord, risco, livro)
        coord.reconciliacao_concluida("portão liberado à força")
        nova = reservar(risco, "ETH/USDT")
        with pytest.raises(ExecutionRefused, match="incidente|COVERAGE"):
            enviar(coord, nova, "c2")
        assert not coord.broker.enviadas, "ordem enviada com incidente aberto"


class TestIncidenteSobreviveAoRestart:
    def test_restarting_is_not_a_resolution(self):
        m, risco, livro, coord = sistema()
        divergencia(coord, risco, livro)
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert risco2.incidentes_abertos(), (
            "bastava reiniciar para a violação desaparecer")
        ok, _ = risco2.capital_state_consistent()
        assert ok is False


class _AtorAutorizado:
    """Ator com capacidade real: o nome sozinho não concede poder."""
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT"})


class TestResolucaoExigeEvidencia:
    def test_resolving_without_evidence_is_refused(self):
        m, risco, livro, coord = sistema()
        divergencia(coord, risco, livro)
        inc = risco.incidentes_abertos()[0]
        with pytest.raises(ValueError, match="evid"):
            risco.resolve_risk_incident(inc["incident_id"],
                                        actor=_AtorAutorizado(),
                                        evidence="", reason="")

    def test_resolving_with_evidence_closes_it(self):
        m, risco, livro, coord = sistema()
        divergencia(coord, risco, livro)
        inc = risco.incidentes_abertos()[0]
        risco.resolve_risk_incident(
            inc["incident_id"], actor=_AtorAutorizado(),
            evidence="extrato da corretora 2026-09-14 confere",
            reason="ajuste manual conferido")
        assert not risco.incidentes_abertos()
        ok, motivo = risco.capital_state_consistent()
        assert ok is True, motivo

    def test_the_history_is_never_erased(self):
        m, risco, livro, coord = sistema()
        divergencia(coord, risco, livro)
        inc = risco.incidentes_abertos()[0]
        risco.resolve_risk_incident(inc["incident_id"], actor=_AtorAutorizado(),
                                    evidence="conferido", reason="ok")
        todos = risco.incidentes()
        registro = next(i for i in todos
                        if i["incident_id"] == inc["incident_id"])
        assert registro["status"] == "RESOLVED"
        assert registro["resolved_by"] == "risk-manager-1"
        assert registro["resolution_evidence"]
        assert registro["opened_at"], "a abertura precisa continuar visível"

    def test_balances_matching_again_does_not_close_it(self):
        """Bug aconteceu, números coincidiram depois: o sistema não pode
        esquecer sozinho."""
        m, risco, livro, coord = sistema()
        divergencia(coord, risco, livro)
        # nada mais está reservado dos dois lados: os saldos "batem"
        assert risco.reserved_total == Decimal("0")
        assert livro.reserved_cash() == Decimal("0")
        ok, _ = risco.capital_state_consistent()
        assert ok is False, "o incidente fechou sozinho ao saldo coincidir"


class TestConjuntosDeReserva:
    def test_an_orphan_ledger_reservation_is_detected(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        enviar(coord, res)
        livro.reserve(Decimal("50"), ref="fantasma", reservation_id="R-fantasma")
        ok, motivo = risco.capital_state_consistent()
        assert ok is False
        assert "R-fantasma" in motivo and "ORPHAN" in motivo.upper()

    def test_a_reservation_missing_in_the_ledger_is_detected(self):
        m, risco, livro, coord = sistema()
        reservar(risco)          # risco tem, livro não (não foi enviada)
        ok, motivo = risco.capital_state_consistent()
        assert ok is False
        assert "MISSING" in motivo.upper()

    def test_the_ledger_lists_its_reservations(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="o1", reservation_id="R1")
        livro.reserve(Decimal("50"), ref="o2", reservation_id="R2")
        saldos = livro.reservation_balances()
        assert saldos == {"R1": Decimal("100"), "R2": Decimal("50")}


class TestMigracaoAmbiguaAbreIncidente:
    def test_the_restore_contract_agrees_with_the_internal_state(self):
        from tests.test_reserva_identificada import TestMigracaoDeSnapshotAntigo as M
        ajudante = M()
        m, risco, livro, coord = sistema()
        doc = ajudante._doc_antigo(
            [ajudante._reserva_doc("R1", "125"),
             ajudante._reserva_doc("R2", "125")], "250")
        info = coord.restaurar_estado(doc)
        assert any("UNKNOWN_RESERVATION_OWNERSHIP" in p
                   for p in info["pendencias"])
        assert info["exige_reconciliacao"] is True, (
            "o retorno discordava do estado interno")
        assert coord.reconciliacao_pendente is True
        assert any(i["type"] == "UNKNOWN_RESERVATION_OWNERSHIP"
                   for i in risco.incidentes_abertos())
