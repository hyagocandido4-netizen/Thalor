"""Telemetry utilities (Package P).

This package provides:

* A tiny Prometheus-style metrics registry (no external dependency)
* An optional HTTP server exposing /metrics and Kubernetes-style health probes

The design goal is to keep runtime code dependency-light while still allowing
production-grade introspection.
"""

from .metrics import REGISTRY, Counter, Gauge, Histogram, render_prometheus_text
from .server import TelemetryServer, TelemetryState

__all__ = [
    'REGISTRY',
    'Counter',
    'Gauge',
    'Histogram',
    'render_prometheus_text',
    'TelemetryServer',
    'TelemetryState',
]
