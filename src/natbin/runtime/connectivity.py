from __future__ import annotations

"""Runtime connectivity wiring helpers.

This module centralizes the translation from resolved Thalor configuration into
runtime-ready connectivity collaborators:

* :class:`natbin.utils.network_transport.NetworkTransportManager`
* :class:`natbin.utils.request_metrics.RequestMetrics`

The helpers keep path resolution and environment overlay handling in one place
so broker adapters and runtimes stay thin and dependency-injected.
"""

from datetime import UTC, datetime
import logging
from pathlib import Path
from typing import Any, Mapping

from ..utils import (
    NetworkTransportConfig,
    NetworkTransportManager,
    RequestMetrics,
    RequestMetricsConfig,
)

__all__ = [
    'build_runtime_connectivity_payload',
    'build_runtime_network_transport_config',
    'build_runtime_network_transport_manager',
    'build_runtime_request_metrics',
    'build_runtime_request_metrics_config',
    'request_metrics_settings_from_resolved',
    'transport_settings_from_resolved',
]


def _to_mapping(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, 'model_dump'):
        try:
            return raw.model_dump(mode='python')
        except Exception:
            pass
    try:
        return dict(raw)
    except Exception:
        return {}



def _resolve_repo_path(repo_root: Path, raw: Any) -> str | None:
    if raw in (None, ''):
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = repo_root / path
    return str(path)



def _resolved_config_mapping(resolved_config: Any) -> dict[str, Any]:
    return _to_mapping(resolved_config)



def transport_settings_from_resolved(resolved_config: Any) -> dict[str, Any]:
    cfg = _resolved_config_mapping(resolved_config)
    network = _to_mapping(cfg.get('network'))
    return _to_mapping(network.get('transport'))



def request_metrics_settings_from_resolved(resolved_config: Any) -> dict[str, Any]:
    cfg = _resolved_config_mapping(resolved_config)
    observability = _to_mapping(cfg.get('observability'))
    return _to_mapping(observability.get('request_metrics'))



def build_runtime_network_transport_config(
    *,
    resolved_config: Any,
    repo_root: str | Path = '.',
) -> NetworkTransportConfig:
    repo = Path(repo_root).resolve()
    payload = dict(transport_settings_from_resolved(resolved_config))

    endpoint_file = _resolve_repo_path(repo, payload.get('endpoint_file'))
    if endpoint_file is not None:
        payload['endpoint_file'] = endpoint_file
    endpoints_file = _resolve_repo_path(repo, payload.get('endpoints_file'))
    if endpoints_file is not None:
        payload['endpoints_file'] = endpoints_file
    structured_log_path = _resolve_repo_path(repo, payload.get('structured_log_path'))
    if structured_log_path is not None:
        payload['structured_log_path'] = structured_log_path
    elif bool(payload.get('enabled')):
        payload['structured_log_path'] = str(repo / 'runs' / 'logs' / 'network_transport.jsonl')

    return NetworkTransportConfig.from_sources(payload)



def build_runtime_network_transport_manager(
    *,
    resolved_config: Any,
    repo_root: str | Path = '.',
    logger: logging.Logger | None = None,
) -> NetworkTransportManager:
    config = build_runtime_network_transport_config(resolved_config=resolved_config, repo_root=repo_root)
    return NetworkTransportManager(config, logger=logger or logging.getLogger('natbin.runtime.connectivity.transport'))



def build_runtime_request_metrics_config(
    *,
    resolved_config: Any,
    repo_root: str | Path = '.',
) -> RequestMetricsConfig:
    repo = Path(repo_root).resolve()
    payload = dict(request_metrics_settings_from_resolved(resolved_config))
    cfg = _resolved_config_mapping(resolved_config)
    if payload.get('timezone') in (None, ''):
        payload['timezone'] = cfg.get('timezone') or 'UTC'

    structured_log_path = _resolve_repo_path(repo, payload.get('structured_log_path'))
    if structured_log_path is not None:
        payload['structured_log_path'] = structured_log_path
    elif bool(payload.get('enabled', True)):
        payload['structured_log_path'] = str(repo / 'runs' / 'logs' / 'request_metrics.jsonl')

    return RequestMetricsConfig.from_sources(payload)



def build_runtime_request_metrics(
    *,
    resolved_config: Any,
    repo_root: str | Path = '.',
    logger: logging.Logger | None = None,
) -> RequestMetrics:
    config = build_runtime_request_metrics_config(resolved_config=resolved_config, repo_root=repo_root)
    return RequestMetrics.from_config(config, logger=logger or logging.getLogger('natbin.runtime.connectivity.request_metrics'))



def build_runtime_connectivity_payload(
    *,
    resolved_config: Any,
    repo_root: str | Path = '.',
) -> dict[str, Any]:
    transport_manager = build_runtime_network_transport_manager(resolved_config=resolved_config, repo_root=repo_root)
    request_metrics_config = build_runtime_request_metrics_config(resolved_config=resolved_config, repo_root=repo_root)
    transport_dependency = transport_manager.dependency_status()
    cfg = _resolved_config_mapping(resolved_config)
    return {
        'kind': 'runtime_connectivity',
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'repo_root': str(Path(repo_root).resolve()),
        'asset': cfg.get('asset'),
        'interval_sec': cfg.get('interval_sec'),
        'timezone': cfg.get('timezone'),
        'source_trace': list(cfg.get('source_trace') or []),
        'transport_enabled': bool(transport_manager.enabled),
        'transport_ready': bool(transport_manager.ready),
        'transport_dependency_available': bool(transport_dependency.get('available', True)),
        'transport_requires_pysocks': bool(transport_dependency.get('requires_pysocks', False)),
        'transport_dependency_reason': transport_dependency.get('reason'),
        'request_metrics_enabled': bool(request_metrics_config.enabled),
        'transport': transport_manager.snapshot(),
        'request_metrics': request_metrics_config.as_dict(),
    }
