"""Contrato do adaptador de corretora, exercitado pelo CÓDIGO REAL.

A auditoria apontou com razão: a reconciliação era provada só com lambdas.
Aqui a exchange é falsa, mas o adaptador é o de produção — é ele que traduz
a resposta do CCXT para a estrutura canônica.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.brokers import BrokerConsultaIndisponivel, CCXTDeskBroker


class ExchangeFalsa:
    """Imita a superfície do CCXT que o adaptador realmente usa."""

    id = "binance"

    def __init__(self, *, saldo=None, ordens=None, erro=None):
        self._saldo = saldo
        self._ordens = ordens or []
        self._erro = erro
        self.urls = {"test": "https://testnet"}

    def set_sandbox_mode(self, ativo):
        self.sandbox = ativo

    def fetch_balance(self):
        if self._erro:
            raise self._erro
        return self._saldo

    def fetch_orders(self, symbol=None, since=None, limit=None):
        if self._erro:
            raise self._erro
        return self._ordens


def adaptador(exchange) -> CCXTDeskBroker:
    """Constrói o adaptador REAL sem tocar a rede."""
    b = CCXTDeskBroker.__new__(CCXTDeskBroker)
    ponte = type("PonteFalsa", (), {})()
    ponte._ex = exchange
    ponte.mode = "testnet"
    b.bridge = ponte
    b.final_state = {}
    b.mode = "testnet"
    b.submitted = {}
    b.universe = ("BTC/USDT", "ETH/USDT")
    return b


class TestConsultaDePosicoes:
    def test_zero_balances_are_omitted(self):
        a = adaptador(ExchangeFalsa(saldo={"total": {
            "BTC": 0.5, "ETH": 0, "USDT": 900}}))
        assert a.fetch_positions() == {"BTC/USDT": Decimal("0.5")}

    def test_quote_currency_is_not_a_position(self):
        a = adaptador(ExchangeFalsa(saldo={"total": {"USDT": 1000}}))
        assert a.fetch_positions() == {}, "o caixa virou posição"

    def test_quantities_are_decimal_never_float(self):
        a = adaptador(ExchangeFalsa(saldo={"total": {"BTC": 0.1}}))
        valor = a.fetch_positions()["BTC/USDT"]
        assert isinstance(valor, Decimal)

    def test_symbols_are_normalised_with_the_quote(self):
        a = adaptador(ExchangeFalsa(saldo={"total": {"ETH": 2}}))
        assert "ETH/USDT" in a.fetch_positions()

    def test_external_error_becomes_unknown_not_empty(self):
        """O pior erro possível aqui seria devolver {} numa falha: o sistema
        concluiria 'não há posição' e liberaria."""
        a = adaptador(ExchangeFalsa(erro=RuntimeError("timeout")))
        with pytest.raises(BrokerConsultaIndisponivel):
            a.fetch_positions()

    def test_malformed_payload_is_refused(self):
        for saldo in ({}, {"total": None}, "não é dict"):
            a = adaptador(ExchangeFalsa(saldo=saldo))
            with pytest.raises(BrokerConsultaIndisponivel):
                a.fetch_positions()

    def test_unparseable_quantity_is_refused(self):
        a = adaptador(ExchangeFalsa(saldo={"total": {"BTC": "muito"}}))
        with pytest.raises(BrokerConsultaIndisponivel):
            a.fetch_positions()

    def test_missing_capability_is_refused(self):
        class SemSaldo:
            id = "x"
            urls = {}
        a = adaptador(SemSaldo())
        with pytest.raises(BrokerConsultaIndisponivel, match="não expõe"):
            a.fetch_positions()


class TestConsultaDeOrdem:
    """Três respostas distintas. Confundi-las é o que devolve capital de uma
    ordem viva."""

    def test_direct_lookup_finds_the_order(self):
        class ComBusca(ExchangeFalsa):
            def fetch_order(self, oid, symbol=None):
                return {"id": "ex-1", "clientOrderId": oid, "status": "CLOSED",
                        "filled": 1.5, "average": 100, "fee": {"cost": 0.1},
                        "info": {"tradeId": "t-99"}}

        r = adaptador(ComBusca()).fetch_order_by_client_id("coid-1")
        assert r["resultado"] == "FOUND"
        assert r["ordem"]["status"] == "closed" and r["ordem"]["fill_id"] == "t-99"

    def test_exchange_saying_order_not_found_is_confirmation(self):
        class OrderNotFound(Exception):
            pass

        class Nega(ExchangeFalsa):
            def fetch_order(self, oid, symbol=None):
                raise OrderNotFound("unknown order")

        r = adaptador(Nega()).fetch_order_by_client_id("coid-1")
        assert r["resultado"] == "CONFIRMED_NOT_FOUND"

    def test_network_failure_is_unknown_never_confirmation(self):
        class Cai(ExchangeFalsa):
            def fetch_order(self, oid, symbol=None):
                raise RuntimeError("timeout")

        r = adaptador(Cai()).fetch_order_by_client_id("coid-1")
        assert r["resultado"] == "UNKNOWN"

    def test_order_outside_the_history_window_is_unknown(self):
        """A ordem alvo é a 10ª de 100; a janela consultada traz as últimas.
        Não aparecer NÃO prova que não existe."""
        ordens = [{"id": f"ex-{i}", "clientOrderId": f"coid-{i}",
                   "status": "closed"} for i in range(60, 100)]
        a = adaptador(ExchangeFalsa(ordens=ordens))
        r = a.fetch_order_by_client_id("coid-10")
        assert r["resultado"] == "UNKNOWN", (
            "ordem fora da janela foi tratada como inexistente")
        assert "NÃO prova" in r["motivo"]

    def test_history_scan_can_still_find(self):
        a = adaptador(ExchangeFalsa(ordens=[
            {"id": "ex-2", "info": {"clOrdId": "coid-2"}, "status": "open"}]))
        r = a.fetch_order_by_client_id("coid-2")
        assert r["resultado"] == "FOUND" and r["ordem"]["status"] == "open"


class TestConsultaDeExecucoes:
    """order.filled é CUMULATIVO; os efeitos econômicos vêm dos fills."""

    class ComTrades(ExchangeFalsa):
        def __init__(self, trades, **kw):
            super().__init__(**kw)
            self._trades = trades

        def fetch_my_trades(self, *a, **k):
            if self._erro:
                raise self._erro
            return self._trades

    def test_returns_canonical_fills(self):
        a = adaptador(self.ComTrades([
            {"id": "t-1", "order": "ex-1", "symbol": "BTC/USDT", "side": "buy",
             "amount": 0.3, "price": 100, "fee": {"cost": 0.03, "currency": "USDT"},
             "timestamp": 1}]))
        fills = a.fetch_order_fills("coid-1", "ex-1")
        assert len(fills) == 1
        f = fills[0]
        assert f["fill_id"] == "t-1" and f["quantity"] == Decimal("0.3")
        assert isinstance(f["price"], Decimal) and f["fee_currency"] == "USDT"

    def test_all_fee_components_survive_the_adapter(self):
        """`order.fee` é UM componente; uma taxa dividida em `fees[]` (parte
        na moeda de cotação, parte numa moeda nativa) precisa chegar inteira.
        Antes o adaptador lia apenas `t['fee']` e a moeda nativa sumia — o
        custo reportado ficava menor do que o real."""
        a = adaptador(self.ComTrades([
            {"id": "t-1", "order": "ex-1", "symbol": "BTC/USDT", "side": "buy",
             "amount": 0.3, "price": 100,
             "fees": [{"cost": 0.03, "currency": "USDT"},
                      {"cost": 0.001, "currency": "BNB"}],
             "timestamp": 1}]))
        fill = a.fetch_order_fills("coid-1", "ex-1")[0]
        assert fill["fees"] == [
            {"cost": Decimal("0.03"), "currency": "USDT"},
            {"cost": Decimal("0.001"), "currency": "BNB"},
        ], "a moeda nativa da taxa desapareceu no adaptador"

    def test_fill_without_identifier_is_refused(self):
        a = adaptador(self.ComTrades([
            {"order": "ex-1", "amount": 0.3, "price": 100}]))
        with pytest.raises(BrokerConsultaIndisponivel, match="deduplicar"):
            a.fetch_order_fills("coid-1", "ex-1")

    def test_missing_capability_is_refused(self):
        with pytest.raises(BrokerConsultaIndisponivel):
            adaptador(ExchangeFalsa()).fetch_order_fills("coid-1")


class TestIntegracaoComAReconciliacao:
    """O caminho completo: coordenador → adaptador real → exchange falsa."""

    def test_reconciliation_uses_the_real_adapter(self):
        from tradingagents.desk.execution import ExecutionCoordinator
        from tradingagents.desk.ledger import Ledger
        from tradingagents.desk.models import DeskMandate, Mode
        from tradingagents.desk.risk import RiskEngine
        m = DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                        instruments=("BTC/USDT",), capital=Decimal("1000"))
        a = adaptador(ExchangeFalsa(saldo={"total": {"BTC": 0.5, "USDT": 900}},
                                    ordens=[]))
        coord = ExecutionCoordinator(m, Ledger(opening_cash=m.capital),
                                     RiskEngine(m), worker_id="w", broker=a)
        rel = coord.reconciliar(a.fetch_order_by_client_id,
                                consultar_posicoes=a.fetch_positions)
        # a corretora tem 0.5 BTC que o sistema não conhece
        assert rel["seguro"] is False
        assert any("POSICAO_ORFA_REMOTA" in d for d in rel["divergencias"])

    def test_adapter_failure_keeps_the_gate_closed(self):
        from tradingagents.desk.execution import ExecutionCoordinator
        from tradingagents.desk.ledger import Ledger
        from tradingagents.desk.models import DeskMandate, Mode
        from tradingagents.desk.risk import RiskEngine
        m = DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                        instruments=("BTC/USDT",), capital=Decimal("1000"))
        a = adaptador(ExchangeFalsa(erro=RuntimeError("exchange caiu")))
        coord = ExecutionCoordinator(m, Ledger(opening_cash=m.capital),
                                     RiskEngine(m), worker_id="w", broker=a)
        rel = coord.reconciliar(a.fetch_order_by_client_id,
                                consultar_posicoes=a.fetch_positions)
        assert rel["seguro"] is False and rel["posicoes_status"] == "UNKNOWN"
