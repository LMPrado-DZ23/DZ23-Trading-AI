from tradingagents.telemetry import MetricsRegistry


def test_metrics_registry_counts_and_gauges():
    metrics = MetricsRegistry()
    metrics.inc("orders", 2)
    metrics.set_gauge("health", 1)
    assert metrics.snapshot() == {"counters": {"orders": 2}, "gauges": {"health": 1}}
