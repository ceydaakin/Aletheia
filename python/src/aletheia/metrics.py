"""A minimal Prometheus-compatible metrics registry.

Mirrors ``gateway/internal/obs/metrics.go`` so that both halves of the system
expose the same shapes, and keeps the services free of a client library they
would use three functions from.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, str], int] = {}
        self._hists: dict[tuple[str, str], list[float]] = {}

    def inc(self, name: str, value: int = 1, **labels: str) -> None:
        key = (name, _labels(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        key = (name, _labels(labels))
        with self._lock:
            self._hists.setdefault(key, []).append(seconds)

    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            hists = {k: list(v) for k, v in self._hists.items()}

        lines: list[str] = []
        for (name, labels), value in sorted(counters.items()):
            lines.append(f"{name}{labels} {value}")
        for (name, labels), values in sorted(hists.items()):
            for bucket in BUCKETS:
                count = sum(1 for v in values if v <= bucket)
                lines.append(f"{name}_bucket{_with(labels, 'le', repr(bucket))} {count}")
            lines.append(f"{name}_bucket{_with(labels, 'le', '+Inf')} {len(values)}")
            lines.append(f"{name}_sum{labels} {sum(values)}")
            lines.append(f"{name}_count{labels} {len(values)}")
        return "\n".join(lines) + "\n"


def _labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    pairs: Iterable[str] = (f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + ",".join(pairs) + "}"


def _with(labels: str, key: str, value: str) -> str:
    pair = f'{key}="{value}"'
    return "{" + pair + "}" if not labels else labels[:-1] + "," + pair + "}"


registry = Registry()
