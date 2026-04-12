from __future__ import annotations

import argparse
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
    write_scope_artifact,
)
from .diag_suite import build_diag_suite_payload
from .module_smoke import build_module_smoke_payload
from .safe_refresh import maybe_heal_breaker, maybe_heal_control_freshness, maybe_heal_market_context
from .transport_smoke import build_transport_smoke_payload


def _check_status(payload: dict[str, Any]) -> str:
    severity = str(payload.get('severity') or ('error' if not bool(payload.get('ok', True)) else 'ok'))
    if severity == 'error':
        return 'error'
    if severity in {'warn', 'warning'}:
        return 'warn'
    return 'ok'


def _extract_practice_payload(diag_suite_payload: dict[str, Any]) -> dict[str, Any] | None:
    results = diag_suite_payload.get('results')
    if not isinstance(results, dict):
        return None
    practice = results.get('practice')
    return practice if isinstance(practice, dict) else None


def _find_check(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in list(payload.get('checks') or []):
        if str(item.get('name')) == str(name):
            return item
    return None


def _check_names(payload: dict[str, Any], statuses: set[str]) -> set[str]:
    out: set[str] = set()
    for item in list(payload.get('checks') or []):
        if str(item.get('status') or '').lower() in statuses:
            out.add(str(item.get('name') or 'unknown'))
    return out


def build_practice_preflight_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    probe_broker: bool = False,
    probe_provider: bool = True,
    sample_candles: int = 3,
    market_context_max_age_sec: int | None = None,
    min_dataset_rows: int = 100,
    max_stake_amount: float = 5.0,
    soak_stale_after_sec: int | None = None,
    allow_warnings: bool = False,
    heal_breaker: bool = True,
    breaker_stale_after_sec: int | None = None,
    heal_market_context: bool = True,
    heal_control_freshness: bool = True,
    heal_soak: bool = False,
    soak_cycles: int = 6,
    dry_run: bool = False,
    logger=None,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    sensitive_values = collect_sensitive_values(ctx.resolved_config)

    log_event(
        logger,
        'practice_preflight_start',
        repo_root=str(repo),
        config_path=str(ctx.config.config_path),
        probe_broker=bool(probe_broker),
        probe_provider=bool(probe_provider),
        heal_breaker=bool(heal_breaker),
        heal_market_context=bool(heal_market_context),
        heal_control_freshness=bool(heal_control_freshness),
        heal_soak=bool(heal_soak),
        dry_run=bool(dry_run),
    )

    repairs: list[dict[str, Any]] = []
    breaker_repair = maybe_heal_breaker(
        repo_root=repo,
        config_path=ctx.config.config_path,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        enabled=bool(heal_breaker),
        dry_run=bool(dry_run),
        stale_after_sec=breaker_stale_after_sec,
    )
    if bool(heal_breaker) or bool(breaker_repair.get('attempted')) or str(breaker_repair.get('status')) in {'planned', 'error', 'warn'}:
        repairs.append(breaker_repair)

    max_age = int(market_context_max_age_sec or max(int(ctx.config.interval_sec) * 3, 900))
    freshness_limit = max(int(ctx.config.interval_sec) * 4, 900)
    market_context_repair = maybe_heal_market_context(
        repo_root=repo,
        config_path=ctx.config.config_path,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        max_age_sec=max_age,
        enabled=bool(heal_market_context),
        dry_run=bool(dry_run),
    )
    if bool(heal_market_context) or bool(market_context_repair.get('attempted')) or str(market_context_repair.get('status')) in {'planned', 'error', 'warn'}:
        repairs.append(market_context_repair)

    control_freshness_repair = maybe_heal_control_freshness(
        repo_root=repo,
        config_path=ctx.config.config_path,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        freshness_limit_sec=int(freshness_limit),
        enabled=bool(heal_control_freshness),
        dry_run=bool(dry_run),
    )
    if bool(heal_control_freshness) or bool(control_freshness_repair.get('attempted')) or str(control_freshness_repair.get('status')) in {'planned', 'error', 'warn'}:
        repairs.append(control_freshness_repair)

    def _run_diag_suite() -> dict[str, Any]:
        return build_diag_suite_payload(
            repo_root=repo,
            config_path=ctx.config.config_path,
            asset=str(ctx.config.asset),
            interval_sec=int(ctx.config.interval_sec),
            all_scopes=False,
            include_provider_probe=True,
            active_provider_probe=bool(probe_provider) and not dry_run,
            include_practice=True,
            include_support_bundle=False,
            probe_broker=bool(probe_broker) and not dry_run,
            sample_candles=max(0, int(sample_candles)),
            market_context_max_age_sec=market_context_max_age_sec,
            min_dataset_rows=int(min_dataset_rows),
            heal_breaker=bool(heal_breaker),
            breaker_stale_after_sec=breaker_stale_after_sec,
            heal_market_context=bool(heal_market_context),
            heal_control_freshness=bool(heal_control_freshness),
            max_stake_amount=float(max_stake_amount),
            soak_stale_after_sec=soak_stale_after_sec,
            dry_run=bool(dry_run),
            logger=logger,
        )

    diag_suite = _run_diag_suite()
    transport = build_transport_smoke_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        all_scopes=False,
        active_healthchecks=not dry_run,
        only_unavailable=False,
        simulate_failures=0,
        operation='practice_preflight',
        dry_run=bool(dry_run),
        logger=logger,
    )
    modules = build_module_smoke_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        dry_run=bool(dry_run),
        logger=logger,
    )

    practice = _extract_practice_payload(diag_suite) or {}
    drain_check = _find_check(practice, 'drain_mode') or {}
    soak_check = _find_check(practice, 'runtime_soak') or {}
    doctor_check = _find_check(practice, 'production_doctor') or {}
    transport_scope = (transport.get('scope_results') or [{}])[0] if isinstance(transport.get('scope_results'), list) and transport.get('scope_results') else {}

    if bool(heal_soak) and not bool(dry_run):
        practice_errors = _check_names(practice, {'error'})
        practice_warns = _check_names(practice, {'warn', 'warning'})
        only_soak_is_pending = bool(soak_check) and str(soak_check.get('status')) == 'warn' and not practice_errors and practice_warns <= {'runtime_soak'}
        if only_soak_is_pending:
            from .practice_bootstrap import build_practice_bootstrap_payload

            bootstrap = build_practice_bootstrap_payload(
                repo_root=repo,
                config_path=ctx.config.config_path,
                soak_cycles=max(1, int(soak_cycles)),
                force_prepare=False,
                force_soak=True,
                skip_soak=False,
                max_stake_amount=float(max_stake_amount),
                soak_stale_after_sec=soak_stale_after_sec,
                clear_drain=False,
                reset_breaker=False,
                alerts_test=False,
                write_artifact=True,
            )
            soak_repair = {
                'name': 'runtime_soak',
                'safe': False,
                'potentially_submits': True,
                'enabled': True,
                'attempted': True,
                'status': 'ok' if bool(bootstrap.get('ok')) else 'error',
                'message': 'runtime_soak_refreshed' if bool(bootstrap.get('ok')) else str(bootstrap.get('blocked_reason') or 'runtime_soak_refresh_failed'),
                'result': bootstrap,
            }
            repairs.append(soak_repair)
            diag_suite = _run_diag_suite()
            practice = _extract_practice_payload(diag_suite) or {}
            drain_check = _find_check(practice, 'drain_mode') or {}
            soak_check = _find_check(practice, 'runtime_soak') or {}
            doctor_check = _find_check(practice, 'production_doctor') or {}
        elif bool(soak_check) and str(soak_check.get('status')) == 'warn':
            repairs.append(
                {
                    'name': 'runtime_soak',
                    'safe': False,
                    'potentially_submits': True,
                    'enabled': True,
                    'attempted': False,
                    'status': 'skip',
                    'message': 'runtime_soak_repair_skipped_due_additional_blockers',
                    'blocking_checks': sorted(_check_names(practice, {'error', 'warn', 'warning'}) - {'runtime_soak'}),
                }
            )
    elif bool(heal_soak) and bool(dry_run):
        repairs.append(
            {
                'name': 'runtime_soak',
                'safe': False,
                'potentially_submits': True,
                'enabled': True,
                'attempted': False,
                'status': 'planned',
                'message': 'would_run_runtime_soak_if_needed',
                'cycles': max(1, int(soak_cycles)),
            }
        )

    blockers: list[str] = []
    warnings: list[str] = []
    actions: list[str] = []

    if _check_status(diag_suite) == 'error':
        blockers.append('diag_suite')
    elif _check_status(diag_suite) == 'warn':
        warnings.append('diag_suite')

    if _check_status(transport) == 'error':
        blockers.append('transport_smoke')
    elif _check_status(transport) == 'warn':
        warnings.append('transport_smoke')

    if _check_status(modules) == 'error':
        blockers.append('module_smoke')
    elif _check_status(modules) == 'warn':
        warnings.append('module_smoke')

    practice_status = _check_status(practice) if practice else ('ok' if bool(practice.get('ready_for_practice', True)) else 'error')
    if practice_status == 'error':
        blockers.append('practice_readiness')
    elif practice_status == 'warn':
        warnings.append('practice_readiness')

    if bool(drain_check) and str(drain_check.get('status')) == 'warn':
        blockers.append('drain_mode_active')
        actions.append('Desative runs/DRAIN_MODE antes de iniciar a sessão longa em PRACTICE.')

    if bool(soak_check) and str(soak_check.get('status')) == 'warn':
        warnings.append('runtime_soak')
        actions.append('Rode um soak curto antes da sessão longa para validar loop, alertas e artifacts.')

    if bool(doctor_check) and str(doctor_check.get('status')) == 'error':
        actions.append('Resolva os blockers do production_doctor antes de iniciar observe/practice-round.')

    for source in (diag_suite, transport_scope, modules):
        for action in list(source.get('actions') or []):
            text = str(action or '').strip()
            if text and text not in actions:
                actions.append(text)

    if any(str(item.get('name')) == 'market_context' and str(item.get('status')) in {'planned', 'warn', 'error'} for item in repairs):
        hint = 'Use --no-heal-market-context apenas se quiser inspecionar sem mutar artifacts; o preflight atual já tenta a regeneração segura do market_context.'
        if hint not in actions:
            actions.append(hint)

    if warnings and not allow_warnings:
        if 'warnings_present' not in blockers:
            blockers.append('warnings_present')
        notice = 'Resolva todos os warnings antes da sessão longa; a política atual é zero-warning.'
        if notice not in actions:
            actions.append(notice)

    severity = 'error' if blockers else ('warn' if warnings else 'ok')
    ready_for_long_practice = not blockers and (allow_warnings or not warnings) and bool(practice.get('ready_for_practice', diag_suite.get('ready_for_practice', False)))

    checks = [
        {
            'name': 'diag_suite',
            'status': _check_status(diag_suite),
            'message': 'Suíte consolidada de auditorias concluída',
        },
        {
            'name': 'transport_smoke',
            'status': _check_status(transport),
            'message': 'Smoke do transporte/proxy concluído',
        },
        {
            'name': 'module_smoke',
            'status': _check_status(modules),
            'message': 'Smoke de módulos críticos concluído',
        },
        {
            'name': 'practice_readiness',
            'status': practice_status,
            'message': 'Readiness de PRACTICE consolidado',
        },
    ]

    recommended_next_command = None
    if not ready_for_long_practice and bool(soak_check) and str(soak_check.get('status')) == 'warn':
        recommended_next_command = (
            f'python -m natbin.runtime_app --repo-root "{repo}" --config "{ctx.config.config_path}" '
            f'practice-bootstrap --force-soak --soak-cycles {max(1, int(soak_cycles))} --json'
        )

    payload = {
        'kind': 'practice_preflight',
        'at_utc': utc_now_iso(),
        'ok': severity != 'error',
        'severity': severity,
        'ready_for_long_practice': ready_for_long_practice,
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': {
            'asset': str(ctx.config.asset),
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': str(ctx.scope.scope_tag),
        },
        'checks': checks,
        'blockers': blockers,
        'warnings': warnings,
        'actions': actions,
        'repairs': repairs,
        'policy': {
            'zero_warning': not bool(allow_warnings),
        },
        'recommended_start_command': (
            f'python -m natbin.runtime_app --repo-root "{repo}" --config "{ctx.config.config_path}" observe --once'
            if ready_for_long_practice
            else None
        ),
        'recommended_next_command': recommended_next_command,
        'results': {
            'diag_suite': diag_suite,
            'transport_smoke': transport,
            'module_smoke': modules,
        },
    }
    payload = sanitize_payload(payload, sensitive_values=sensitive_values)
    if not dry_run:
        try:
            write_scope_artifact(repo, str(ctx.scope.scope_tag), 'practice_preflight', payload)
        except Exception:
            pass
    log_event(
        logger,
        'practice_preflight_complete',
        severity=severity,
        ready_for_long_practice=ready_for_long_practice,
        blockers=blockers,
        warnings=warnings,
    )
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Gate final antes de uma sessão longa em PRACTICE.')
    add_repo_config_args(parser)
    add_output_args(parser)
    parser.add_argument('--probe-broker', action='store_true', help='Permite broker preflight ativo no doctor')
    parser.set_defaults(probe_provider=True, heal_breaker=True, heal_market_context=True, heal_control_freshness=True)
    parser.add_argument('--probe-provider', dest='probe_provider', action='store_true', help='Executa provider-probe remoto ativo (padrão recomendado para preflight).')
    parser.add_argument('--no-probe-provider', dest='probe_provider', action='store_false', help='Desabilita o provider-probe remoto nesta execução.')
    parser.add_argument('--sample-candles', type=int, default=3)
    parser.add_argument('--market-context-max-age-sec', type=int, default=None)
    parser.add_argument('--min-dataset-rows', type=int, default=100)
    parser.add_argument('--heal-breaker', dest='heal_breaker', action='store_true', help='Reseta com segurança breaker stale em open/half-open antes do gate (padrão).')
    parser.add_argument('--no-heal-breaker', dest='heal_breaker', action='store_false', help='Não tenta reparar automaticamente breaker stale.')
    parser.add_argument('--breaker-stale-after-sec', type=int, default=None)
    parser.add_argument('--max-stake-amount', type=float, default=5.0)
    parser.add_argument('--soak-stale-after-sec', type=int, default=None)
    parser.add_argument('--heal-market-context', dest='heal_market_context', action='store_true', help='Regenera de forma segura o market_context antes da suíte (padrão).')
    parser.add_argument('--no-heal-market-context', dest='heal_market_context', action='store_false', help='Não tenta reparar automaticamente o market_context.')
    parser.add_argument('--heal-control-freshness', dest='heal_control_freshness', action='store_true', help='Materializa loop_status/health com observe seguro em drain mode antes da suíte (padrão).')
    parser.add_argument('--no-heal-control-freshness', dest='heal_control_freshness', action='store_false', help='Não tenta reparar automaticamente loop_status/health.')
    parser.add_argument('--heal-soak', action='store_true', help='Se o único pendente for runtime_soak, executa um bootstrap/soak novo. Pode submeter ordens em PRACTICE.')
    parser.add_argument('--soak-cycles', type=int, default=6, help='Número de ciclos do soak quando --heal-soak estiver habilitado.')
    parser.add_argument('--allow-warnings', action='store_true', help='Permite warnings no parecer final (desabilita a política zero-warning)')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    logger = build_logger('natbin.practice_preflight', verbose=bool(ns.verbose))
    try:
        payload = build_practice_preflight_payload(
            repo_root=ns.repo_root,
            config_path=ns.config,
            probe_broker=bool(getattr(ns, 'probe_broker', False)),
            probe_provider=bool(getattr(ns, 'probe_provider', False)),
            sample_candles=int(getattr(ns, 'sample_candles', 3) or 3),
            market_context_max_age_sec=getattr(ns, 'market_context_max_age_sec', None),
            min_dataset_rows=int(getattr(ns, 'min_dataset_rows', 100) or 100),
            max_stake_amount=float(getattr(ns, 'max_stake_amount', 5.0) or 5.0),
            soak_stale_after_sec=getattr(ns, 'soak_stale_after_sec', None),
            allow_warnings=bool(getattr(ns, 'allow_warnings', False)),
            heal_breaker=bool(getattr(ns, 'heal_breaker', True)),
            breaker_stale_after_sec=getattr(ns, 'breaker_stale_after_sec', None),
            heal_market_context=bool(getattr(ns, 'heal_market_context', True)),
            heal_control_freshness=bool(getattr(ns, 'heal_control_freshness', True)),
            heal_soak=bool(getattr(ns, 'heal_soak', False)),
            soak_cycles=int(getattr(ns, 'soak_cycles', 6) or 6),
            dry_run=bool(getattr(ns, 'dry_run', False)),
            logger=logger,
        )
    except Exception as exc:  # pragma: no cover
        payload = exception_payload('practice_preflight', exc)
        print_payload(payload, as_json=True)
        return 2

    if ns.output:
        write_json(ns.output, payload)
    maybe_append_log(getattr(ns, 'log_jsonl_path', None), payload)
    print_payload(payload, as_json=bool(ns.json))
    return exit_code_from_payload(payload)


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
