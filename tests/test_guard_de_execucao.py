"""Ativo descoberto não se resolve com texto, e o envio consulta tudo.

Dois furos: `UNCOVERED_ASSET_BALANCE` podia ser marcado RESOLVED com o
`ASSET:<moeda>` ainda negativo, e `submit_intent` olhava só incidentes
abertos — então bastava "resolver" com uma frase e liberar o portão de
reconciliação para uma ordem nova sair com o ativo a descoberto.
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


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


class CorretoraContada:
    mode = "testnet"

    def __init__(self):
        self.chamadas = 0

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.chamadas += 1
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=CorretoraContada())
    return m, risco, livro, coord


def descobrir(livro, moeda="BNB", qtd="0.5", ref="a"):
    livro.reserve(Decimal("100"), ref=ref, reservation_id=f"R{ref}")
    livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                   reserved=Decimal("100"), ref=ref, agent_id="A",
                   reservation_id=f"R{ref}",
                   fee_in_other_currency=(Decimal(qtd), moeda))


def enviar(coord, risco, instrumento="ETH/USDT", coid="c1"):
    p = TradeProposal(agent_id="A", strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal("0.05"),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    res = risco.reserve(p, Decimal("100"), agent_id="A")
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY, instrument=instrumento,
                         quantity=res.quantity, agent_id="A",
                         account="conta-1", client_order_id=coid,
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    return coord.submit_intent(ei)


class TestNaoResolverComSaldoNegativo:
    def test_text_does_not_resolve_a_live_shortfall(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        with pytest.raises(ValueError, match="ASSET:BNB|descoberto|negativ"):
            risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                        evidence="diferença aceita", reason="")
        atual = next(i for i in risco.incidentes()
                     if i["incident_id"] == inc["incident_id"])
        assert atual["status"] != "RESOLVED"
        assert livro.balance("ASSET:BNB") == Decimal("-0.5")

    def test_it_resolves_once_the_asset_is_covered(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        livro.capital_adjustment_asset("BNB", Decimal("0.5"), reason="aporte",
                                       actor="op")
        risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                    evidence="ativo aportado", reason="")
        assert not [i for i in risco.incidentes_abertos()
                    if i["type"] == "UNCOVERED_ASSET_BALANCE"]


class TestReconhecerNaoLibera:
    def test_acknowledging_keeps_it_blocking(self):
        """Reconhecer uma diferença viva preserva a evidência SEM liberar."""
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        registro = risco.reconhecer_incidente(
            inc["incident_id"], actor=Ator(),
            evidence="diferença aceita pela política de risco")
        assert registro["status"] == "ACKNOWLEDGED_BLOCKING"
        assert registro["acknowledged_by"] == "risk-manager-1"
        assert registro["acknowledged_evidence"]
        assert risco.execution_blockers(), "reconhecer liberou a execução"


class TestGuardDeExecucao:
    def test_the_guard_has_no_side_effects(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        antes = len(risco.incidentes())
        for _ in range(3):
            risco.execution_blockers()
        assert len(risco.incidentes()) == antes, (
            "consultar o guard criou incidente")

    def test_it_sees_a_negative_asset_even_without_an_incident(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        motivos = risco.execution_blockers()
        assert any("ASSET:BNB" in x for x in motivos), motivos

    def test_it_sees_acknowledged_incidents(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                   evidence="aceita")
        assert risco.execution_blockers()

    def test_a_clean_desk_has_no_blockers(self):
        m, risco, livro, coord = sistema()
        assert risco.execution_blockers() == []


class TestBypassCompleto:
    def test_the_broker_is_never_called_with_a_live_shortfall(self):
        """O caminho inteiro do furo, do começo ao fim."""
        m, risco, livro, coord = sistema()
        descobrir(livro)                              # 1. ativo descoberto
        risco.conferir_ativos_descobertos()           # 2. incidente aberto
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        with pytest.raises(ValueError):               # 3. "resolver" com texto
            risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                        evidence="aceita", reason="")
        coord.reconciliacao_concluida("portão liberado à força")  # 4.
        with pytest.raises(ExecutionRefused):         # 5. nova ordem
            enviar(coord, risco)
        assert coord.broker.chamadas == 0, (          # 6.
            "a corretora foi chamada com o ativo descoberto")

    def test_even_an_acknowledged_incident_blocks_the_broker(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                   evidence="aceita")
        coord.reconciliacao_concluida("liberado")
        with pytest.raises(ExecutionRefused):
            enviar(coord, risco)
        assert coord.broker.chamadas == 0

    def test_a_covered_desk_can_send(self):
        m, risco, livro, coord = sistema()
        enviar(coord, risco)
        assert coord.broker.chamadas == 1


class TestChaveEstavelPorMoeda:
    def test_repeated_scans_do_not_multiply_incidents(self):
        m, risco, livro, coord = sistema()
        descobrir(livro, "BNB", "0.5", "a")
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        livro.capital_adjustment_asset("BNB", Decimal("0.5"), reason="aporte",
                                       actor="op")
        risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                    evidence="aportado", reason="")
        descobrir(livro, "BNB", "0.8", "b")           # piora nova
        for _ in range(10):
            risco.conferir_ativos_descobertos()
        abertos = [i for i in risco.incidentes_abertos()
                   if i["type"] == "UNCOVERED_ASSET_BALANCE"]
        assert len(abertos) == 1, (
            f"cada varredura abriu um incidente: {len(abertos)}")
        assert Decimal(abertos[0]["amount"]) == Decimal("0.8")


class TestDescobertoSemVarredura:
    """O furo mais silencioso: ninguém rodou a detecção, então não existe
    incidente — e olhar só a lista de incidentes libera a ordem."""

    def test_the_broker_is_not_called_when_no_scan_ever_ran(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        assert not risco.incidentes(), (
            "o cenário precisa NÃO ter incidente registrado")
        assert livro.balance("ASSET:BNB") == Decimal("-0.5")

        with pytest.raises(ExecutionRefused, match="ASSET:BNB|descoberto"):
            enviar(coord, risco)
        assert coord.broker.chamadas == 0, (
            "ordem enviada com ativo descoberto só porque ninguém varreu")

    def test_an_active_deficit_without_an_incident_also_blocks(self):
        m, risco, livro, coord = sistema()
        risco.abrir_incidente("RESERVATION_DEFICIT", amount=Decimal("5"),
                              reservation_id="R-x")
        motivos = risco.execution_blockers()
        assert any("déficit" in x or "RESERVATION_DEFICIT" in x
                   for x in motivos), motivos
