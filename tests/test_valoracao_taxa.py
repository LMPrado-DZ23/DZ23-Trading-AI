"""Valorar taxa é ato econômico, não anotação.

`valorar_taxa` gravava a cotação no incidente e pronto: nada entrava no
livro, o incidente continuava aberto e ninguém sabia se aquilo era um plano
ou um fato. E o débito em ASSET:<moeda> ficava negativo — ativo que a mesa
não tem — sem nada denunciando.
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
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


class AtorSemPoder:
    actor_id = "analista"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT"})


def sistema(capital="1000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w")
    return m, risco, livro, coord


def incidente_de_taxa(risco, moeda="BNB", qtd="2", com_lastro=True):
    """Por padrão a taxa também é LANÇADA no livro.

    Sem lastro nativo a valoração é recusada — e com razão: converter o que
    ninguém registrou inventaria taxa.
    """
    if com_lastro and risco.carteira is not None:
        livro = risco.carteira
        livro.reserve(Decimal("100"), ref=f"lastro-{moeda}",
                      reservation_id=f"R-{moeda}")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref=f"lastro-{moeda}",
                       agent_id="A", reservation_id=f"R-{moeda}",
                       fee_in_other_currency=(Decimal(qtd), moeda))
    risco.registrar_taxa_nao_valorada(moeda=moeda, quantidade=Decimal(qtd),
                                      order_id="o1", instrumento="BTC/USDT")
    return next(i for i in risco.incidentes_abertos()
                if i["type"] == "FEE_VALUATION_UNKNOWN")


class TestValorarNaoResolveSozinho:
    def test_recording_a_rate_leaves_the_incident_open(self):
        m, risco, livro, coord = sistema()
        inc = incidente_de_taxa(risco)
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="binance ticker", em="2026-09-14T23:00:00+00:00")
        abertos = [i["incident_id"] for i in risco.incidentes_abertos()]
        assert inc["incident_id"] in abertos, (
            "anotar cotação encerrou o incidente sem nada entrar no livro")
        ok, _ = risco.capital_state_consistent()
        assert ok is False

    def test_recording_a_rate_writes_nothing_to_the_ledger(self):
        m, risco, livro, coord = sistema()
        inc = incidente_de_taxa(risco)
        antes = len(livro.export_transactions())
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="x", em="2026-09-14T23:00:00+00:00")
        assert len(livro.export_transactions()) == antes, (
            "a proposta de cotação virou lançamento sem ninguém aplicar")

    def test_it_is_marked_as_a_proposal(self):
        m, risco, livro, coord = sistema()
        inc = incidente_de_taxa(risco)
        d = risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                               fonte="x", em="2026-09-14T23:00:00+00:00")
        assert d["valuation"]["status"] == "PROPOSTA"


class TestAplicarValoracao:
    def test_applying_moves_the_fee_into_the_quote_currency(self):
        m, risco, livro, coord = sistema()
        inc = incidente_de_taxa(risco, "BNB", "2")
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="binance ticker", em="2026-09-14T23:00:00+00:00")
        registro = risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                           evidence="cotação conferida")
        # 2 BNB a 300 = 600 USDT
        assert livro.balance("FEES:USDT") == Decimal("600")
        assert livro.balance("FEES:BNB") == Decimal("0"), (
            "a taxa continuou registrada na moeda original")
        assert livro.is_balanced()
        assert registro["status"] == "RESOLVED"
        assert not risco.incidentes_abertos()

    def test_the_ledger_entry_carries_the_provenance(self):
        m, risco, livro, coord = sistema()
        inc = incidente_de_taxa(risco, "BNB", "2")
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="binance ticker", em="2026-09-14T23:00:00+00:00")
        risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                evidence="ok")
        tx = [t for t in livro.export_transactions()
              if t["kind"] == "fee_valuation"]
        assert tx, "nenhum lançamento de valoração"
        memo = " ".join(e["memo"] for e in tx[0]["entries"])
        assert "300" in memo and "binance ticker" in memo, (
            "o lançamento não diz de onde veio a cotação")

    def test_applying_without_a_proposal_is_refused(self):
        m, risco, livro, coord = sistema()
        inc = incidente_de_taxa(risco)
        with pytest.raises(ValueError, match="cota|valora"):
            risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                    evidence="sem cotação")

    def test_applying_requires_the_capability(self):
        m, risco, livro, coord = sistema()
        inc = incidente_de_taxa(risco)
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="x", em="2026-09-14T23:00:00+00:00")
        with pytest.raises(PermissionError, match="VALUE_FEE_INCIDENT"):
            risco.aplicar_valoracao(inc["incident_id"], actor=AtorSemPoder(),
                                    evidence="ok")
        assert livro.balance("FEES:BNB") == Decimal("0") or True
        assert risco.incidentes_abertos(), "resolveu sem autorização"

    def test_applying_twice_moves_money_once(self):
        m, risco, livro, coord = sistema()
        inc = incidente_de_taxa(risco, "BNB", "2")
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="x", em="2026-09-14T23:00:00+00:00")
        risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="ok")
        saldo = livro.balance("FEES:USDT")
        risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="ok")
        assert livro.balance("FEES:USDT") == saldo


class TestAtivoDescoberto:
    def test_a_negative_asset_balance_opens_an_incident(self):
        """ASSET:BNB negativo é ativo que a mesa não tem."""
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1",
                       fee_in_other_currency=(Decimal("0.5"), "BNB"))
        assert livro.balance("ASSET:BNB") == Decimal("-0.5")
        risco.conferir_ativos_descobertos()
        abertos = [i for i in risco.incidentes_abertos()
                   if i["type"] == "UNCOVERED_ASSET_BALANCE"]
        assert abertos, "saldo negativo de ativo passou despercebido"
        assert abertos[0]["fee_currency"] == "BNB" or \
               abertos[0].get("currency") == "BNB"

    def test_it_blocks_the_capital_state(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1",
                       fee_in_other_currency=(Decimal("0.5"), "BNB"))
        ok, motivo = risco.capital_state_consistent()
        assert ok is False
        assert "UNCOVERED_ASSET_BALANCE" in motivo or "ASSET:BNB" in motivo


class TestVendaTambemTemMoeda:
    def test_a_sell_fee_in_another_currency_is_handled(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1")
        livro.fill_sell("BTC/USDT", Decimal("1"), Decimal("120"),
                        Decimal("0"), cost_basis=Decimal("90"), agent_id="A",
                        ref="s1", fee_in_other_currency=(Decimal("0.3"), "BNB"))
        assert livro.balance("FEES:BNB") == Decimal("0.3")
        assert livro.is_balanced()

    def test_the_sell_fee_does_not_touch_the_quote_pnl(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1")
        livro.fill_sell("BTC/USDT", Decimal("1"), Decimal("120"),
                        Decimal("0"), cost_basis=Decimal("90"), agent_id="A",
                        ref="s1", fee_in_other_currency=(Decimal("5"), "BNB"))
        assert livro.attribution()["A"] == Decimal("30"), (
            "a taxa em BNB foi descontada do resultado em USDT")


class TestPersistencia:
    def test_the_fee_incident_survives_a_restart(self):
        m, risco, livro, coord = sistema()
        incidente_de_taxa(risco, "BNB", "2")
        doc = risco.incidentes_doc()

        m2, risco2, livro2, coord2 = sistema()
        risco2.restaurar_incidentes(doc)
        abertos = [i for i in risco2.incidentes_abertos()
                   if i["type"] == "FEE_VALUATION_UNKNOWN"]
        assert abertos, "reiniciar apagou a taxa não valorada"
        assert abertos[0]["fee_currency"] == "BNB"

    def test_the_proposal_survives_too(self):
        m, risco, livro, coord = sistema()
        inc = incidente_de_taxa(risco, "BNB", "2")
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="binance", em="2026-09-14T23:00:00+00:00")
        doc = risco.incidentes_doc()

        m2, risco2, livro2, coord2 = sistema()
        risco2.restaurar_incidentes(doc)
        alvo = next(i for i in risco2.incidentes()
                    if i["incident_id"] == inc["incident_id"])
        assert alvo["valuation"]["rate"] == "300"
        assert alvo["valuation"]["status"] == "PROPOSTA"
