from tradingagents.execution.reconciliation import reconcile_state


def test_reconcile_state_matches_normalized_snapshots():
    local = {"balance": "100", "orders": [{"id": "o1", "qty": "1"}], "fills": [], "positions": []}
    remote = {"balance": "100.000000001", "orders": [{"id": "o1", "qty": "1.0"}], "fills": [], "positions": []}
    assert reconcile_state(local, remote).is_safe


def test_reconcile_state_blocks_divergence():
    local = {"balance": "100", "orders": [{"id": "o1", "qty": "1"}], "fills": [], "positions": []}
    remote = {"balance": "90", "orders": [{"id": "o1", "qty": "2"}], "fills": [], "positions": []}
    report = reconcile_state(local, remote)
    assert not report.is_safe
    assert "account:balance" in report.mismatches
    assert "orders:o1:qty" in report.mismatches
