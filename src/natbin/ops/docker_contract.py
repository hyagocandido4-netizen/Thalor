from __future__ import annotations

"""Docker/VPS runtime contract inspection helpers.

This module validates what the container *actually* resolves at runtime instead
of relying only on rendered Compose YAML. It is intentionally side-effect free
so operators can run it from local Docker, VPS or production containers.
"""

import argparse
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from ..config.loader import load_resolved_config
from ..config.paths import resolve_config_path, resolve_repo_root
from ..runtime.connectivity import build_runtime_connectivity_payload

__all__ = ['build_docker_runtime_contract', 'main']

_TRUE_VALUES = {'1', 'true', 't', 'yes', 'y', 'on'}
_FALSE_VALUES = {'0', 'false', 'f', 'no', 'n', 'off'}


@contextmanager
def _patched_environ(env: Mapping[str, str | None] | None):
    if env is None:
        yield
        return
    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update({str(key): str(value) for key, value in dict(env).items() if value is not None})
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)



def _first_nonempty(env: Mapping[str, str | None], *keys: str) -> str | None:
    for key in keys:
        raw = env.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value == '':
            continue
        return value
    return None



def _parse_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return None



def _requested_flags_from_env(env: Mapping[str, str | None]) -> dict[str, Any]:
    return {
        'repo_root': _first_nonempty(env, 'THALOR_REPO_ROOT'),
        'config': _first_nonempty(env, 'THALOR_CONFIG_PATH', 'THALOR_CONFIG'),
        'dashboard_config': _first_nonempty(env, 'THALOR_DASHBOARD_CONFIG_PATH', 'THALOR_DASHBOARD_CONFIG'),
        'transport_enabled': _parse_bool(_first_nonempty(env, 'THALOR__NETWORK__TRANSPORT__ENABLED', 'TRANSPORT_ENABLED')),
        'transport_endpoint_file': _first_nonempty(env, 'THALOR__NETWORK__TRANSPORT__ENDPOINT_FILE', 'TRANSPORT_ENDPOINT_FILE'),
        'transport_endpoint': _first_nonempty(env, 'THALOR__NETWORK__TRANSPORT__ENDPOINT', 'TRANSPORT_ENDPOINT'),
        'request_metrics_enabled': _parse_bool(_first_nonempty(env, 'THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED', 'REQUEST_METRICS_ENABLED')),
        'request_metrics_log_path': _first_nonempty(env, 'THALOR__OBSERVABILITY__REQUEST_METRICS__STRUCTURED_LOG_PATH', 'REQUEST_METRICS_LOG_PATH'),
        'production_profile': _first_nonempty(env, 'THALOR__PRODUCTION__PROFILE'),
        'deployment_profile': _first_nonempty(env, 'THALOR__SECURITY__DEPLOYMENT_PROFILE'),
    }



def build_docker_runtime_contract(
    *,
    repo_root: str | Path | None = None,
    config_path: str | Path | None = None,
    env: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    env_map: Mapping[str, str | None] = dict(env or os.environ)
    with _patched_environ(env_map if env is not None else None):
        root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
        resolved_config_path = resolve_config_path(repo_root=root, config_path=config_path)
        resolved = load_resolved_config(repo_root=root, config_path=resolved_config_path)
        connectivity = build_runtime_connectivity_payload(resolved_config=resolved, repo_root=root)
    requested = _requested_flags_from_env(env_map)

    resolved_view = {
        'config_path': str(resolved_config_path),
        'profile': str(getattr(resolved, 'profile', '') or ''),
        'asset': str(getattr(resolved, 'asset', '') or ''),
        'interval_sec': int(getattr(resolved, 'interval_sec', 0) or 0),
        'timezone': str(getattr(resolved, 'timezone', '') or ''),
        'transport_enabled': bool(connectivity.get('transport_enabled')),
        'transport_ready': bool(connectivity.get('transport_ready')),
        'request_metrics_enabled': bool(connectivity.get('request_metrics_enabled')),
    }

    issues: list[dict[str, Any]] = []

    if requested.get('transport_enabled') is not None and requested['transport_enabled'] != resolved_view['transport_enabled']:
        issues.append(
            {
                'name': 'transport_enabled_mismatch',
                'severity': 'error',
                'requested': requested['transport_enabled'],
                'resolved': resolved_view['transport_enabled'],
            }
        )
    if requested.get('request_metrics_enabled') is not None and requested['request_metrics_enabled'] != resolved_view['request_metrics_enabled']:
        issues.append(
            {
                'name': 'request_metrics_enabled_mismatch',
                'severity': 'error',
                'requested': requested['request_metrics_enabled'],
                'resolved': resolved_view['request_metrics_enabled'],
            }
        )
    if bool(resolved_view['transport_enabled']) and not bool(resolved_view['transport_ready']):
        issues.append(
            {
                'name': 'transport_not_ready',
                'severity': 'error',
                'message': 'Transport layer is enabled but no usable endpoint is available inside the container.',
            }
        )
    if requested.get('config') and str(resolved_config_path) != str(resolve_config_path(repo_root=root, config_path=requested['config'])):
        issues.append(
            {
                'name': 'config_path_mismatch',
                'severity': 'error',
                'requested': str(requested['config']),
                'resolved': str(resolved_config_path),
            }
        )

    return {
        'kind': 'docker_runtime_contract',
        'ok': not issues,
        'repo_root': str(Path(root).resolve()),
        'requested': requested,
        'resolved': resolved_view,
        'connectivity': connectivity,
        'issues': issues,
    }



def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='python -m natbin.ops.docker_contract', description='Inspect effective Docker/VPS runtime configuration and connectivity flags.')
    p.add_argument('--repo-root', default=None)
    p.add_argument('--config', default=None)
    p.add_argument('--json', action='store_true')
    p.add_argument('--strict', action='store_true', help='Return non-zero when the runtime contract is unhealthy or mismatched.')
    return p



def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    payload = build_docker_runtime_contract(repo_root=ns.repo_root, config_path=ns.config)
    if ns.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"ok={payload['ok']} repo_root={payload['repo_root']} config={payload['resolved']['config_path']}")
        for issue in payload.get('issues') or []:
            print(f"- {issue.get('name')}: {issue.get('message') or issue}")
    return 1 if ns.strict and not bool(payload.get('ok')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
