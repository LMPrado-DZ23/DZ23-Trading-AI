"""Cada componente é processado UMA vez, com moeda canônica.

Havia dois caminhos: um bloco antecipado tratava a "taxa principal" e o laço
tratava a lista inteira — o mesmo componente sem moeda era contado duas
vezes (0,01 virava 0,02). E a moeda ia crua para o livro: "usdt" criava uma
conta nativa diferente de "USDT".
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

    def __init__(self, trades):
        self.trades = trades
        self.chamadas = 0

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.chamadas += 1
        return {"id": f"ex-{coid}", "status": "closed", "filled": str(quantity),
                "trades": self.trades}


def sistema(trades=None, capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital),
                    slippage_buffer_pct=Decimal("0.05"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora(trades or []))
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
    return coord.submit_intent(ei)


class TestPassagemUnica:
    def test_a_fee_without_currency_is_counted_once(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fees": [{"cost": "0.01", "currency": ""}]}]
        m, risco, livro, coord = sistema(trades)
        entrar(coord, risco)
        incidentes = [i for i in risco.incidentes()
                      if i["type"] == "FEE_CURRENCY_UNKNOWN"]
        assert len(incidentes) == 1
        assert Decimal(incidentes[0]["fee_amount"]) == Decimal("0.01"), (
            "o mesmo componente foi contado duas vezes")

    def test_the_bare_fee_field_is_also_counted_once(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "0.02"}}]
        m, risco, livro, coord = sistema(trades)
        entrar(coord, risco)
        inc = [i for i in risco.incidentes()
               if i["type"] == "FEE_CURRENCY_UNKNOWN"][0]
        assert Decimal(inc["fee_amount"]) == Decimal("0.02")

    def test_a_native_fee_is_counted_once(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fees": [{"cost": "0.01", "currency": "BNB"}]}]
        m, risco, livro, coord = sistema(trades)
        entrar(coord, risco)
        assert livro.balance("FEES:BNB") == Decimal("0.01")
        inc = [i for i in risco.incidentes()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        assert Decimal(inc["fee_amount"]) == Decimal("0.01")


class TestMesmoResultadoNosDoisCaminhos:
    FILL = {"id": "F1", "amount": "0.5", "price": "100",
            "fee": {"cost": "0.03"}}

    def _por_reconciliacao(self):
        from tradingagents.desk.execution import OrderState
        m, risco, livro, coord = sistema()
        coord.orders["ord-1"] = coord._montar_registro(
            order_id="ord-1", state=OrderState.ACKNOWLEDGED,
            instrument="BTC/USDT", side="buy", quantity=Decimal("0.5"),
            client_order_id="coid-1", agent_id="A", external_id="ex-1")
        coord.reconciliar(
            lambda coid: {"resultado": "FOUND", "ordem": {
                "id": "ex-1", "status": "filled", "filled": "0.5",
                "average": "100"}},
            consultar_posicoes=lambda: {"BTC/USDT": Decimal("0.5")},
            consultar_ordens_abertas=lambda: [],
            consultar_fills=lambda coid, exid="": [
                {"fill_id": "F1", "quantity": Decimal("0.5"),
                 "price": Decimal("100"), "fee": Decimal("0.03"),
                 "fee_currency": "", "side": "buy"}])
        return risco

    def test_both_paths_produce_the_same_incident(self):
        m, risco, livro, coord = sistema([self.FILL])
        entrar(coord, risco)
        imediato = [i for i in risco.incidentes()
                    if i["type"] == "FEE_CURRENCY_UNKNOWN"]
        outro = [i for i in self._por_reconciliacao().incidentes()
                 if i["type"] == "FEE_CURRENCY_UNKNOWN"]
        assert len(imediato) == len(outro) == 1
        assert Decimal(imediato[0]["fee_amount"]) == \
            Decimal(outro[0]["fee_amount"]) == Decimal("0.03")


class TestMoedaCanonica:
    @pytest.mark.parametrize("escrita", ["usdt", " USDT ", "Usdt"])
    def test_the_quote_currency_is_recognised_however_written(self, escrita):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fees": [{"cost": "2", "currency": escrita}]}]
        m, risco, livro, coord = sistema(trades)
        entrar(coord, risco)
        assert livro.balance("FEES:USDT") == Decimal("2"), (
            f"{escrita!r} não foi reconhecida como a moeda de cotação")
        assert not risco.incidentes(), "virou incidente por capitalização"

    @pytest.mark.parametrize("escrita", ["bnb", " BnB ", "BNB"])
    def test_a_native_currency_uses_one_account(self, escrita):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fees": [{"cost": "0.01", "currency": escrita}]}]
        m, risco, livro, coord = sistema(trades)
        entrar(coord, risco)
        assert livro.balance("FEES:BNB") == Decimal("0.01")
        contas = [c for c in livro.balances() if c.startswith("FEES:")]
        assert len(set(contas)) == len(contas)
        assert all(c.split(":", 1)[1].isupper() for c in contas), contas


class TestAmbiguidadeEstruturada:
    TRADES = [{"id": "F1", "amount": "0.5", "price": "100",
               "fee": {"cost": "5", "currency": "USDT"},
               "fees": [{"cost": "1", "currency": "USDT"},
                        {"cost": "2", "currency": "USDT"}]}]

    def test_the_incident_carries_the_numbers(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_COMPONENTS_AMBIGUOUS"][0]
        assert Decimal(inc["reported_total"]) == Decimal("5")
        assert inc["reported_currency"] == "USDT"
        assert Decimal(inc["component_sum"]) == Decimal("3")
        assert Decimal(inc["unexplained_difference"]) == Decimal("2"), (
            "a diferença de 2 se perdeu")
        assert inc["components"]
        assert inc["fill_id"]

    def test_the_known_components_are_still_booked(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        assert livro.balance("FEES:USDT") == Decimal("3"), (
            "os componentes conhecidos foram descartados junto")

    def test_the_generic_resolver_refuses_it(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_COMPONENTS_AMBIGUOUS"][0]
        with pytest.raises(ValueError, match="ambigu|caminho próprio|FEE_"):
            risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                        evidence="conferido", reason="")
        assert risco.execution_blockers()

    def test_the_gate_stays_closed_for_a_new_entry(self):
        from tradingagents.desk.execution import ExecutionRefused
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        chamadas = coord.broker.chamadas
        coord.reconciliacao_concluida("liberado à força")
        with pytest.raises(ExecutionRefused):
            entrar(coord, risco, "ETH/USDT", "nova")
        assert coord.broker.chamadas == chamadas


class TestValidacaoNoLivro:
    """O livro não pode depender de o parser acima ter feito o trabalho."""

    @pytest.mark.parametrize("valor,moeda", [
        (Decimal("-1"), "BNB"),
        (Decimal("0.01"), ""),
        (Decimal("0.01"), "bnb"),
        (Decimal("0.01"), "USDT"),
    ])
    def test_a_bad_native_component_is_refused(self, valor, moeda):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        with pytest.raises(ValueError):
            livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"),
                           Decimal("0"), reserved=Decimal("100"), ref="o",
                           agent_id="A", reservation_id="R1",
                           native_fees=[(valor, moeda)])

    def test_both_fee_parameters_together_is_a_conflict(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        with pytest.raises(ValueError, match="conflito|ambos|apenas um"):
            livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"),
                           Decimal("0"), reserved=Decimal("100"), ref="o",
                           agent_id="A", reservation_id="R1",
                           native_fees=[(Decimal("0.01"), "BNB")],
                           fee_in_other_currency=(Decimal("0.02"), "SOL"))


class TestAporteLegadoNaoLimpaIncidente:
    def test_the_legacy_adapter_is_marked_internal(self):
        from tradingagents.desk.ledger import Ledger as L
        doc = (L.capital_adjustment_asset.__doc__ or "").lower()
        assert "migra" in doc or "interna" in doc

    def test_clearing_an_operational_incident_needs_provenance(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1",
                       native_fees=[(Decimal("0.5"), "BNB")])
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        livro.asset_contribution("BNB", Decimal("0.5"), reason="aporte",
                                 actor="op", rate=Decimal("300"),
                                 fonte="binance",
                                 em="2026-09-14T23:00:00+00:00")
        risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                    evidence="aporte com cotação", reason="")
        assert not risco.ativos_descobertos()


class TestCanonizacaoNaOrigem:
    """A canonicalização acontece no PARSER, não só mais adiante.

    Deixar a forma crua atravessar e normalizar depois funciona até alguém
    consumir o componente por outro caminho — e aí "usdt" vira uma conta
    nova.
    """

    @pytest.mark.parametrize("escrita,esperada", [
        ("usdt", "USDT"), (" USDT ", "USDT"), ("Usdt", "USDT"),
        (" BnB ", "BNB"), ("bnb", "BNB"),
    ])
    def test_the_parser_emits_canonical_currency(self, escrita, esperada):
        fills = ExecutionCoordinator._fills_da_resposta(
            {"trades": [{"id": "F1", "amount": "1", "price": "100",
                         "fees": [{"cost": "2", "currency": escrita}]}]})
        assert fills[0]["fees"][0]["currency"] == esperada
        assert fills[0]["fee_currency"] == esperada

    def test_the_components_list_is_canonical_too(self):
        componentes = ExecutionCoordinator._componentes_de_taxa(
            {"fees": [{"cost": "1", "currency": " usdt "},
                      {"cost": "2", "currency": "BnB"}]})
        assert [c["currency"] for c in componentes] == ["USDT", "BNB"]
