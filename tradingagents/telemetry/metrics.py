from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from threading import Lock

_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,95}$")
_LABEL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_DEFAULT_BUCKETS_MS = (10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0)


class MetricsRegistry:
    """Registro em memória, thread-safe e de cardinalidade limitada.

    Contadores e gauges sem rótulos mantêm o contrato histórico. Séries
    rotuladas rejeitam nomes/valores ilimitados, para que uma URL, conta ou
    símbolo nunca transforme telemetria em vazamento ou uso de memória sem fim.
    """

    def __init__(self, *, max_labeled_series: int = 512) -> None:
        if max_labeled_series < 1:
            raise ValueError("max_labeled_series deve ser positivo")
        self._lock = Lock()
        self._counters: Counter[str] = Counter()
        self._gauges: dict[str, float] = {}
        self._labeled_counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], dict[str, object]
        ] = {}
        self._max_labeled_series = max_labeled_series

    @staticmethod
    def _metric_name(name: str) -> str:
        if not isinstance(name, str) or not _METRIC_NAME.fullmatch(name):
            raise ValueError("nome de métrica inválido")
        return name

    @staticmethod
    def _labels(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
        if not labels:
            return ()
        if len(labels) > 8:
            raise ValueError("máximo de 8 rótulos por série")
        normalized: list[tuple[str, str]] = []
        for raw_name, raw_value in labels.items():
            name = str(raw_name)
            value = str(raw_value)
            if not _LABEL_NAME.fullmatch(name):
                raise ValueError("nome de rótulo inválido")
            if not value.isascii() or not 1 <= len(value) <= 128:
                raise ValueError("valor de rótulo inválido")
            normalized.append((name, value))
        return tuple(sorted(normalized))

    def _has_capacity(self, key: tuple[str, tuple[tuple[str, str], ...]]) -> bool:
        known = set(self._labeled_counters) | set(self._histograms)
        return key in known or len(known) < self._max_labeled_series

    def inc(
        self,
        name: str,
        amount: int = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        metric_name = self._metric_name(name)
        if not isinstance(amount, int) or amount < 0:
            raise ValueError("incremento deve ser inteiro não negativo")
        metric_labels = self._labels(labels)
        with self._lock:
            if not metric_labels:
                self._counters[metric_name] += amount
                return
            key = (metric_name, metric_labels)
            if self._has_capacity(key):
                self._labeled_counters[key] += amount
            else:
                self._counters["telemetry.series_dropped"] += 1

    def set_gauge(self, name: str, value: float) -> None:
        metric_name = self._metric_name(name)
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("gauge deve ser finito")
        with self._lock:
            self._gauges[metric_name] = numeric

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str],
        buckets: tuple[float, ...] = _DEFAULT_BUCKETS_MS,
    ) -> None:
        metric_name = self._metric_name(name)
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError("observação deve ser finita e não negativa")
        metric_labels = self._labels(labels)
        if not metric_labels:
            raise ValueError("histograma requer rótulos limitados")
        limits = tuple(float(limit) for limit in buckets)
        if not limits or any(not math.isfinite(limit) or limit <= 0 for limit in limits):
            raise ValueError("buckets devem ser positivos e finitos")
        if tuple(sorted(set(limits))) != limits:
            raise ValueError("buckets devem ser únicos e crescentes")

        key = (metric_name, metric_labels)
        with self._lock:
            if not self._has_capacity(key):
                self._counters["telemetry.series_dropped"] += 1
                return
            series = self._histograms.get(key)
            if series is None:
                series = {
                    "buckets": limits,
                    "bucket_counts": [0] * len(limits),
                    "count": 0,
                    "sum": 0.0,
                    "max": 0.0,
                }
                self._histograms[key] = series
            elif series["buckets"] != limits:
                raise ValueError("buckets não podem mudar para uma série existente")
            series["count"] = int(series["count"]) + 1
            series["sum"] = float(series["sum"]) + numeric
            series["max"] = max(float(series["max"]), numeric)
            counts = series["bucket_counts"]
            assert isinstance(counts, list)
            for index, limit in enumerate(limits):
                if numeric <= limit:
                    counts[index] += 1

    @staticmethod
    def _quantile_upper_bound(
        count: int, limits: tuple[float, ...], counts: list[int], quantile: float
    ) -> float | None:
        if count == 0:
            return None
        target = max(1, math.ceil(count * quantile))
        for limit, bucket_count in zip(limits, counts, strict=True):
            if bucket_count >= target:
                return limit
        return None

    def snapshot(self, *, detailed: bool = False) -> dict:
        with self._lock:
            legacy = {"counters": dict(self._counters), "gauges": dict(self._gauges)}
            if not detailed:
                return legacy
            counter_series = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._labeled_counters.items())
            ]
            histogram_series = []
            for (name, labels), raw in sorted(self._histograms.items()):
                limits = raw["buckets"]
                counts = raw["bucket_counts"]
                assert isinstance(limits, tuple) and isinstance(counts, list)
                count = int(raw["count"])
                histogram_series.append(
                    {
                        "name": name,
                        "labels": dict(labels),
                        "count": count,
                        "sum": round(float(raw["sum"]), 3),
                        "max": round(float(raw["max"]), 3),
                        "buckets": [
                            {"le": limit, "count": bucket_count}
                            for limit, bucket_count in zip(limits, counts, strict=True)
                        ],
                        "p50_upper_bound": self._quantile_upper_bound(count, limits, counts, 0.50),
                        "p95_upper_bound": self._quantile_upper_bound(count, limits, counts, 0.95),
                        "p99_upper_bound": self._quantile_upper_bound(count, limits, counts, 0.99),
                    }
                )
            return {
                "schema": "dz23.metrics/2",
                **legacy,
                "counter_series": counter_series,
                "histogram_series": histogram_series,
                "limits": {"max_labeled_series": self._max_labeled_series},
            }
