from __future__ import annotations

import pytest

from tradingagents.telemetry import MetricsRegistry


def test_metrics_preserve_legacy_shape_and_publish_bounded_histogram():
    metrics = MetricsRegistry(max_labeled_series=4)
    metrics.inc("execution.attempts")
    labels = {"method": "GET", "route": "/readyz", "status_class": "2xx"}
    metrics.inc("http.requests", labels=labels)
    for value in (5, 20, 200):
        metrics.observe("http.request.duration_ms", value, labels=labels, buckets=(10, 100, 500))

    snapshot = metrics.snapshot(detailed=True)
    assert snapshot["schema"] == "dz23.metrics/2"
    assert snapshot["counters"]["execution.attempts"] == 1
    assert snapshot["counter_series"] == [
        {"name": "http.requests", "labels": labels, "value": 1}
    ]
    histogram = snapshot["histogram_series"][0]
    assert histogram["count"] == 3
    assert histogram["sum"] == 225.0
    assert histogram["buckets"] == [
        {"le": 10.0, "count": 1},
        {"le": 100.0, "count": 2},
        {"le": 500.0, "count": 3},
    ]
    assert histogram["p95_upper_bound"] == 500.0


def test_metrics_reject_secret_shaped_or_unbounded_labels():
    metrics = MetricsRegistry()
    with pytest.raises(ValueError, match="valor de rótulo"):
        metrics.inc("http.requests", labels={"authorization": "x" * 129})
    with pytest.raises(ValueError, match="nome de rótulo"):
        metrics.inc("http.requests", labels={"bad-label": "value"})
    with pytest.raises(ValueError, match="observação"):
        metrics.observe("http.request.duration_ms", float("nan"), labels={"route": "/"})


def test_metrics_cap_new_series_instead_of_growing_forever():
    metrics = MetricsRegistry(max_labeled_series=2)
    metrics.inc("http.requests", labels={"route": "/healthz"})
    metrics.inc("http.requests", labels={"route": "/readyz"})
    metrics.inc("http.requests", labels={"route": "/unexpected"})

    snapshot = metrics.snapshot(detailed=True)
    assert len(snapshot["counter_series"]) == 2
    assert snapshot["counters"]["telemetry.series_dropped"] == 1
