from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..runtime.connectivity import (
    build_runtime_network_transport_config,
    build_runtime_network_transport_manager,
    build_runtime_request_metrics_config,
)
from ..security.redaction import collect_sensitive_values, sanitize_payload
from .config_provenance import _collect_transport_sources, _read_bundle, _safe_env_lines, _secret_bundle_path
from .diag_cli_common import (
    add_output_args,
    add_repo_config_args,
    add_scope_args,
    build_logger,
    exception_payload,
    exit_code_from_payload,
    log_event,
    maybe_append_log,
    print_payload,
    utc_now_iso,
    write_json,
    write_repo_artifact,
    write_scope_artifact,
)


def _scope_status(result: dict[str, Any]) -> str:
    return 'error' if str(result.get('severity') or 'ok') == 'error' else ('warn' if str(result.get('severity') or 'ok') in {'warn', 'warning'} else 'ok')


def _transport_sources(repo: Path, resolved_config: dict[str, Any]) -> dict[str, Any]:
    env_path = repo / '.env'
    env_lines = _safe_env_lines(env_path if env_path.exists() else None)
    bundle_path = _secret_bundle_path(repo, resolved_config, env_path if env_path.exists() else None)
    bundle = _read_bundle(bundle_path)
    payload = _collect_transport_sources(repo, bundle, env_lines)
    payload['secret_bundle_present'] = bool(bundle_path and bundle_path.exists())
    payload['secret_bundle_path'] = str(bundle_path) if bundle_path is not None and bundle_path.exists() else None
    return payload


def _simulate_failures(manager, endpoint, *, operation: str, count: int) -> dict[str, Any]:
    if endpoint is None or int(count) <= 0:
        return {'attempted': False, 'count': 0, 'quarantined': False}
    for idx in range(int(count)):
        manager.record_failure(
            endpoint,
            operation=operation,
            error=RuntimeError(f'diagnostic_injected_failure_{idx + 1}'),
            latency_s=0.0,
        )
    snapshot = manager.snapshot()
    quarantined = any(
        str((item.get('endpoint') or {}).get('name') or '') == str(getattr(endpoint, 'name', ''))
        and bool(item.get('quarantined'))
        for item in list(snapshot.get('endpoints') or [])
    )
    return {
        'attempted': True,
        'count': int(count),
        'quarantined': quarantined,
        'snapshot': snapshot,
    }


def _scope_payload(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    asset: str | None,
    interval_sec: int | None,
    active_healthchecks: bool,
    only_unavailable: bool,
    simulate_failures: int,
    operation: str,
    dry_run: bool,
    logger=None,
) -> dict[str, Any]:
    ctx = build_context(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        dump_snapshot=False,
    )
    repo = Path(ctx.repo_root).resolve()
    resolved = dict(ctx.resolved_config or {})
    sensitive_values = collect_sensitive_values(ctx.resolved_config)

    source_hint = _transport_sources(repo, resolved)
    transport_config = build_runtime_network_transport_config(resolved_config=ctx.resolved_config, repo_root=repo)
    request_metrics_config = build_runtime_request_metrics_config(resolved_config=ctx.resolved_config, repo_root=repo)
    manager = build_runtime_network_transport_manager(resolved_config=ctx.resolved_config, repo_root=repo)
    dependency = manager.dependency_status()
    snapshot_before = manager.snapshot()

    checks: list[dict[str, Any]] = []
    actions: list[str] = []

    if bool(source_hint.get('configured')):
        checks.append(
            {
                'name': 'transport_config_present',
                'status': 'ok',
                'message': 'Configuração de transporte encontrada',
                'selected_source': source_hint.get('selected_source'),
                'scheme': source_hint.get('scheme'),
            }
        )
    else:
        checks.append(
            {
                'name': 'transport_config_present',
                'status': 'warn',
                'message': 'Nenhum endpoint de transporte configurado',
                'selected_source': source_hint.get('selected_source'),
            }
        )
        actions.append('Preencha o transporte em secrets/transport_endpoint ou via THALOR__NETWORK__TRANSPORT__*.')

    if bool(transport_config.enabled):
        checks.append(
            {
                'name': 'transport_enabled',
                'status': 'ok' if bool(transport_config.ready) else 'warn',
                'message': 'Transporte habilitado' if bool(transport_config.ready) else 'Transporte habilitado sem endpoint pronto',
                'endpoint_count': len(transport_config.endpoints),
            }
        )
    else:
        checks.append(
            {
                'name': 'transport_enabled',
                'status': 'warn',
                'message': 'Transporte desabilitado no scope atual',
                'endpoint_count': len(transport_config.endpoints),
            }
        )

    if bool(dependency.get('available', True)):
        checks.append(
            {
                'name': 'dependency_status',
                'status': 'ok',
                'message': 'Dependências do transporte disponíveis',
                'requires_pysocks': bool(dependency.get('requires_pysocks')),
                'pysocks_available': bool(dependency.get('pysocks_available', True)),
            }
        )
    else:
        checks.append(
            {
                'name': 'dependency_status',
                'status': 'error',
                'message': 'Dependência do transporte ausente',
                'reason': dependency.get('reason'),
                'requires_pysocks': bool(dependency.get('requires_pysocks')),
                'pysocks_available': bool(dependency.get('pysocks_available', False)),
            }
        )
        actions.append('Instale PySocks quando o endpoint configurado usar socks/socks4/socks5.')

    binding_payload: dict[str, Any] | None = None
    binding_error: str | None = None
    selected_endpoint = None
    try:
        binding = manager.select_binding(operation=operation)
        selected_endpoint = binding.endpoint
        binding_payload = binding.as_dict(mask_secret=True)
        checks.append(
            {
                'name': 'binding_selection',
                'status': 'ok',
                'message': 'Binding selecionado com sucesso',
                'binding': binding_payload,
            }
        )
    except Exception as exc:
        binding_error = f'{type(exc).__name__}: {exc}'
        checks.append(
            {
                'name': 'binding_selection',
                'status': 'error',
                'message': 'Falha ao selecionar binding',
                'reason': binding_error,
            }
        )
        actions.append('Revise endpoint(s), fail-open e quarentena do transporte.')

    simulated = _simulate_failures(
        manager,
        selected_endpoint,
        operation=operation,
        count=max(0, int(simulate_failures)),
    )
    if simulated.get('attempted'):
        checks.append(
            {
                'name': 'quarantine_simulation',
                'status': 'ok' if bool(simulated.get('quarantined')) else 'warn',
                'message': 'Falhas injetadas moveram o endpoint para quarentena'
                if bool(simulated.get('quarantined'))
                else 'Falhas injetadas não colocaram o endpoint em quarentena',
                'count': int(simulated.get('count') or 0),
            }
        )

    if bool(active_healthchecks) and not dry_run:
        healthchecks = manager.run_health_checks(only_unavailable=bool(only_unavailable))
        checks.append(
            {
                'name': 'active_healthchecks',
                'status': 'ok' if int(healthchecks.get('unhealthy') or 0) == 0 else 'warn',
                'message': 'Healthchecks ativos concluídos',
                'checked': int(healthchecks.get('checked') or 0),
                'healthy': int(healthchecks.get('healthy') or 0),
                'unhealthy': int(healthchecks.get('unhealthy') or 0),
            }
        )
    else:
        healthchecks = {
            'enabled': bool(manager.enabled),
            'skipped': True,
            'reason': 'dry_run' if dry_run else 'disabled_by_flag',
        }
        checks.append(
            {
                'name': 'active_healthchecks',
                'status': 'warn',
                'message': 'Healthchecks ativos não executados',
                'reason': healthchecks.get('reason'),
            }
        )

    if bool(request_metrics_config.enabled):
        checks.append(
            {
                'name': 'request_metrics',
                'status': 'ok',
                'message': 'Request metrics habilitado',
                'structured_log_path': str(request_metrics_config.structured_log_path)
                if request_metrics_config.structured_log_path is not None
                else None,
            }
        )
    else:
        checks.append(
            {
                'name': 'request_metrics',
                'status': 'warn',
                'message': 'Request metrics desabilitado',
            }
        )

    severity = 'error' if any(item['status'] == 'error' for item in checks) else ('warn' if any(item['status'] == 'warn' for item in checks) else 'ok')
    payload = {
        'kind': 'transport_smoke',
        'at_utc': utc_now_iso(),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': {
            'asset': str(ctx.config.asset),
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': str(ctx.scope.scope_tag),
        },
        'dry_run': bool(dry_run),
        'operation': str(operation),
        'transport_sources': source_hint,
        'transport_config': transport_config.as_dict(mask_secret=True),
        'dependency': dependency,
        'snapshot_before': snapshot_before,
        'binding': binding_payload,
        'binding_error': binding_error,
        'simulation': simulated,
        'healthchecks': healthchecks,
        'request_metrics': request_metrics_config.as_dict(),
        'checks': checks,
        'actions': actions,
    }
    payload = sanitize_payload(payload, sensitive_values=sensitive_values)
    if not dry_run:
        try:
            write_scope_artifact(repo, str(ctx.scope.scope_tag), 'transport_smoke', payload)
        except Exception:
            pass
    log_event(
        logger,
        'transport_smoke_scope_complete',
        scope_tag=str(ctx.scope.scope_tag),
        severity=severity,
        endpoint_count=int(payload.get('transport_config', {}).get('endpoint_count') or 0),
    )
    return payload


def build_transport_smoke_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = False,
    active_healthchecks: bool = True,
    only_unavailable: bool = False,
    simulate_failures: int = 0,
    operation: str = 'diagnostic',
    dry_run: bool = False,
    logger=None,
) -> dict[str, Any]:
    # Resolve scopes through config by reusing build_context for the default scope and then
    # iterating via load_selected_scopes only when requested. Avoid a hard dependency on
    # control_repo mappings so the command can be dropped into the repo immediately.
    from .diagnostic_utils import load_selected_scopes

    repo, cfg_path, _cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )

    scope_results = [
        _scope_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=str(scope.asset),
            interval_sec=int(scope.interval_sec),
            active_healthchecks=bool(active_healthchecks),
            only_unavailable=bool(only_unavailable),
            simulate_failures=int(simulate_failures),
            operation=operation,
            dry_run=bool(dry_run),
            logger=logger,
        )
        for scope in scopes
    ]
    severity = 'error' if any(_scope_status(item) == 'error' for item in scope_results) else ('warn' if any(_scope_status(item) == 'warn' for item in scope_results) else 'ok')
    payload = {
        'kind': 'transport_smoke',
        'at_utc': utc_now_iso(),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'summary': {
            'scope_count': len(scope_results),
            'healthy_scopes': sum(1 for item in scope_results if str(item.get('severity') or 'ok') == 'ok'),
            'warn_scopes': [item['scope']['scope_tag'] for item in scope_results if str(item.get('severity') or 'ok') == 'warn'],
            'error_scopes': [item['scope']['scope_tag'] for item in scope_results if str(item.get('severity') or 'ok') == 'error'],
        },
        'scope_results': scope_results,
    }
    if not dry_run:
        try:
            write_repo_artifact(repo, 'transport_smoke', payload)
        except Exception:
            pass
    log_event(
        logger,
        'transport_smoke_complete',
        severity=severity,
        scope_count=len(scope_results),
    )
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Valida NetworkTransportManager, proxy/Decodo, binding e quarentena.')
    add_repo_config_args(parser)
    add_scope_args(parser, all_scopes=True)
    add_output_args(parser)
    parser.add_argument('--no-active-healthchecks', action='store_true', help='Não executa healthchecks de rede')
    parser.add_argument('--only-unavailable', action='store_true', help='Quando ativo, sonda apenas endpoints indisponíveis')
    parser.add_argument('--simulate-failures', type=int, default=0, help='Injeta falhas em memória para validar quarentena')
    parser.add_argument('--operation', default='diagnostic', help='Nome lógico da operação do binding')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    logger = build_logger('natbin.transport_smoke', verbose=bool(ns.verbose))
    try:
        payload = build_transport_smoke_payload(
            repo_root=ns.repo_root,
            config_path=ns.config,
            asset=getattr(ns, 'asset', None),
            interval_sec=getattr(ns, 'interval_sec', None),
            all_scopes=bool(getattr(ns, 'all_scopes', False)),
            active_healthchecks=not bool(getattr(ns, 'no_active_healthchecks', False)),
            only_unavailable=bool(getattr(ns, 'only_unavailable', False)),
            simulate_failures=int(getattr(ns, 'simulate_failures', 0) or 0),
            operation=str(getattr(ns, 'operation', 'diagnostic') or 'diagnostic'),
            dry_run=bool(getattr(ns, 'dry_run', False)),
            logger=logger,
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        payload = exception_payload('transport_smoke', exc)
        print_payload(payload, as_json=True)
        return 2

    if ns.output:
        write_json(ns.output, payload)
    maybe_append_log(getattr(ns, 'log_jsonl_path', None), payload)
    print_payload(payload, as_json=bool(ns.json))
    return exit_code_from_payload(payload)


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
