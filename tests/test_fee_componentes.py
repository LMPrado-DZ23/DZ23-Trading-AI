"""Todo componente de taxa produz efeito, e valorar não quita a origem.

Três defeitos: só o "componente principal" chegava ao livro — os demais
abriam incidente sem lançamento; `fee` e `fees[]` com o mesmo componente
dobravam a taxa; e a valoração creditava ASSET:<moeda>, fazendo um ativo
descoberto desaparecer sem aporte nenhum.
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


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


class Corretora:
    mode = "testnet"

    def __init__(self, trades=None):
        self.trades = trades or []
        self.chamadas = 0

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.chamadas += 1
        return {"id": f"ex-{coid}", "status": "closed", "filled": str(quantity),
                "trades": self.trades}


def sistema(capital="10000", broker=None):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0.05"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w", broker=broker)
    return m, risco, livro, coord


def entrar(coord, risco, instrumento="BTC/USDT", coid="c1"):
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
    return coord.submit_intent(ei), res


class TestTodosOsComponentes:
    TRADES = [{"id": "F1", "amount": "0.5", "price": "100",
               "fees": [{"cost": "1", "currency": "USDT"},
                        {"cost": "2", "currency": "USDT"},
                        {"cost": "0.01", "currency": "BNB"}]}]

    def test_the_quote_components_are_summed(self):
        m, risco, livro, coord = sistema(broker=Corretora(self.TRADES))
        entrar(coord, risco)
        assert livro.balance("FEES:USDT") == Decimal("3"), (
            "só o primeiro componente em cotação virou despesa")

    def test_the_native_component_reaches_the_ledger(self):
        m, risco, livro, coord = sistema(broker=Corretora(self.TRADES))
        entrar(coord, risco)
        assert livro.balance("FEES:BNB") == Decimal("0.01")
        assert livro.balance("ASSET:BNB") == Decimal("-0.01"), (
            "o componente em BNB abriu incidente sem lançamento nenhum")

    def test_the_incident_matches_the_ledger(self):
        m, risco, livro, coord = sistema(broker=Corretora(self.TRADES))
        entrar(coord, risco)
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        assert Decimal(inc["fee_amount"]) == livro.balance("FEES:BNB")

    def test_nothing_is_lost(self):
        m, risco, livro, coord = sistema(broker=Corretora(self.TRADES))
        entrar(coord, risco)
        assert livro.is_balanced()


class TestDeduplicacao:
    def test_the_same_component_in_fee_and_fees_counts_once(self):
        """A corretora que repete o componente nos dois campos não cobra
        duas vezes — e o livro não pode inventar a segunda cobrança."""
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "2", "currency": "USDT"},
                   "fees": [{"cost": "2", "currency": "USDT"}]}]
        m, risco, livro, coord = sistema(broker=Corretora(trades))
        entrar(coord, risco)
        assert livro.balance("FEES:USDT") == Decimal("2"), (
            "a mesma taxa foi contabilizada duas vezes")

    def test_genuinely_repeated_components_are_kept(self):
        """Dois componentes iguais DENTRO de fees[] são duas cobranças."""
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fees": [{"cost": "2", "currency": "USDT"},
                            {"cost": "2", "currency": "USDT"}]}]
        m, risco, livro, coord = sistema(broker=Corretora(trades))
        entrar(coord, risco)
        assert livro.balance("FEES:USDT") == Decimal("4")


class TestValorarNaoQuitaAOrigem:
    def _com_taxa_nativa(self):
        livro = Ledger(opening_cash=Decimal("10000"))
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1",
                       fee_in_other_currency=(Decimal("2"), "BNB"))
        return livro

    def test_the_uncovered_asset_survives_the_valuation(self):
        """Valorar responde QUANTO valem 2 BNB. Não financia os 2 BNB."""
        livro = self._com_taxa_nativa()
        assert livro.balance("ASSET:BNB") == Decimal("-2")
        livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00")
        assert livro.balance("ASSET:BNB") == Decimal("-2"), (
            "a valoração fez o ativo descoberto desaparecer sem aporte")

    def test_the_functional_side_creates_a_liability(self):
        livro = self._com_taxa_nativa()
        livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00")
        assert livro.balance("FEES:USDT") == Decimal("600")
        assert livro.balance("LIABILITY:FEE_FUNDING") == Decimal("-600"), (
            "a despesa apareceu sem dizer contra o quê")
        assert livro.is_balanced()

    def test_the_desk_stays_blocked_until_funded(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1",
                       fee_in_other_currency=(Decimal("2"), "BNB"))
        livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00")
        assert risco.execution_blockers(), (
            "valorar liberou a mesa com o ativo ainda descoberto")

    def test_funding_the_asset_is_what_clears_it(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1",
                       fee_in_other_currency=(Decimal("2"), "BNB"))
        livro.asset_contribution("BNB", Decimal("2"), reason="aporte",
                                 actor="op", rate=Decimal("300"),
                                 fonte="binance",
                                 em="2026-09-14T23:00:00+00:00")
        assert livro.balance("ASSET:BNB") == Decimal("0")
        assert not risco.ativos_descobertos()


class TestAporteDeAtivoDeclarado:
    def test_the_contribution_declares_both_sides(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        tx = livro.asset_contribution("BNB", Decimal("2"), reason="aporte",
                                      actor="op", rate=Decimal("300"),
                                      fonte="binance",
                                      em="2026-09-14T23:00:00+00:00")
        d = livro.find_transaction(tx.tx_id)
        assert d["native_currency"] == "BNB" and d["native_amount"] == "2"
        assert d["functional_currency"] == "USDT"
        assert Decimal(d["functional_amount"]) == Decimal("600")
        assert livro.is_balanced()

    def test_a_contribution_must_be_positive(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        for q in (Decimal("0"), Decimal("-2")):
            with pytest.raises(ValueError, match="positiv"):
                livro.asset_contribution("BNB", q, reason="x", actor="op",
                                         rate=Decimal("300"), fonte="f",
                                         em="2026-09-14T23:00:00+00:00")

    def test_withdrawal_is_a_separate_operation(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.asset_contribution("BNB", Decimal("5"), reason="aporte",
                                 actor="op", rate=Decimal("300"), fonte="f",
                                 em="2026-09-14T23:00:00+00:00")
        livro.asset_withdrawal("BNB", Decimal("2"), reason="retirada",
                               actor="op", rate=Decimal("300"), fonte="f",
                               em="2026-09-14T23:00:00+00:00")
        assert livro.balance("ASSET:BNB") == Decimal("3")
        assert livro.is_balanced()


class TestValidacaoDimensionalPorTransacao:
    def test_a_cross_unit_transaction_is_refused_at_entry(self):
        """Não basta o livro voltar a zero depois: o lançamento que mistura
        unidades precisa ser recusado na hora."""
        from tradingagents.desk.ledger import Entry
        livro = Ledger(opening_cash=Decimal("1000"))
        with pytest.raises(ValueError, match="unidade|dimens|moeda"):
            livro._post("suspeito",
                        [Entry("FEES:BNB", Decimal("2"), "taxa"),
                         Entry("CASH:USDT", Decimal("-2"), "pago")],
                        ref="cruzado")

    def test_a_declared_conversion_is_allowed(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1",
                       fee_in_other_currency=(Decimal("2"), "BNB"))
        livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00")
        assert livro.is_balanced()


class TestResolvedorGenericoRecusado:
    def test_a_fee_currency_incident_is_not_generically_resolvable(self):
        m, risco, livro, coord = sistema()
        risco.registrar_taxa_sem_moeda(quantidade=Decimal("2"),
                                       order_id="o1", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_CURRENCY_UNKNOWN"][0]
        with pytest.raises(ValueError, match="moeda|caminho próprio|FEE_"):
            risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                        evidence="conferido", reason="")
        assert risco.incidentes_abertos()

    def test_a_valuation_incident_is_not_generically_resolvable(self):
        m, risco, livro, coord = sistema()
        risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("2"),
                                          order_id="o1", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        with pytest.raises(ValueError, match="valora|caminho próprio|FEE_"):
            risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                        evidence="conferido", reason="")

    def test_the_bypass_does_not_open_the_gate(self):
        m, risco, livro, coord = sistema(broker=Corretora())
        risco.registrar_taxa_sem_moeda(quantidade=Decimal("2"),
                                       order_id="o1", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_CURRENCY_UNKNOWN"][0]
        with pytest.raises(ValueError):
            risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                        evidence="conferido", reason="")
        coord.reconciliacao_concluida("liberado à força")
        from tradingagents.desk.execution import ExecutionRefused
        with pytest.raises(ExecutionRefused):
            entrar(coord, risco, "ETH/USDT", "nova")
        assert coord.broker.chamadas == 0
