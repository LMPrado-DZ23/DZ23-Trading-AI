"""Lastro é por COMPONENTE, não por saldo acumulado.

`FEES:BNB` é histórico: permanece depois de uma valoração. Usá-lo como prova
de que uma ocorrência NOVA existe deixa o saldo antigo financiar uma
valoração nova — a mesma taxa provando duas dívidas diferentes.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


def sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    return m, RiskEngine(m, carteira=livro), livro


def cobrar_taxa(livro, moeda, qtd, ref="a", fill_id=""):
    livro.reserve(Decimal("100"), ref=ref, reservation_id=f"R{ref}")
    return livro.fill_buy(
        "BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
        reserved=Decimal("100"), ref=ref, agent_id="A",
        reservation_id=f"R{ref}",
        native_fees=[(Decimal(qtd), moeda)], fill_id=fill_id or ref)


class TestComponenteTemIdentidade:
    def test_the_ledger_records_a_component_id(self):
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        componentes = livro.fee_components("BNB")
        assert len(componentes) == 1
        c = componentes[0]
        assert c["fee_component_id"]
        assert c["currency"] == "BNB"
        assert Decimal(c["amount"]) == Decimal("2")
        assert c["fill_id"] == "F1"
        assert c["occurred_at"]
        assert c["valuation_status"] == "UNVALUED"

    def test_two_charges_are_two_components(self):
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        cobrar_taxa(livro, "BNB", "1", "b", fill_id="F2")
        componentes = livro.fee_components("BNB")
        assert len(componentes) == 2
        assert len({c["fee_component_id"] for c in componentes}) == 2

    def test_only_unvalued_components_are_available(self):
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids)
        assert livro.fee_components("BNB", apenas_nao_valorados=True) == []
        assert len(livro.fee_components("BNB")) == 1, "o histórico sumiu"


class TestLastroNaoSeReutiliza:
    def test_an_old_balance_does_not_back_a_new_incident(self):
        """Taxa antiga valorada; incidente novo sem componente novo."""
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids)
        assert livro.balance("FEES:BNB") == Decimal("2"), (
            "o saldo histórico precisa permanecer")

        # incidente NOVO de 1 BNB, sem nenhum componente novo no livro
        risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("1"),
                                          order_id="o9", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="binance", em="2026-09-14T23:00:00+00:00")
        with pytest.raises(ValueError, match="lastro|componente|não valorad"):
            risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                    evidence="usando saldo velho")

    def test_a_new_component_does_back_it(self):
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids)
        # agora uma cobrança NOVA de 1 BNB acontece de verdade
        cobrar_taxa(livro, "BNB", "1", "b", fill_id="F2")
        risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("1"),
                                          order_id="o9", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="binance", em="2026-09-14T23:00:00+00:00")
        risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                evidence="componente novo")
        assert livro.balance("FEES:USDT") == Decimal("900")

    def test_a_component_is_not_valued_twice(self):
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids)
        with pytest.raises(ValueError, match="já valorad|valorado"):
            livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                                rate=Decimal("400"), fonte="outra",
                                em="2026-09-15T10:00:00+00:00",
                                fee_component_ids=ids)


class TestPersistencia:
    def test_the_components_survive_a_restart(self):
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        restaurado = Ledger.from_transactions(livro.export_transactions(),
                                              currency="USDT")
        componentes = restaurado.fee_components("BNB")
        assert len(componentes) == 1
        assert componentes[0]["fill_id"] == "F1"

    def test_the_valuation_status_survives_too(self):
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids)
        restaurado = Ledger.from_transactions(livro.export_transactions(),
                                              currency="USDT")
        assert restaurado.fee_components("BNB", apenas_nao_valorados=True) == []


class TestConsumoPelaValoracao:
    """Valorar pelo RiskEngine precisa CONSUMIR os componentes. Sem isso o
    mesmo componente volta a ficar disponível e financia a próxima."""

    def test_the_risk_path_marks_the_components_as_valued(self):
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("2"),
                                          order_id="o1", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="binance", em="2026-09-14T23:00:00+00:00")
        risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                evidence="ok")
        assert livro.fee_components("BNB", apenas_nao_valorados=True) == [], (
            "os componentes continuaram disponíveis depois de valorados")

    def test_a_second_incident_cannot_reuse_them(self):
        m, risco, livro = sistema()
        cobrar_taxa(livro, "BNB", "2", "a", fill_id="F1")
        for i, qtd in enumerate(("2", "2")):
            risco.registrar_taxa_nao_valorada(
                moeda="BNB", quantidade=Decimal(qtd), order_id=f"o{i}",
                instrumento="BTC/USDT")
            inc = [x for x in risco.incidentes_abertos()
                   if x["type"] == "FEE_VALUATION_UNKNOWN"][0]
            risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                               fonte="binance", em="2026-09-14T23:00:00+00:00")
            if i == 0:
                risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                        evidence="primeira")
            else:
                with pytest.raises(ValueError, match="lastro|componente"):
                    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                            evidence="segunda")
        assert livro.balance("FEES:USDT") == Decimal("600"), (
            "a segunda valoração passou usando o mesmo componente")
