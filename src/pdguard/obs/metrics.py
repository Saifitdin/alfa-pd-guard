"""Метрики Prometheus: Latency, RPS и TPS (tokens per second).

RPS и TPS считаются счётчиками — мгновенные значения получаются как
rate(...) на стороне Prometheus/Grafana. Для быстрого взгляда без Prometheus
есть сводка в GET /stats.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry(auto_describe=True)

REQUESTS = Counter(
    "pdguard_requests_total",
    "Обработанные запросы",
    ("system", "direction", "status"),
    registry=REGISTRY,
)

LATENCY = Histogram(
    "pdguard_latency_seconds",
    "Время обработки запроса",
    ("direction",),
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

TOKENS = Counter(
    "pdguard_tokens_total",
    "Обработано токенов (оценка) — основа для TPS",
    ("direction",),
    registry=REGISTRY,
)

ENTITIES = Counter(
    "pdguard_pd_entities_total",
    "Найденные сущности ПД по типам",
    ("type",),
    registry=REGISTRY,
)

REJECTED = Counter(
    "pdguard_rejected_total",
    "Отклонённые запросы",
    ("reason",),
    registry=REGISTRY,
)

STORE_SIZE = Gauge(
    "pdguard_store_items",
    "Записей в хранилище соответствий",
    registry=REGISTRY,
)

INFLIGHT = Gauge(
    "pdguard_inflight_requests",
    "Запросов в обработке прямо сейчас",
    registry=REGISTRY,
)


class RollingStats:
    """Скользящее окно для человекочитаемой сводки в /stats."""

    def __init__(self, window_seconds: float = 60.0, max_samples: int = 200_000) -> None:
        self._window = window_seconds
        self._samples: deque[tuple[float, float, int]] = deque(maxlen=max_samples)
        self._lock = Lock()

    def observe(self, latency: float, tokens: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, latency, tokens))

    def snapshot(self) -> dict[str, float]:
        cutoff = time.monotonic() - self._window
        with self._lock:
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()
            samples = list(self._samples)

        if not samples:
            return {"rps": 0.0, "tps": 0.0, "latency_avg_ms": 0.0, "latency_p95_ms": 0.0,
                    "latency_p99_ms": 0.0, "requests_in_window": 0}

        latencies = sorted(sample[1] for sample in samples)
        total_tokens = sum(sample[2] for sample in samples)
        span = max(samples[-1][0] - samples[0][0], 1e-6)

        def percentile(fraction: float) -> float:
            index = min(int(len(latencies) * fraction), len(latencies) - 1)
            return latencies[index] * 1000

        return {
            "rps": round(len(samples) / span, 1),
            "tps": round(total_tokens / span, 1),
            "latency_avg_ms": round(sum(latencies) / len(latencies) * 1000, 3),
            "latency_p95_ms": round(percentile(0.95), 3),
            "latency_p99_ms": round(percentile(0.99), 3),
            "requests_in_window": len(samples),
        }


ROLLING = RollingStats()


def render_prometheus() -> bytes:
    return generate_latest(REGISTRY)
