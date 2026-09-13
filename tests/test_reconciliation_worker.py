from tradingagents.reconciliation_worker import ReconciliationMonitor


def test_monitor_runs_and_reports_safe_state():
    seen = []
    monitor = ReconciliationMonitor(lambda: {"balance": "100", "orders": [], "fills": [], "positions": []}, lambda: {"balance": "100", "orders": [], "fills": [], "positions": []}, seen.append)
    report = monitor.run_once()
    assert report.is_safe
    assert seen == [report]
    assert monitor.health()["safe"] is True


def test_monitor_fails_closed_on_provider_error():
    monitor = ReconciliationMonitor(lambda: {}, lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    report = monitor.run_once()
    assert report.status == "UNKNOWN"
    assert not report.is_safe
