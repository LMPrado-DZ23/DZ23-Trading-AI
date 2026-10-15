"""V9-SEC-08 — gates do MT5Adapter: nenhum order_send contra conta REAL.

Contraprova do achado: com terminal fake, um adaptador REAL (modo LIVE ou
conta de terminal REAL) deve produzir ZERO order_send — inclusive no
cancelamento — e o modo DEMO deve confrontar a conta do terminal antes de
qualquer operação que envie ordem.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from tradingagents.domain import Environment, OrderRequest, Side
from tradingagents.mt5 import MT5Adapter


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008

    def __init__(self, *, trade_mode=0):
        self.trade_mode = trade_mode
        self.order_send_calls: list[dict] = []
        self.order_check_calls: list[dict] = []

    def initialize(self, **kwargs):
        return True

    def shutdown(self):
        pass

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(ask=100.0, bid=99.9)

    def order_check(self, payload):
        self.order_check_calls.append(payload)
        return SimpleNamespace(retcode=10009, comment="ok")

    def order_send(self, payload):
        self.order_send_calls.append(payload)
        return SimpleNamespace(retcode=10009, order=123, deal=456, comment="done")

    def positions_get(self):
        return []

    def orders_get(self):
        return []

    def account_info(self):
        return SimpleNamespace(
            equity=10000, balance=10000, margin=0, margin_free=10000,
            trade_mode=self.trade_mode,
        )


def _adapter(mode=Environment.DEMO, *, trade_mode=0):
    fake = FakeMT5(trade_mode=trade_mode)
    adapter = MT5Adapter(mode=mode, terminal=fake)
    assert adapter.initialize()
    return adapter, fake


def _request(environment=Environment.DEMO):
    return OrderRequest(
        "coid", "dec", "run", "strategy", "account", "EURUSD", "mt5",
        Side.BUY, "MARKET", Decimal("1"), Decimal("100"), Decimal("99"),
        Decimal("102"), environment=environment,
    )


def test_sec08_cancel_live_bloqueado_sem_order_send():
    adapter, fake = _adapter(Environment.LIVE)
    with pytest.raises(PermissionError):
        adapter.cancel("123")
    assert fake.order_send_calls == []


def test_sec08_ordem_live_nem_e_construida_no_contrato():
    with pytest.raises(ValueError):
        _request(Environment.LIVE)


def test_sec08_submit_live_bloqueado_sem_order_send():
    adapter, fake = _adapter(Environment.LIVE)
    with pytest.raises(PermissionError):
        adapter.submit(_request(Environment.DEMO), risk_approved=True)
    assert fake.order_send_calls == []


def test_sec08_demo_com_conta_real_submit_recusado_sem_order_send():
    adapter, fake = _adapter(Environment.DEMO, trade_mode=2)
    with pytest.raises(PermissionError):
        adapter.submit(_request(), risk_approved=True)
    assert fake.order_send_calls == []
    assert fake.order_check_calls == []


def test_sec08_demo_com_conta_real_cancel_recusado_sem_order_send():
    adapter, fake = _adapter(Environment.DEMO, trade_mode=2)
    with pytest.raises(PermissionError):
        adapter.cancel("123")
    assert fake.order_send_calls == []


def test_sec08_conta_sem_modo_declarado_fail_closed():
    fake = FakeMT5()
    fake.account_info = lambda: SimpleNamespace(
        equity=1, balance=1, margin=0, margin_free=1
    )
    adapter = MT5Adapter(mode=Environment.DEMO, terminal=fake)
    assert adapter.initialize()
    with pytest.raises(PermissionError):
        adapter.cancel("123")
    assert fake.order_send_calls == []


def test_sec08_demo_com_conta_demo_continua_enviando():
    adapter, fake = _adapter(Environment.DEMO, trade_mode=0)
    ack = adapter.submit(_request(), risk_approved=True)
    assert ack.status.value == "ACCEPTED"
    assert len(fake.order_send_calls) == 1


def test_sec08_demo_com_conta_demo_continua_cancelando():
    adapter, fake = _adapter(Environment.DEMO, trade_mode=0)
    ack = adapter.cancel("123")
    assert ack.status.value == "CANCELLED"
    assert len(fake.order_send_calls) == 1
    assert fake.order_send_calls[0]["action"] == 8
