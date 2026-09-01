"""Small, dependency-free runtime metrics for API and pipeline diagnostics."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import math
import threading
import time
from typing import Any, Iterator


DEFAULT_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
    2.5, 5.0, 10.0, 30.0, 60.0, 120.0,
)


def _labels(values: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in values.items()))


def _format_labels(values: tuple[tuple[str, str], ...]) -> str:
    if not values:
        return ""
    encoded = []
    for key, value in values:
        safe = value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        encoded.append(f'{key}="{safe}"')
    return "{" + ",".join(encoded) + "}"


class MetricsRegistry:
    """Thread-safe counters, gauges, and fixed-bucket histograms."""

    def __init__(self, *, buckets: tuple[float, ...] = DEFAULT_BUCKETS) -> None:
        self.buckets = tuple(sorted(float(value) for value in buckets))
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]
        ] = {}

    def increment(self, name: str, amount: float = 1, **labels: str) -> None:
        with self._lock:
            self._counters[(name, _labels(labels))] += amount

    def gauge_add(self, name: str, amount: float, **labels: str) -> None:
        with self._lock:
            self._gauges[(name, _labels(labels))] += amount

    def observe(self, name: str, value: float, **labels: str) -> None:
        if not math.isfinite(value) or value < 0:
            return
        key = (name, _labels(labels))
        with self._lock:
            histogram = self._histograms.setdefault(
                key,
                {"count": 0, "sum": 0.0, "buckets": [0] * len(self.buckets)},
            )
            histogram["count"] += 1
            histogram["sum"] += value
            for index, upper_bound in enumerate(self.buckets):
                if value <= upper_bound:
                    histogram["buckets"][index] += 1

    @contextmanager
    def stage(self, name: str, **labels: str) -> Iterator[None]:
        started = time.perf_counter()
        status = "ok"
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            self.observe(
                "chunk_studio_stage_duration_seconds",
                time.perf_counter() - started,
                stage=name,
                status=status,
                **labels,
            )
            self.increment(
                "chunk_studio_stage_total",
                stage=name,
                status=status,
                **labels,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._counters.items())
            ]
            gauges = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._gauges.items())
            ]
            histograms = []
            for (name, labels), value in sorted(self._histograms.items()):
                histograms.append({
                    "name": name,
                    "labels": dict(labels),
                    "count": value["count"],
                    "sum": value["sum"],
                    "buckets": dict(zip(self.buckets, value["buckets"], strict=True)),
                })
        return {"counters": counters, "gauges": gauges, "histograms": histograms}

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"{name}{_format_labels(labels)} {value:g}")
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(f"{name}{_format_labels(labels)} {value:g}")
            for (name, labels), value in sorted(self._histograms.items()):
                for upper_bound, count in zip(self.buckets, value["buckets"], strict=True):
                    bucket_labels = tuple(sorted((*labels, ("le", f"{upper_bound:g}"))))
                    lines.append(f"{name}_bucket{_format_labels(bucket_labels)} {count}")
                infinity_labels = tuple(sorted((*labels, ("le", "+Inf"))))
                lines.append(
                    f"{name}_bucket{_format_labels(infinity_labels)} {value['count']}"
                )
                lines.append(f"{name}_sum{_format_labels(labels)} {value['sum']:g}")
                lines.append(f"{name}_count{_format_labels(labels)} {value['count']}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Clear process-local metrics. Intended for isolated tests only."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


metrics = MetricsRegistry()
