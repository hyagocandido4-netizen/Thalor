from __future__ import annotations

import argparse
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..security.redaction import collect_sensitive_values, sanitize_payload
from .diag_cli_common import (
    add_output_args,
    add_repo_config_args,
    build_logger,
    exception_payload,
    exit_code_from_payload,
    log_event,
    maybe_append_log,
    print_payload,
    utc_now_iso,
    write_json,
    write_repo_artifact,
)


@dataclass(frozen=True)
class TargetSpec:
    name: str
    module: str
    symbols: tuple[str, ...] = ()
    probe_kind: str | None = None


_DEFAULT_TARGETS = (
    TargetSpec('control_app', 'natbin.control.app', ('main',), probe_kind='control_parser'),
    TargetSpec('runtime_connectivity', 'natbin.runtime.connectivity', ('build_runtime_connectivity_payload',), probe_kind='runtime_connectivity'),
    TargetSpec('network_transport', 'natbin.utils.network_transport', ('NetworkTransportManager', 'NetworkTransportConfig')),
    TargetSpec('request_metrics', 'natbin.utils.request_metrics', ('RequestMetrics', 'RequestMetricsConfig')),
    TargetSpec('broker_surface', 'natbin.runtime.broker_surface', ('adapter_from_context',), probe_kind='broker_adapter'),
    TargetSpec('brokers_iqoption', 'natbin.brokers.iqoption', ('IQOptionAdapter',)),
    TargetSpec('adapters_iq_client', 'natbin.adapters.iq_client', ('IQClient', 'iqoption_dependency_status'), probe_kind='iq_dependency'),
    TargetSpec('runtime_daemon', 'natbin.runtime.daemon', ('main', 'DaemonStatus')),
    TargetSpec('portfolio_runner', 'natbin.portfolio.runner', ('load_scopes', 'run_portfolio_cycle'), probe_kind='portfolio_scopes'),
    TargetSpec('production_doctor', 'natbin.ops.production_doctor', ('build_production_doctor_payload',)),
)


def _probe_runtime_connectivity(ctx, repo: Path) -> dict[str, Any]:
    from ..runtime.connectivity import (
        build_runtime_network_transport_manager,
        build_runtime_request_metrics_config,
    )

    manager = build_runtime_network_transport_manager(resolved_config=ctx.resolved_config, repo_root=repo)
    metrics = build_runtime_request_metrics_config(resolved_config=ctx.resolved_config, repo_root=repo)
    return {
        'transport_enabled': bool(manager.enabled),
        'transport_ready': bool(manager.ready),
        'transport_dependency': manager.dependency_status(),
        'request_metrics_enabled': bool(metrics.enabled),
    }


def _probe_broker_adapter(ctx, repo: Path) -> dict[str, Any]:
    from ..runtime.broker_surface import adapter_from_context

    adapter = adapter_from_context(ctx, repo_root=repo)
    out: dict[str, Any] = {
        'adapter_type': type(adapter).__name__,
    }
    dep = getattr(adapter, '_dependency_status', None)
    if callable(dep):
        out['dependency'] = dep()
    creds = getattr(adapter, '_credentials', None)
    if callable(creds):
        try:
            email, password = creds()
            out['credentials_present'] = bool(email) and bool(password)
        except Exception as exc:
            out['credentials_error'] = f'{type(exc).__name__}: {exc}'
    return out


def _probe_iq_dependency() -> dict[str, Any]:
    from ..adapters.iq_client import iqoption_dependency_status

    return {'dependency': iqoption_dependency_status()}


def _probe_control_parser(module) -> dict[str, Any]:
    import argparse

    parser_factory = getattr(module, '_build_parser', None)
    if not callable(parser_factory):
        return {'parser_available': False}
    parser = parser_factory()
    command_count = None
    for action in getattr(parser, '_actions', []):
        if isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
            command_count = len(action.choices)
            break
    return {
        'parser_available': True,
        'command_count': command_count,
    }


def _probe_portfolio_scopes(repo: Path, cfg_path: Path) -> dict[str, Any]:
    from ..portfolio.runner import load_scopes

    scopes, _cfg = load_scopes(repo_root=repo, config_path=cfg_path)
    return {
        'scope_count': len(scopes),
        'scope_tags': [str(getattr(item, 'scope_tag', '')) for item in scopes[:10]],
    }


def _run_probe(spec: TargetSpec, module, *, ctx, repo: Path, cfg_path: Path) -> dict[str, Any]:
    if spec.probe_kind == 'runtime_connectivity':
        return _probe_runtime_connectivity(ctx, repo)
    if spec.probe_kind == 'broker_adapter':
        return _probe_broker_adapter(ctx, repo)
    if spec.probe_kind == 'iq_dependency':
        return _probe_iq_dependency()
    if spec.probe_kind == 'control_parser':
        return _probe_control_parser(module)
    if spec.probe_kind == 'portfolio_scopes':
        return _probe_portfolio_scopes(repo, cfg_path)
    return {}


def build_module_smoke_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    include_optional: bool = True,
    dry_run: bool = False,
    logger=None,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    cfg_path = Path(ctx.config.config_path).resolve()
    sensitive_values = collect_sensitive_values(ctx.resolved_config)

    log_event(logger, 'module_smoke_start', repo_root=str(repo), config_path=str(cfg_path))
    results: list[dict[str, Any]] = []
    actions: list[str] = []

    for spec in _DEFAULT_TARGETS:
        item: dict[str, Any] = {
            'name': spec.name,
            'module': spec.module,
            'symbols': list(spec.symbols),
        }
        try:
            module = importlib.import_module(spec.module)
            item['import_ok'] = True
            missing = [symbol for symbol in spec.symbols if not hasattr(module, symbol)]
            item['missing_symbols'] = missing
            if missing:
                item['status'] = 'error'
                item['message'] = 'Módulo importou, mas símbolos esperados não existem'
            else:
                probe = _run_probe(spec, module, ctx=ctx, repo=repo, cfg_path=cfg_path)
                item['probe'] = probe
                item['status'] = 'ok'
                item['message'] = 'Import e probe local concluídos'
                dep = probe.get('dependency') if isinstance(probe, dict) else None
                if isinstance(dep, dict) and not bool(dep.get('available', True)):
                    item['status'] = 'warn'
                    item['message'] = 'Import ok, mas dependência opcional indisponível'
        except Exception as exc:
            item['import_ok'] = False
            item['status'] = 'error'
            item['message'] = f'{type(exc).__name__}: {exc}'
            actions.append(f'Corrija o módulo/import {spec.module}.')
        results.append(item)

    severity = 'error' if any(item['status'] == 'error' for item in results) else ('warn' if any(item['status'] == 'warn' for item in results) else 'ok')
    payload = {
        'kind': 'module_smoke',
        'at_utc': utc_now_iso(),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'summary': {
            'targets_total': len(results),
            'ok_targets': sum(1 for item in results if item['status'] == 'ok'),
            'warn_targets': sum(1 for item in results if item['status'] == 'warn'),
            'error_targets': sum(1 for item in results if item['status'] == 'error'),
        },
        'results': results,
        'actions': actions,
        'dry_run': bool(dry_run),
    }
    payload = sanitize_payload(payload, sensitive_values=sensitive_values)
    if not dry_run:
        try:
            write_repo_artifact(repo, 'module_smoke', payload)
        except Exception:
            pass
    log_event(logger, 'module_smoke_complete', severity=severity, targets_total=len(results))
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Importa e faz probes locais dos módulos críticos do Thalor sem side effects remotos.')
    add_repo_config_args(parser)
    add_output_args(parser)
    parser.add_argument('--no-optional', action='store_true', help='Reservado para futura expansão')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    logger = build_logger('natbin.module_smoke', verbose=bool(ns.verbose))
    try:
        payload = build_module_smoke_payload(
            repo_root=ns.repo_root,
            config_path=ns.config,
            include_optional=not bool(getattr(ns, 'no_optional', False)),
            dry_run=bool(getattr(ns, 'dry_run', False)),
            logger=logger,
        )
    except Exception as exc:  # pragma: no cover
        payload = exception_payload('module_smoke', exc)
        print_payload(payload, as_json=True)
        return 2

    if ns.output:
        write_json(ns.output, payload)
    maybe_append_log(getattr(ns, 'log_jsonl_path', None), payload)
    print_payload(payload, as_json=bool(ns.json))
    return exit_code_from_payload(payload)


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
