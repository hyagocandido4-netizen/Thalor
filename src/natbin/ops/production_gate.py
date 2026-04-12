from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..control.commands import release_payload
from ..control.plan import build_context
from ..ops.production_doctor import build_production_doctor_payload
from ..state.control_repo import write_control_artifact, write_repo_control_artifact
from .diagnostic_utils import check, dedupe_actions, load_selected_scopes, now_utc, severity_from_checks
from .provider_probe import build_provider_probe_payload


_CATEGORY_HINTS = {
    'broker_preflight': 'provider',
    'provider_dependency': 'provider',
    'provider_credentials': 'provider',
    'provider_session': 'provider',
    'remote_candles': 'provider',
    'remote_market_context': 'provider',
    'market_context': 'data',
    'market_context_local': 'data',
    'candle_db_local': 'data',
    'dataset_ready': 'data',
    'failsafe_kill_switch': 'guardrail',
    'failsafe_drain_mode': 'guardrail',
    'circuit_breaker': 'guardrail',
    'mode_alignment': 'config',
    'execution_mode': 'config',
    'security_posture': 'security',
    'intelligence_surface': 'intelligence',
}


def _scope_gate(scope_doctor: dict[str, Any], scope_probe: dict[str, Any]) -> dict[str, Any]:
    doctor_sev = str(scope_doctor.get('severity') or 'ok')
    probe_sev = str(scope_probe.get('severity') or 'ok')
    severity = 'error' if 'error' in {doctor_sev, probe_sev} else ('warn' if 'warn' in {doctor_sev, probe_sev} else 'ok')
    blockers: list[dict[str, Any]] = []
    for item in list(scope_doctor.get('blockers') or []):
        name = str(item)
        blockers.append({'name': name, 'category': _CATEGORY_HINTS.get(name, 'runtime'), 'source': 'doctor'})
    for item in list(scope_probe.get('checks') or []):
        if str(item.get('status')) != 'error':
            continue
        name = str(item.get('name') or 'provider_probe')
        blockers.append({'name': name, 'category': _CATEGORY_HINTS.get(name, 'provider'), 'source': 'provider_probe'})
    warnings: list[dict[str, Any]] = []
    for item in list(scope_doctor.get('warnings') or []):
        name = str(item)
        warnings.append({'name': name, 'category': _CATEGORY_HINTS.get(name, 'runtime'), 'source': 'doctor'})
    for item in list(scope_probe.get('checks') or []):
        if str(item.get('status')) != 'warn':
            continue
        name = str(item.get('name') or 'provider_probe')
        warnings.append({'name': name, 'category': _CATEGORY_HINTS.get(name, 'provider'), 'source': 'provider_probe'})

    ready_for_cycle = bool(scope_doctor.get('ready_for_cycle')) and severity != 'error'
    ready_for_live = bool(scope_doctor.get('ready_for_live')) and severity != 'error'
    provider_ready = bool(((scope_probe.get('shared_provider_session') or {}).get('ok'))) and all(
        bool(item.get('ok'))
        for item in (
            scope_probe.get('remote_candles') or {},
            scope_probe.get('remote_market_context') or {},
        )
        if bool(item.get('attempted'))
    )
    if not bool((scope_probe.get('shared_provider_session') or {}).get('attempted')):
        provider_ready = bool(scope_probe.get('ok'))

    actions = dedupe_actions(list(scope_doctor.get('actions') or []) + list(scope_probe.get('actions') or []))
    return {
        'scope': dict(scope_probe.get('scope') or scope_doctor.get('scope') or {}),
        'severity': severity,
        'ok': severity != 'error',
        'ready_for_cycle': ready_for_cycle,
        'ready_for_live': ready_for_live,
        'provider_ready': provider_ready,
        'doctor': {
            'severity': doctor_sev,
            'blockers': list(scope_doctor.get('blockers') or []),
            'warnings': list(scope_doctor.get('warnings') or []),
            'ready_for_cycle': bool(scope_doctor.get('ready_for_cycle')),
            'ready_for_live': bool(scope_doctor.get('ready_for_live')),
            'ready_for_practice': bool(scope_doctor.get('ready_for_practice')),
            'ready_for_real': bool(scope_doctor.get('ready_for_real')),
        },
        'provider_probe': {
            'severity': probe_sev,
            'shared_provider_session': scope_probe.get('shared_provider_session'),
            'remote_candles': scope_probe.get('remote_candles'),
            'remote_market_context': scope_probe.get('remote_market_context'),
        },
        'blockers': blockers,
        'warnings': warnings,
        'actions': actions,
    }


def build_production_gate_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = False,
    probe_provider: bool = True,
    sample_candles: int = 3,
    market_context_max_age_sec: int | None = None,
    min_dataset_rows: int = 100,
    write_artifact: bool = True,
) -> dict[str, Any]:
    repo, cfg_path, cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    if not scopes:
        return {
            'at_utc': now_utc().isoformat(timespec='seconds'),
            'kind': 'production_gate',
            'ok': False,
            'severity': 'error',
            'repo_root': str(repo),
            'config_path': str(cfg_path),
            'message': 'no_scopes_selected',
            'scope_results': [],
        }

    primary = scopes[0]
    primary_ctx = build_context(repo_root=repo, config_path=cfg_path, asset=str(primary.asset), interval_sec=int(primary.interval_sec), dump_snapshot=False)
    scope_results: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    provider_payload = build_provider_probe_payload(
        repo_root=repo,
        config_path=cfg_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
        active=bool(probe_provider),
        sample_candles=sample_candles,
        probe_market_context=True,
        market_context_max_age_sec=market_context_max_age_sec,
        write_artifact=False,
    )
    checks.append(check('provider_probe', str(provider_payload.get('severity') or 'ok'), 'Provider probe consolidado', scope_count=int((provider_payload.get('summary') or {}).get('scope_count') or 0)))

    release = release_payload(repo_root=repo, config_path=cfg_path)
    checks.append(check('release_readiness', str(release.get('severity') or 'ok'), 'Release readiness avaliado', ready_for_real=bool(release.get('ready_for_real')), ready_for_practice=bool(release.get('ready_for_practice'))))

    provider_scope_map = {
        str((item.get('scope') or {}).get('scope_tag') or ''): item
        for item in list(provider_payload.get('scope_results') or [])
        if isinstance(item, dict)
    }

    for scope in scopes:
        doctor = build_production_doctor_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=str(scope.asset),
            interval_sec=int(scope.interval_sec),
            probe_broker=False,
            strict_runtime_artifacts=True,
            enforce_live_broker_prereqs=True,
            market_context_max_age_sec=market_context_max_age_sec,
            min_dataset_rows=int(min_dataset_rows),
            write_artifact=False,
        )
        probe = provider_scope_map.get(str(scope.scope_tag)) or {
            'scope': {'asset': str(scope.asset), 'interval_sec': int(scope.interval_sec), 'scope_tag': str(scope.scope_tag)},
            'severity': 'error',
            'ok': False,
            'checks': [check('provider_probe_missing', 'error', 'Resultado do provider_probe não encontrado para o scope')],
            'actions': ['Rerode runtime_app provider-probe para este scope.'],
            'shared_provider_session': {'attempted': False, 'ok': False, 'reason': 'missing'},
            'remote_candles': {'attempted': False, 'ok': False, 'reason': 'missing'},
            'remote_market_context': {'attempted': False, 'ok': False, 'reason': 'missing'},
        }
        gate = _scope_gate(doctor, probe)
        scope_results.append(gate)
        if write_artifact:
            write_control_artifact(repo_root=repo, asset=str(scope.asset), interval_sec=int(scope.interval_sec), name='production_gate', payload=gate)

    scope_errors = sum(1 for item in scope_results if str(item.get('severity')) == 'error')
    scope_warnings = sum(1 for item in scope_results if str(item.get('severity')) == 'warn')
    ready_for_cycle_count = sum(1 for item in scope_results if bool(item.get('ready_for_cycle')))
    ready_for_live_count = sum(1 for item in scope_results if bool(item.get('ready_for_live')))
    provider_ready_count = sum(1 for item in scope_results if bool(item.get('provider_ready')))

    severity = severity_from_checks(checks + [check(f"scope:{(item.get('scope') or {}).get('scope_tag')}", item.get('severity') or 'ok', 'scope_gate') for item in scope_results])
    blockers = [blocker for item in scope_results for blocker in list(item.get('blockers') or [])]
    warnings = [warning for item in scope_results for warning in list(item.get('warnings') or [])]
    actions = dedupe_actions([
        *[action for item in scope_results for action in list(item.get('actions') or [])],
        *list(provider_payload.get('actions') or []),
        'Quando todos os scopes estiverem verdes, rode runtime_app portfolio observe --once para validar o ciclo multi-asset completo.' if bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)) else 'Quando o gate estiver verde, execute a validação operacional seguinte (practice-round ou real-preflight).',
    ])

    payload = {
        'at_utc': now_utc().isoformat(timespec='seconds'),
        'kind': 'production_gate',
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'all_scopes': bool(all_scopes),
        'scope_count': len(scope_results),
        'summary': {
            'scope_errors': scope_errors,
            'scope_warnings': scope_warnings,
            'ready_for_cycle_count': ready_for_cycle_count,
            'ready_for_live_count': ready_for_live_count,
            'provider_ready_count': provider_ready_count,
            'multi_asset_enabled': bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
            'max_parallel_assets': int(getattr(getattr(cfg, 'multi_asset', None), 'max_parallel_assets', 1) or 1),
            'portfolio_topk_total': int(getattr(getattr(cfg, 'multi_asset', None), 'portfolio_topk_total', 1) or 1),
            'portfolio_hard_max_positions': int(getattr(getattr(cfg, 'multi_asset', None), 'portfolio_hard_max_positions', 1) or 1),
        },
        'execution': {
            'enabled': bool((primary_ctx.resolved_config.get('execution') or {}).get('enabled')),
            'mode': str((primary_ctx.resolved_config.get('execution') or {}).get('mode') or 'disabled'),
            'provider': str((primary_ctx.resolved_config.get('execution') or {}).get('provider') or 'fake'),
            'account_mode': str((primary_ctx.resolved_config.get('execution') or {}).get('account_mode') or 'PRACTICE').upper(),
        },
        'broker': {
            'provider': str((primary_ctx.resolved_config.get('broker') or {}).get('provider') or ((primary_ctx.resolved_config.get('execution') or {}).get('provider') or 'unknown')),
            'balance_mode': str((primary_ctx.resolved_config.get('broker') or {}).get('balance_mode') or ((primary_ctx.resolved_config.get('execution') or {}).get('account_mode') or 'PRACTICE')).upper(),
        },
        'ready_for_cycle': scope_errors == 0 and ready_for_cycle_count == len(scope_results),
        'ready_for_live': scope_errors == 0 and ready_for_live_count == len(scope_results),
        'ready_for_all_scopes': scope_errors == 0 and provider_ready_count == len(scope_results) and ready_for_cycle_count == len(scope_results),
        'blockers': blockers,
        'warnings': warnings,
        'actions': actions,
        'provider_probe': {
            'severity': provider_payload.get('severity'),
            'summary': provider_payload.get('summary'),
            'shared_provider_session': provider_payload.get('shared_provider_session'),
            'transport_hint': provider_payload.get('transport_hint'),
            'probe_mode': 'active' if bool(probe_provider) else 'passive_cached',
            'effective_summary': {
                'scope_count': len(scope_results),
                'provider_ready_scopes': provider_ready_count,
                'ready_for_cycle_count': ready_for_cycle_count,
                'ready_for_live_count': ready_for_live_count,
            },
        },
        'release': {
            'severity': release.get('severity'),
            'ready_for_live': release.get('ready_for_live'),
            'ready_for_practice': release.get('ready_for_practice'),
            'ready_for_real': release.get('ready_for_real'),
            'checks': [
                {
                    'name': item.get('name'),
                    'status': item.get('status'),
                    'message': item.get('message'),
                }
                for item in list(release.get('checks') or [])
            ],
        },
        'scope_results': scope_results,
    }
    if write_artifact:
        if all_scopes or len(scope_results) > 1:
            write_repo_control_artifact(repo_root=repo, name='production_gate', payload=payload)
        else:
            scope = scope_results[0].get('scope') or {}
            write_control_artifact(repo_root=repo, asset=str(scope.get('asset') or primary.asset), interval_sec=int(scope.get('interval_sec') or primary.interval_sec), name='production_gate', payload=payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Consolidate provider, doctor and release diagnostics into one production gate payload')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--asset', default=None)
    ap.add_argument('--interval-sec', type=int, default=None)
    ap.add_argument('--all-scopes', action='store_true')
    ap.add_argument('--probe-provider', action='store_true', help='Open a real provider session and fetch remote probes (recommended for live readiness)')
    ap.add_argument('--sample-candles', type=int, default=3)
    ap.add_argument('--market-context-max-age-sec', type=int, default=None)
    ap.add_argument('--min-dataset-rows', type=int, default=100)
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_production_gate_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        all_scopes=bool(ns.all_scopes),
        probe_provider=bool(ns.probe_provider),
        sample_candles=int(ns.sample_candles or 0),
        market_context_max_age_sec=ns.market_context_max_age_sec,
        min_dataset_rows=int(ns.min_dataset_rows or 100),
        write_artifact=True,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
