"""Cada moeda nativa chega ao livro; nenhum incidente espúrio.

Um bloco legado sobreviveu à refatoração e abria um SEGUNDO incidente com
as variáveis da cotação — criando um incidente de "0,01 USDT" ou de moeda
vazia que ninguém pediu. E `next()` fazia só a primeira moeda nativa chegar
ao livro: com BNB e SOL na mesma execução, uma das duas sumia.
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

    def __init__(self, trades):
        self.trades = trades

    def submit(self, coid, instrument, side, quantity, final_state=None):
        return {"id": f"ex-{coid}", "status": "closed", "filled": str(quantity),
                "trades": self.trades}


def sistema(trades, capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital),
                    slippage_buffer_pct=Decimal("0.05"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora(trades))
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


def incidentes_de_taxa(risco):
    return [i for i in risco.incidentes()
            if i["type"] in ("FEE_VALUATION_UNKNOWN", "FEE_CURRENCY_UNKNOWN")]


class TestSemIncidenteEspurio:
    TRADES = [{"id": "F1", "amount": "0.5", "price": "100",
               "fees": [{"cost": "1", "currency": "USDT"},
                        {"cost": "0.01", "currency": "BNB"}]}]

    def test_only_the_native_component_opens_an_incident(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        incidentes = incidentes_de_taxa(risco)
        assert len(incidentes) == 1, (
            f"{len(incidentes)} incidentes: "
            f"{[(i['type'], i.get('fee_currency'), i['amount']) for i in incidentes]}")
        assert incidentes[0]["fee_currency"] == "BNB"
        assert Decimal(incidentes[0]["fee_amount"]) == Decimal("0.01")

    def test_no_incident_in_the_quote_currency(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        assert not [i for i in incidentes_de_taxa(risco)
                    if i.get("fee_currency") == "USDT"], (
            "a taxa em USDT, que é normal, virou incidente")

    def test_no_incident_with_an_empty_currency(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        assert not [i for i in incidentes_de_taxa(risco)
                    if not i.get("fee_currency")], (
            "incidente com moeda vazia, sem origem real")


class TestTaxaNativaPura:
    TRADES = [{"id": "F1", "amount": "0.5", "price": "100",
               "fees": [{"cost": "0.01", "currency": "BNB"}]}]

    def test_one_incident_in_the_right_currency(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        incidentes = incidentes_de_taxa(risco)
        assert len(incidentes) == 1
        assert incidentes[0]["fee_currency"] == "BNB"

    def test_the_ledger_records_it(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        assert livro.balance("FEES:BNB") == Decimal("0.01")
        assert livro.balance("ASSET:BNB") == Decimal("-0.01")
        assert livro.balance("FEES:USDT") == Decimal("0")

    def test_no_zero_amount_incident(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        assert not [i for i in incidentes_de_taxa(risco)
                    if Decimal(i["amount"]) == 0]


class TestTodasAsMoedasNativas:
    TRADES = [{"id": "F1", "amount": "0.5", "price": "100",
               "fees": [{"cost": "1", "currency": "USDT"},
                        {"cost": "0.01", "currency": "BNB"},
                        {"cost": "0.20", "currency": "SOL"}]}]

    def test_every_native_currency_reaches_the_ledger(self):
        """next() pegava só a primeira: a segunda moeda sumia do livro."""
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        assert livro.balance("FEES:USDT") == Decimal("1")
        assert livro.balance("FEES:BNB") == Decimal("0.01")
        assert livro.balance("ASSET:BNB") == Decimal("-0.01")
        assert livro.balance("FEES:SOL") == Decimal("0.20")
        assert livro.balance("ASSET:SOL") == Decimal("-0.20")

    def test_two_incidents_one_per_currency(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        por_moeda = {i["fee_currency"]: Decimal(i["fee_amount"])
                     for i in incidentes_de_taxa(risco)}
        assert por_moeda == {"BNB": Decimal("0.01"), "SOL": Decimal("0.20")}

    def test_the_book_stays_balanced(self):
        m, risco, livro, coord = sistema(self.TRADES)
        entrar(coord, risco)
        assert livro.is_balanced()

    def test_the_sell_side_also_handles_several(self):
        livro = Ledger(opening_cash=Decimal("10000"))
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1")
        livro.fill_sell("BTC/USDT", Decimal("1"), Decimal("120"), Decimal("0"),
                        cost_basis=Decimal("90"), agent_id="A", ref="s",
                        native_fees=[(Decimal("0.01"), "BNB"),
                                     (Decimal("0.20"), "SOL")])
        assert livro.balance("FEES:BNB") == Decimal("0.01")
        assert livro.balance("FEES:SOL") == Decimal("0.20")
        assert livro.is_balanced()


class TestDedupCanonico:
    def test_numeric_equality_not_string_equality(self):
        """'2' e '2.0' são o mesmo valor; comparar texto não vê isso."""
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "2.0", "currency": "USDT"},
                   "fees": [{"cost": "2", "currency": "USDT"}]}]
        m, risco, livro, coord = sistema(trades)
        entrar(coord, risco)
        assert livro.balance("FEES:USDT") == Decimal("2"), (
            "a mesma taxa entrou duas vezes por diferença de formatação")
        assert not [i for i in risco.incidentes_abertos()
                    if i["type"] == "FEE_COMPONENTS_AMBIGUOUS"], (
            "reconhecer '2.0' e '2' como iguais evita o incidente; cair no "
            "ramo de ambiguidade fecharia a mesa sem motivo")

    def test_currency_case_is_normalised(self):
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "2", "currency": "usdt"},
                   "fees": [{"cost": "2", "currency": "USDT"}]}]
        m, risco, livro, coord = sistema(trades)
        entrar(coord, risco)
        assert livro.balance("FEES:USDT") == Decimal("2")
        assert not [i for i in risco.incidentes_abertos()
                    if i["type"] == "FEE_COMPONENTS_AMBIGUOUS"]

    def test_an_aggregate_that_matches_the_sum_is_not_added(self):
        """fee=3 com fees=[1,2] é o agregado dos componentes."""
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "3", "currency": "USDT"},
                   "fees": [{"cost": "1", "currency": "USDT"},
                            {"cost": "2", "currency": "USDT"}]}]
        m, risco, livro, coord = sistema(trades)
        entrar(coord, risco)
        assert livro.balance("FEES:USDT") == Decimal("3"), (
            "o agregado foi somado por cima dos componentes")
        assert not [i for i in risco.incidentes_abertos()
                    if i["type"] == "FEE_COMPONENTS_AMBIGUOUS"], (
            "3 = 1 + 2 é agregado reconhecível: tratar como ambíguo fecharia "
            "a mesa num caso que o sistema entende")

    def test_an_ambiguous_aggregate_closes_the_gate(self):
        """fee=5 com fees=[1,2]: não dá para saber se 5 inclui os dois."""
        trades = [{"id": "F1", "amount": "0.5", "price": "100",
                   "fee": {"cost": "5", "currency": "USDT"},
                   "fees": [{"cost": "1", "currency": "USDT"},
                            {"cost": "2", "currency": "USDT"}]}]
        m, risco, livro, coord = sistema(trades)
        entrar(coord, risco)
        assert [i for i in risco.incidentes_abertos()
                if i["type"] == "FEE_COMPONENTS_AMBIGUOUS"], (
            "agregado incompatível com os componentes passou em silêncio")
        assert risco.execution_blockers()


class TestAdapterLegado:
    def test_it_refuses_a_negative_amount(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        with pytest.raises(ValueError, match="positiv|asset_withdrawal"):
            livro.capital_adjustment_asset("BNB", Decimal("-2"), reason="x",
                                           actor="op")

    def test_withdrawal_has_its_own_operation(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.capital_adjustment_asset("BNB", Decimal("5"), reason="aporte",
                                       actor="op")
        livro.asset_withdrawal("BNB", Decimal("2"), reason="retirada",
                               actor="op", rate=Decimal("300"), fonte="f",
                               em="2026-09-14T23:00:00+00:00")
        assert livro.balance("ASSET:BNB") == Decimal("3")
