from __future__ import annotations

"""Minimal Prometheus-style metrics.

We intentionally avoid external dependencies (e.g. prometheus_client) to keep the
project lightweight and deterministic in constrained environments.

Supported metric types:

* Counter
* Gauge
* Histogram

The API is intentionally small and "good enough" for production dashboards.
"""

from dataclasses import dataclass, field
import math
import threading
import time
from typing import Any, Mapping


def _now() -> float:
    return time.time()


def _sanitize_name(name: str) -> str:
    # Prometheus metric name: [a-zA-Z_:][a-zA-Z0-9_:]*
    raw = str(name or '').strip()
    out = []
    for i, ch in enumerate(raw):
        if ch.isalnum() or ch in ('_', ':'):
            out.append(ch)
        elif ch in ('-', '.', ' '):
            out.append('_')
        else:
            out.append('_')
    if not out:
        return 'metric'
    if not (out[0].isalpha() or out[0] in ('_', ':')):
        out.insert(0, '_')
    return ''.join(out)


def _escape_label_value(value: str) -> str:
    s = str(value)
    return s.replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def _label_key(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


@dataclass
class _Series:
    value: float = 0.0
    updated_at: float = field(default_factory=_now)


class Metric:
    def __init__(self, name: str, *, help: str = '', labelnames: tuple[str, ...] = ()) -> None:
        self.name = _sanitize_name(name)
        self.help = str(help or '')
        self.labelnames = tuple(str(x) for x in (labelnames or ()))
        self._lock = threading.Lock()

    def _validate_labels(self, labels: Mapping[str, str]) -> dict[str, str]:
        out = {str(k): str(v) for k, v in (labels or {}).items()}
        if self.labelnames:
            missing = [k for k in self.labelnames if k not in out]
            if missing:
                raise KeyError(f'missing metric labels: {missing}')
            # ignore unexpected labels to keep runtime stable
            out = {k: out[k] for k in self.labelnames}
        return out


class Counter(Metric):
    """Monotonic counter."""

    def __init__(self, name: str, *, help: str = '', labelnames: tuple[str, ...] = ()) -> None:
        super().__init__(name, help=help, labelnames=labelnames)
        self._series: dict[tuple[tuple[str, str], ...], _Series] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        amt = float(amount)
        if math.isnan(amt) or math.isinf(amt):
            return
        if amt < 0:
            return
        lab = self._validate_labels(labels)
        key = _label_key(lab)
        with self._lock:
            s = self._series.get(key)
            if s is None:
                s = _Series(value=0.0)
                self._series[key] = s
            s.value += amt
            s.updated_at = _now()

    def collect(self) -> list[tuple[dict[str, str], float]]:
        with self._lock:
            out: list[tuple[dict[str, str], float]] = []
            for key, series in self._series.items():
                out.append(({k: v for k, v in key}, float(series.value)))
            return out


class Gauge(Metric):
    """Instantaneous value."""

    def __init__(self, name: str, *, help: str = '', labelnames: tuple[str, ...] = ()) -> None:
        super().__init__(name, help=help, labelnames=labelnames)
        self._series: dict[tuple[tuple[str, str], ...], _Series] = {}

    def set(self, value: float, **labels: str) -> None:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return
        lab = self._validate_labels(labels)
        key = _label_key(lab)
        with self._lock:
            s = self._series.get(key)
            if s is None:
                s = _Series(value=0.0)
                self._series[key] = s
            s.value = v
            s.updated_at = _now()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        v = float(amount)
        if math.isnan(v) or math.isinf(v):
            return
        lab = self._validate_labels(labels)
        key = _label_key(lab)
        with self._lock:
            s = self._series.get(key)
            if s is None:
                s = _Series(value=0.0)
                self._series[key] = s
            s.value += v
            s.updated_at = _now()

    def collect(self) -> list[tuple[dict[str, str], float]]:
        with self._lock:
            out: list[tuple[dict[str, str], float]] = []
            for key, series in self._series.items():
                out.append(({k: v for k, v in key}, float(series.value)))
            return out


@dataclass
class _HistogramSeries:
    buckets: list[float]
    counts: list[int]
    sum: float = 0.0
    count: int = 0
    updated_at: float = field(default_factory=_now)


class Histogram(Metric):
    def __init__(
        self,
        name: str,
        *,
        help: str = '',
        labelnames: tuple[str, ...] = (),
        buckets: list[float] | None = None,
    ) -> None:
        super().__init__(name, help=help, labelnames=labelnames)
        if buckets is None:
            buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        b = sorted(set(float(x) for x in buckets if x is not None and float(x) > 0.0))
        self._buckets = b
        self._series: dict[tuple[tuple[str, str], ...], _HistogramSeries] = {}

    def observe(self, value: float, **labels: str) -> None:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return
        if v < 0:
            v = 0.0
        lab = self._validate_labels(labels)
        key = _label_key(lab)
        with self._lock:
            s = self._series.get(key)
            if s is None:
                s = _HistogramSeries(buckets=list(self._buckets), counts=[0 for _ in self._buckets])
                self._series[key] = s
            s.sum += v
            s.count += 1
            for i, le in enumerate(s.buckets):
                if v <= le:
                    s.counts[i] += 1
            s.updated_at = _now()

    def collect(self) -> list[tuple[dict[str, str], dict[str, Any]]]:
        with self._lock:
            out: list[tuple[dict[str, str], dict[str, Any]]] = []
            for key, series in self._series.items():
                out.append(
                    (
                        {k: v for k, v in key},
                        {
                            'buckets': list(series.buckets),
                            'counts': list(series.counts),
                            'sum': float(series.sum),
                            'count': int(series.count),
                        },
                    )
                )
            return out


class MetricRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[str, Metric] = {}

    def get(self, name: str) -> Metric | None:
        return self._metrics.get(_sanitize_name(name))

    def register(self, metric: Metric) -> Metric:
        with self._lock:
            name = metric.name
            existing = self._metrics.get(name)
            if existing is not None and type(existing) is not type(metric):
                raise TypeError(f'metric name already registered with different type: {name}')
            self._metrics[name] = metric
        return metric

    def counter(self, name: str, *, help: str = '', labelnames: tuple[str, ...] = ()) -> Counter:
        name2 = _sanitize_name(name)
        existing = self.get(name2)
        if isinstance(existing, Counter):
            return existing
        c = Counter(name2, help=help, labelnames=labelnames)
        self.register(c)
        return c

    def gauge(self, name: str, *, help: str = '', labelnames: tuple[str, ...] = ()) -> Gauge:
        name2 = _sanitize_name(name)
        existing = self.get(name2)
        if isinstance(existing, Gauge):
            return existing
        g = Gauge(name2, help=help, labelnames=labelnames)
        self.register(g)
        return g

    def histogram(
        self,
        name: str,
        *,
        help: str = '',
        labelnames: tuple[str, ...] = (),
        buckets: list[float] | None = None,
    ) -> Histogram:
        name2 = _sanitize_name(name)
        existing = self.get(name2)
        if isinstance(existing, Histogram):
            return existing
        h = Histogram(name2, help=help, labelnames=labelnames, buckets=buckets)
        self.register(h)
        return h

    def all_metrics(self) -> list[Metric]:
        with self._lock:
            return list(self._metrics.values())


REGISTRY = MetricRegistry()


def _format_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ''
    parts = []
    for k, v in labels.items():
        parts.append(f'{k}="{_escape_label_value(v)}"')
    return '{' + ','.join(parts) + '}'


def render_prometheus_text(registry: MetricRegistry | None = None) -> str:
    reg = registry or REGISTRY
    lines: list[str] = []
    for m in sorted(reg.all_metrics(), key=lambda x: x.name):
        if isinstance(m, Counter):
            if m.help:
                lines.append(f'# HELP {m.name} {m.help}')
            lines.append(f'# TYPE {m.name} counter')
            for labels, value in m.collect():
                lines.append(f'{m.name}{_format_labels(labels)} {value}')
        elif isinstance(m, Gauge):
            if m.help:
                lines.append(f'# HELP {m.name} {m.help}')
            lines.append(f'# TYPE {m.name} gauge')
            for labels, value in m.collect():
                lines.append(f'{m.name}{_format_labels(labels)} {value}')
        elif isinstance(m, Histogram):
            base = m.name
            if m.help:
                lines.append(f'# HELP {base} {m.help}')
            lines.append(f'# TYPE {base} histogram')
            for labels, data in m.collect():
                buckets = list(data.get('buckets') or [])
                counts = list(data.get('counts') or [])
                running = 0
                for le, c in zip(buckets, counts):
                    running += int(c)
                    lab2 = dict(labels)
                    lab2['le'] = str(le)
                    lines.append(f'{base}_bucket{_format_labels(lab2)} {running}')
                # +Inf bucket
                lab_inf = dict(labels)
                lab_inf['le'] = '+Inf'
                lines.append(f'{base}_bucket{_format_labels(lab_inf)} {int(data.get("count") or 0)}')
                lines.append(f'{base}_sum{_format_labels(labels)} {float(data.get("sum") or 0.0)}')
                lines.append(f'{base}_count{_format_labels(labels)} {int(data.get("count") or 0)}')
        else:
            # unknown metric type - ignore
            continue
    # Prometheus expects trailing newline.
    return '\n'.join(lines) + ('\n' if lines else '')
