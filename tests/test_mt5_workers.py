from decimal import Decimal
from time import sleep
from types import SimpleNamespace

from tradingagents.domain import Environment, OrderRequest, Side
from tradingagents.mt5 import MT5Adapter
from tradingagents.workers import WorkerSupervisor


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008

    def initialize(self, **kwargs):
        return True

    def shutdown(self):
        pass

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(ask=100.0, bid=99.9)

    def order_check(self, payload):
        return SimpleNamespace(retcode=10009, comment="ok")

    def order_send(self, payload):
        return SimpleNamespace(retcode=10009, order=123, deal=456, comment="done")

    def positions_get(self):
        return []

    def orders_get(self):
        return []

    def account_info(self):
        return SimpleNamespace(equity=10000, balance=10000, margin=0, margin_free=10000, trade_mode=0)


def test_mt5_demo_checks_and_sends_order():
    adapter = MT5Adapter(mode=Environment.DEMO, terminal=FakeMT5())
    assert adapter.initialize()
    request = OrderRequest("coid", "dec", "run", "strategy", "account", "EURUSD", "mt5", Side.BUY, "MARKET", Decimal("1"), Decimal("100"), Decimal("99"), Decimal("102"), environment=Environment.DEMO)
    ack = adapter.submit(request, risk_approved=True)
    assert ack.status.value == "ACCEPTED"


def test_worker_recovers_after_failure():
    supervisor = WorkerSupervisor()
    calls = []

    def task():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary")

    supervisor.add("market-data", task, interval_seconds=0.01)
    supervisor.start()
    sleep(0.08)
    supervisor.stop()
    assert len(calls) >= 2
    assert supervisor.health()["market-data"]["status"] == "STOPPED"
