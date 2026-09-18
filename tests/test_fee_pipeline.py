"""Pipeline de taxa: a moeda sobrevive do broker até o livro.

Três furos: a resposta imediata do envio lia `fee.cost` e jogava fora
`fee.currency`; taxa com valor e sem moeda era tratada como se fosse a de
cotação; e a venda pelo coordenador não repassava taxa em outra moeda.
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


class CorretoraComTrades:
    """Devolve execuções individualizadas no próprio envio."""
    mode = "testnet"

    def __init__(self, trades):
        self.trades = trades

    def submit(self, coid, instrument, side, quantity, final_state=None):
        return {"id": f"ex-{coid}", "status": "closed", "filled": str(quantity),
                "trades": self.trades}


def sistema(capital="10000", broker=None):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
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


class TestRespostaImediataPreservaAMoeda:
    def test_the_parser_keeps_the_fee_currency(self):
        parser = ExecutionCoordinator._fills_da_resposta
        fills = parser({"trades": [{"id": "F1", "amount": "1", "price": "100",
                                    "fee": {"cost": "0.001",
                                            "currency": "BNB"}}]})
        assert fills, "nenhuma execução extraída"
        assert fills[0]["fee_currency"] == "BNB", (
            "a moeda da taxa foi descartada no envio")
        assert fills[0]["taxa"] == Decimal("0.001")

    def test_a_submit_with_a_bnb_fee_opens_the_incident(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "0.001", "currency": "BNB"}}]
        m, risco, livro, coord = sistema(broker=CorretoraComTrades(trades))
        entrar(coord, risco)
        assert [i for i in risco.incidentes_abertos()
                if i["type"] == "FEE_VALUATION_UNKNOWN"], (
            "taxa em BNB no envio passou como se fosse cotação")
        assert livro.balance("FEES:BNB") == Decimal("0.001")
        assert livro.balance("FEES:USDT") == Decimal("0")

    def test_the_fees_list_is_also_read(self):
        parser = ExecutionCoordinator._fills_da_resposta
        fills = parser({"trades": [{"id": "F1", "amount": "1", "price": "100",
                                    "fees": [{"cost": "2",
                                              "currency": "USDT"}]}]})
        assert fills[0]["taxa"] == Decimal("2")
        assert fills[0]["fee_currency"] == "USDT"

    def test_several_fee_components_are_not_lost(self):
        parser = ExecutionCoordinator._fills_da_resposta
        fills = parser({"trades": [{"id": "F1", "amount": "1", "price": "100",
                                    "fees": [{"cost": "1", "currency": "USDT"},
                                             {"cost": "0.01",
                                              "currency": "BNB"}]}]})
        componentes = fills[0]["fees"]
        assert len(componentes) == 2, "um componente de taxa sumiu"
        assert {c["currency"] for c in componentes} == {"USDT", "BNB"}


class TestMoedaAusenteNaoViraCotacao:
    def test_a_fee_without_currency_opens_its_own_incident(self):
        """Assumir a moeda de cotação é um palpite com cara de dado."""
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "0.001"}}]        # sem currency
        m, risco, livro, coord = sistema(broker=CorretoraComTrades(trades))
        entrar(coord, risco)
        tipos = {i["type"] for i in risco.incidentes_abertos()}
        assert "FEE_CURRENCY_UNKNOWN" in tipos, tipos

    def test_it_blocks_new_risk(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "0.001"}}]
        m, risco, livro, coord = sistema(broker=CorretoraComTrades(trades))
        entrar(coord, risco)
        assert risco.execution_blockers()

    def test_a_zero_fee_may_omit_the_currency(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "0"}}]
        m, risco, livro, coord = sistema(broker=CorretoraComTrades(trades))
        entrar(coord, risco)
        assert not [i for i in risco.incidentes_abertos()
                    if i["type"] == "FEE_CURRENCY_UNKNOWN"], (
            "taxa zero não precisa de moeda")

    def test_no_fee_at_all_is_fine(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100"}]
        m, risco, livro, coord = sistema(broker=CorretoraComTrades(trades))
        entrar(coord, risco)
        assert not risco.execution_blockers()


class TestValidacaoDaTaxa:
    @pytest.mark.parametrize("valor", ["-1", "NaN", "Infinity", "-Infinity"])
    def test_a_broken_fee_is_refused(self, valor):
        """Negativa é rebate (contrato separado); NaN e Infinity não são
        números com que se possa contabilizar nada."""
        parser = ExecutionCoordinator._fills_da_resposta
        fills = parser({"trades": [{"id": "F1", "amount": "1", "price": "100",
                                    "fee": {"cost": valor,
                                            "currency": "USDT"}}]})
        assert fills[0].get("fee_invalida"), (
            f"taxa {valor} passou como se fosse contabilizável")


class TestVendaPeloCoordenador:
    def test_the_sell_fee_currency_reaches_the_ledger(self):
        m, risco, livro, coord = sistema()
        coord.broker = CorretoraComTrades([])
        rec, res = entrar(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("90"), taxa=Decimal("0"),
                           fill_id="f1")
        ei = ExecutionIntent(kind=Kind.EXIT, side=Side.SELL,
                             instrument="BTC/USDT", quantity=res.quantity,
                             agent_id="A", account="conta-1",
                             client_order_id="saida")
        saida = coord.submit_intent(ei)
        coord.aplicar_fill(saida, side="sell", quantidade=res.quantity,
                           preco=Decimal("120"), taxa=Decimal("0.02"),
                           fill_id="f2", fee_currency="BNB")
        assert livro.balance("FEES:BNB") == Decimal("0.02"), (
            "a venda descartou a taxa em outra moeda")
        assert livro.is_balanced()

    def test_the_sell_opens_the_valuation_incident(self):
        m, risco, livro, coord = sistema()
        coord.broker = CorretoraComTrades([])
        rec, res = entrar(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("90"), taxa=Decimal("0"),
                           fill_id="f1")
        ei = ExecutionIntent(kind=Kind.EXIT, side=Side.SELL,
                             instrument="BTC/USDT", quantity=res.quantity,
                             agent_id="A", account="conta-1",
                             client_order_id="saida")
        saida = coord.submit_intent(ei)
        coord.aplicar_fill(saida, side="sell", quantidade=res.quantity,
                           preco=Decimal("120"), taxa=Decimal("0.02"),
                           fill_id="f2", fee_currency="BNB")
        assert [i for i in risco.incidentes_abertos()
                if i["type"] == "FEE_VALUATION_UNKNOWN"]

    def test_the_traders_result_is_not_touched_by_it(self):
        m, risco, livro, coord = sistema()
        coord.broker = CorretoraComTrades([])
        rec, res = entrar(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("90"), taxa=Decimal("0"),
                           fill_id="f1")
        ei = ExecutionIntent(kind=Kind.EXIT, side=Side.SELL,
                             instrument="BTC/USDT", quantity=res.quantity,
                             agent_id="A", account="conta-1",
                             client_order_id="saida")
        saida = coord.submit_intent(ei)
        coord.aplicar_fill(saida, side="sell", quantidade=res.quantity,
                           preco=Decimal("120"), taxa=Decimal("5"),
                           fill_id="f2", fee_currency="BNB")
        bruto = (Decimal("120") - Decimal("90")) * res.quantity
        assert livro.attribution()["A"] == bruto, (
            "a taxa em BNB foi descontada do resultado em USDT")


class TestComponentesChegamAoLivro:
    """Não basta o parser devolver a lista: cada componente precisa
    produzir efeito. Um deles sumindo no caminho é taxa não contabilizada."""

    def test_the_second_component_opens_its_own_incident(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fees": [{"cost": "1", "currency": "USDT"},
                            {"cost": "0.01", "currency": "BNB"}]}]
        m, risco, livro, coord = sistema(broker=CorretoraComTrades(trades))
        entrar(coord, risco)
        moedas = {i["fee_currency"] for i in risco.incidentes_abertos()
                  if i["type"] == "FEE_VALUATION_UNKNOWN"}
        assert "BNB" in moedas, (
            "o componente em BNB desapareceu entre o parser e o livro")

    def test_a_second_component_without_currency_is_flagged(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fees": [{"cost": "1", "currency": "USDT"},
                            {"cost": "0.01"}]}]
        m, risco, livro, coord = sistema(broker=CorretoraComTrades(trades))
        entrar(coord, risco)
        assert [i for i in risco.incidentes_abertos()
                if i["type"] == "FEE_CURRENCY_UNKNOWN"]

    def test_the_paper_broker_declares_its_own_currency(self):
        """Nosso próprio broker omitindo a moeda derrubaria o ciclo inteiro
        em FEE_CURRENCY_UNKNOWN — ele sabe em que unidade cobra."""
        from tradingagents.desk.execution import PaperBroker
        b = PaperBroker(price=Decimal("100"))
        r = b.submit("coid-1", "BTC/USDT", "buy", Decimal("1"))
        taxa = r["trades"][0]["fee"]
        assert taxa.get("currency"), "o broker de papel não declarou a moeda"

    def test_a_paper_cycle_does_not_open_a_currency_incident(self):
        from tradingagents.desk.execution import PaperBroker
        m, risco, livro, coord = sistema(broker=PaperBroker(price=Decimal("100")))
        entrar(coord, risco)
        assert not [i for i in risco.incidentes_abertos()
                    if i["type"] == "FEE_CURRENCY_UNKNOWN"], (
            "o ciclo de papel virou incidente de moeda desconhecida")
