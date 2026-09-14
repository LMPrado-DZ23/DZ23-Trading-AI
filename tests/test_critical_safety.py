import pytest

from tradingagents.execution import KillSwitch


def test_kill_switch_persists_engaged_state(tmp_path):
    path = tmp_path / "kill.json"
    first = KillSwitch(path)
    first.engage("reconciliation diverged", actor="monitor")
    second = KillSwitch(path)
    with pytest.raises(PermissionError):
        second.assert_allowed()


def test_kill_switch_reset_requires_server_result(tmp_path):
    switch = KillSwitch(tmp_path / "kill.json")
    switch.engage("test")
    with pytest.raises(PermissionError):
        switch.reset(actor="operator", reconciliation_ok=False)


def test_live_ccxt_mode_is_rejected_without_network():
    from tradingagents.brokers.ccxt_bridge import BrokerSecurityError, CCXTBridge
    with pytest.raises(BrokerSecurityError):
        CCXTBridge(env={"TRADINGAGENTS_EXCHANGE": "binance", "TRADINGAGENTS_BROKER_MODE": "live"})
