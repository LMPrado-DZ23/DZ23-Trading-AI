"""Taxa em moeda diferente da cotação.

`fee` era somado ao custo como se fosse sempre na moeda de cotação. Pagar
0,001 BNB numa compra em USDT somava 0,001 ao custo — e a diferença ficava
escondida dentro de um livro que continua fechando, porque a aritmética
está certa; o que está errado é o SIGNIFICADO do número.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine


def sistema(capital="1000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w")
    return m, risco, livro, coord


class TestContaDeTaxaPorMoeda:
    def test_fees_are_kept_per_currency(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("1"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1")
        saldos = livro.balances()
        assert "FEES:USDT" in saldos, "a taxa perdeu a moeda"
        assert saldos["FEES:USDT"] == Decimal("1")

    def test_a_fee_in_another_currency_gets_its_own_account(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1",
                       fee_in_other_currency=(Decimal("0.001"), "BNB"))
        saldos = livro.balances()
        assert "FEES:BNB" in saldos
        assert saldos["FEES:BNB"] == Decimal("0.001")
        assert livro.is_balanced(), "o livro precisa continuar fechando"

    def test_the_other_currency_fee_never_enters_the_quote_cost(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o", agent_id="A",
                       reservation_id="R1",
                       fee_in_other_currency=(Decimal("0.5"), "BNB"))
        # custo em USDT é 90; a taxa em BNB não some 0,5 em USDT
        detalhe = livro.open_positions_detail()["BTC/USDT"]
        assert Decimal(detalhe["custo_total"]) == Decimal("90"), (
            "a taxa em BNB foi somada como se fosse USDT")


class TestIncidenteDeValoracao:
    def test_a_fee_in_another_currency_opens_an_incident(self):
        m, risco, livro, coord = sistema()
        risco.registrar_taxa_nao_valorada(
            moeda="BNB", quantidade=Decimal("0.001"), order_id="ord-1",
            instrumento="BTC/USDT")
        abertos = [i for i in risco.incidentes_abertos()
                   if i["type"] == "FEE_VALUATION_UNKNOWN"]
        assert abertos, "taxa não valorada passou despercebida"
        inc = abertos[0]
        assert inc["fee_currency"] == "BNB"
        assert Decimal(inc["fee_amount"]) == Decimal("0.001")

    def test_it_blocks_new_risk(self):
        m, risco, livro, coord = sistema()
        risco.registrar_taxa_nao_valorada(
            moeda="BNB", quantidade=Decimal("0.001"), order_id="ord-1",
            instrumento="BTC/USDT")
        ok, motivo = risco.capital_state_consistent()
        assert ok is False
        assert "FEE_VALUATION_UNKNOWN" in motivo

    def test_several_fees_in_the_same_currency_aggregate(self):
        m, risco, livro, coord = sistema()
        for i in range(3):
            risco.registrar_taxa_nao_valorada(
                moeda="BNB", quantidade=Decimal("0.001"), order_id=f"ord-{i}",
                instrumento="BTC/USDT")
        incidentes = [i for i in risco.incidentes_abertos()
                      if i["type"] == "FEE_VALUATION_UNKNOWN"]
        assert len(incidentes) == 1, "um incidente por moeda, não por ordem"
        assert Decimal(incidentes[0]["fee_amount"]) == Decimal("0.003")
        assert len(incidentes[0]["occurrences"]) == 3, "a origem de cada uma sumiu"

    def test_different_currencies_are_different_incidents(self):
        m, risco, livro, coord = sistema()
        risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("1"),
                                          order_id="o1", instrumento="BTC/USDT")
        risco.registrar_taxa_nao_valorada(moeda="ETH", quantidade=Decimal("1"),
                                          order_id="o2", instrumento="BTC/USDT")
        moedas = {i["fee_currency"] for i in risco.incidentes_abertos()
                  if i["type"] == "FEE_VALUATION_UNKNOWN"}
        assert moedas == {"BNB", "ETH"}


class TestNadaDeTaxaDeCambioInventada:
    def test_no_implicit_conversion_happens(self):
        """Converter com cotação adivinhada dá número que parece certo."""
        import inspect

        from tradingagents.desk import execution
        fonte = inspect.getsource(execution)
        assert "taxa_cambio_padrao" not in fonte
        assert "FEE_RATE_DEFAULT" not in fonte

    def test_resolving_requires_a_rate_with_provenance(self):
        m, risco, livro, coord = sistema()
        risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("2"),
                                          order_id="o1", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        with pytest.raises(ValueError, match="cota|taxa de câmbio|fonte"):
            risco.valorar_taxa(inc["incident_id"], rate=None, fonte="",
                               em=None)

    def test_a_rate_without_a_source_is_refused(self):
        m, risco, livro, coord = sistema()
        risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("2"),
                                          order_id="o1", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        with pytest.raises(ValueError, match="fonte"):
            risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                               fonte="", em="2026-09-14T23:00:00+00:00")

    def test_valuing_with_provenance_records_everything(self):
        m, risco, livro, coord = sistema()
        risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("2"),
                                          order_id="o1", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        registro = risco.valorar_taxa(
            inc["incident_id"], rate=Decimal("300"), fonte="binance ticker",
            em="2026-09-14T23:00:00+00:00")
        assert registro["valuation"]["rate"] == "300"
        assert registro["valuation"]["fonte"] == "binance ticker"
        assert registro["valuation"]["em"]
        assert Decimal(registro["valuation"]["valor_em_cotacao"]) == Decimal("600")


class TestCustoRealNaoIgnoraATaxa:
    def test_a_fee_in_another_currency_is_not_counted_as_zero(self):
        """Ignorar a taxa faz a execução parecer mais barata do que foi."""
        import inspect

        from tradingagents.desk.execution import ExecutionCoordinator
        fonte = inspect.getsource(ExecutionCoordinator.aplicar_fill)
        assert "fee_currency" in fonte or "moeda_taxa" in fonte, (
            "o fill não olha a moeda da taxa: soma tudo como se fosse cotação")


class TestFillComTaxaEmOutraMoeda:
    """Comportamento, não leitura de código: o fill precisa agir."""

    def _ordem(self, coord, risco):
        from datetime import timedelta

        from tradingagents.desk.intents import ExecutionIntent, Kind, Side
        from tradingagents.desk.models import Action, TradeProposal, utcnow
        p = TradeProposal(agent_id="A", strategy="s", instrument="BTC/USDT",
                          action=Action.BUY, reason="t",
                          desired_exposure_pct=Decimal("0.25"),
                          confidence=Decimal("0.8"),
                          valid_until=utcnow() + timedelta(minutes=5))
        res = risco.reserve(p, Decimal("100"), agent_id="A")

        class Corretora:
            mode = "testnet"

            def submit(self, coid, instrument, side, quantity, final_state=None):
                return {"id": "ex-1", "status": "open", "filled": 0}

        coord.broker = Corretora()
        ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                             instrument="BTC/USDT", quantity=res.quantity,
                             agent_id="A", account="conta-1",
                             client_order_id="c1",
                             reservation_id=res.reservation_id,
                             reserved_amount=res.amount)
        return coord.submit_intent(ei), res

    def test_a_bnb_fee_opens_the_incident_and_closes_the_gate(self):
        m, risco, livro, coord = sistema()
        rec, res = self._ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("0.001"),
                           fill_id="f1", fee_currency="BNB")
        abertos = [i for i in risco.incidentes_abertos()
                   if i["type"] == "FEE_VALUATION_UNKNOWN"]
        assert abertos, "o fill aceitou a taxa em BNB sem registrar nada"
        assert abertos[0]["fee_currency"] == "BNB"
        assert coord.reconciliacao_pendente is True, (
            "custo real desconhecido não travou risco novo")

    def test_the_bnb_fee_does_not_inflate_the_quote_cost(self):
        """0,001 BNB somado ao custo em USDT seria unidade errada."""
        m, risco, livro, coord = sistema()
        rec, res = self._ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("5"),
                           fill_id="f1", fee_currency="BNB")
        esperado = res.quantity * Decimal("100")
        detalhe = livro.open_positions_detail()["BTC/USDT"]
        assert Decimal(detalhe["custo_total"]) == esperado, (
            "a taxa em BNB entrou no custo em USDT")
        assert livro.balance("FEES:USDT") == Decimal("0")
        assert livro.balance("FEES:BNB") == Decimal("5")

    def test_a_fee_in_the_quote_currency_behaves_normally(self):
        # preço abaixo do reservado para a taxa CABER na cobertura: com
        # 100 o custo bate exatamente o reservado e a taxa viraria déficit —
        # que é outro incidente, correto, mas não o desta prova
        m, risco, livro, coord = sistema()
        rec, res = self._ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("99"), taxa=Decimal("1"),
                           fill_id="f1", fee_currency="USDT")
        assert livro.balance("FEES:USDT") == Decimal("1")
        assert not [i for i in risco.incidentes_abertos()
                    if i["type"] == "FEE_VALUATION_UNKNOWN"]
        assert coord.reconciliacao_pendente is False

    def test_no_currency_informed_is_now_unknown(self):
        """Antes assumia-se a cotação. Assumir é um palpite com cara de
        dado: a corretora que não declara a moeda deixa o custo real
        desconhecido, e isso agora aparece."""
        m, risco, livro, coord = sistema()
        rec, res = self._ordem(coord, risco)
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("99"), taxa=Decimal("1"),
                           fill_id="f1")
        assert [i for i in risco.incidentes_abertos()
                if i["type"] == "FEE_CURRENCY_UNKNOWN"]
        assert livro.balance("FEES:USDT") == Decimal("0"), (
            "a taxa sem moeda entrou como se fosse cotação")
