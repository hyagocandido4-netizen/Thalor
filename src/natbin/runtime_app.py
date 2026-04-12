from __future__ import annotations

"""Public Package M control-plane shim.

This module preserves the historical ``natbin.runtime_app`` import path while
moving the real implementation to ``natbin.control.*``.

SYNC-1A adds a light entry path for ``runtime_app sync`` so the canonical
workspace baseline can be generated before the full Python dependency stack is
installed. All other commands still use the normal control-plane entrypoint.
"""

import json
import sys
from datetime import UTC, datetime
from typing import Any

__all__ = [
    'DEFAULT_CONFIG_PATH',
    'RuntimeAppCapabilities',
    'RuntimeAppConfig',
    'RuntimeAppInfo',
    'RuntimeContext',
    'build_context',
    'build_runtime_app_info',
    'derive_scoped_paths',
    'detect_capabilities',
    'load_runtime_app_config',
    'build_runtime_network_transport_config',
    'build_runtime_network_transport_manager',
    'build_runtime_request_metrics_config',
    'build_runtime_request_metrics',
    'build_runtime_connectivity_payload',
    'main',
    'to_json_dict',
]


_MODEL_EXPORTS = {
    'RuntimeAppCapabilities',
    'RuntimeAppConfig',
    'RuntimeAppInfo',
    'RuntimeContext',
}

_PLAN_EXPORTS = {
    'DEFAULT_CONFIG_PATH',
    'build_context',
    'build_runtime_app_info',
    'derive_scoped_paths',
    'detect_capabilities',
    'load_runtime_app_config',
    'to_json_dict',
}

_CONNECTIVITY_EXPORTS = {
    'build_runtime_network_transport_config',
    'build_runtime_network_transport_manager',
    'build_runtime_request_metrics_config',
    'build_runtime_request_metrics',
    'build_runtime_connectivity_payload',
}


def __getattr__(name: str) -> Any:
    if name == 'main':
        from .control.app import main as control_main

        return control_main
    if name in _MODEL_EXPORTS:
        from .control import models as control_models

        return getattr(control_models, name)
    if name in _PLAN_EXPORTS:
        from .control import plan as control_plan

        return getattr(control_plan, name)
    if name in _CONNECTIVITY_EXPORTS:
        from .runtime import connectivity as runtime_connectivity

        return getattr(runtime_connectivity, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


def _wants_json(raw: list[str]) -> bool:
    return '--json' in raw


def _primary_command(raw: list[str]) -> str | None:
    i = 0
    while i < len(raw):
        arg = str(raw[i])
        if arg in {'--repo-root', '--config'}:
            i += 2
            continue
        if arg.startswith('--repo-root=') or arg.startswith('--config='):
            i += 1
            continue
        if arg.startswith('-'):
            i += 1
            continue
        return arg
    return None


def _print_bootstrap_error(exc: ModuleNotFoundError, *, as_json: bool) -> None:
    payload = {
        'ok': False,
        'severity': 'error',
        'kind': 'bootstrap_dependency_error',
        'missing_module': exc.name,
        'message': 'Dependência Python ausente. O runtime completo precisa do ambiente do projeto instalado.',
        'recommended_commands': [
            'py -3.12 -m venv .venv',
            '.\\.venv\\Scripts\\python.exe -m pip install -U pip',
            '.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt',
        ],
        'notes': [
            'O comando `runtime_app sync` já possui um caminho leve no SYNC-1A e roda mesmo sem pydantic.',
            'Os demais comandos do control-plane continuam exigindo o ambiente completo do projeto.',
        ],
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print('Erro de bootstrap do ambiente Python')
    print(f"Módulo ausente: {exc.name}")
    print('Comandos recomendados:')
    for item in payload['recommended_commands']:
        print(f'  {item}')


if __name__ == '__main__':  # pragma: no cover
    raw = list(sys.argv[1:])
    as_json = _wants_json(raw)
    try:
        primary = _primary_command(raw)
        if primary == 'sync':
            from .ops.sync_cli import main as entry_main
        else:
            try:
                from .control.app import main as entry_main
            except ModuleNotFoundError as exc:
                if exc.name in {'pydantic', 'pydantic_settings'}:
                    _print_bootstrap_error(exc, as_json=as_json)
                    raise SystemExit(3)
                raise
        raise SystemExit(entry_main())
    except KeyboardInterrupt:
        payload = {
            'ok': False,
            'message': 'interrupted',
            'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        }
        if as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print('Interrupted')
        raise SystemExit(130)
