import pytest

from tradingagents.execution import KillSwitch


def test_kill_switch_blocks_until_consistent_reconciliation():
    switch = KillSwitch()
    switch.engage("reconciliação divergente", actor="monitor")
    with pytest.raises(PermissionError):
        switch.assert_allowed()
    with pytest.raises(PermissionError):
        switch.reset(actor="operator", reconciliation_ok=False)
    switch.reset(actor="operator", reconciliation_ok=True)
    switch.assert_allowed()
