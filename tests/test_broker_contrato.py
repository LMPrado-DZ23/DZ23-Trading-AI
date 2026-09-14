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
    def test_finds_by_client_order_id(self):
        a = adaptador(ExchangeFalsa(ordens=[
            {"id": "ex-1", "clientOrderId": "coid-1", "status": "CLOSED",
             "filled": 1.5, "average": 100, "fee": {"cost": 0.1},
             "info": {"tradeId": "t-99"}}]))
        o = a.fetch_order_by_client_id("coid-1")
        assert o["status"] == "closed" and o["filled"] == 1.5
        assert o["fill_id"] == "t-99", "o id do trade precisa vir do payload"

    def test_finds_by_okx_style_client_id(self):
        a = adaptador(ExchangeFalsa(ordens=[
            {"id": "ex-2", "info": {"clOrdId": "coid-2"}, "status": "open"}]))
        assert a.fetch_order_by_client_id("coid-2")["status"] == "open"

    def test_unknown_order_returns_none(self):
        a = adaptador(ExchangeFalsa(ordens=[]))
        assert a.fetch_order_by_client_id("coid-x") is None

    def test_error_is_unknown_not_none(self):
        """None significa 'a corretora não conhece a ordem' e DEVOLVE o
        capital. Uma falha de rede jamais pode ser lida assim."""
        a = adaptador(ExchangeFalsa(erro=RuntimeError("500")))
        with pytest.raises(BrokerConsultaIndisponivel):
            a.fetch_order_by_client_id("coid-1")


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
