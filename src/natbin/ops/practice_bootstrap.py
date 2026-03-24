from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..intelligence.refresh import refresh_config_intelligence
from ..runtime.soak import build_runtime_soak_summary
from ..state.control_repo import write_control_artifact
from .practice_readiness import DEFAULT_MAX_STAKE, build_practice_readiness_payload


PRACTICE_BOOTSTRAP_CRITICAL_CHECKS = {
    'execution_mode',
    'execution_account_mode',
    'broker_balance_mode',
    'controlled_scope',
    'controlled_stake',
    'execution_limits',
    'broker_guard',
    'kill_switch',
    'drain_mode',
}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _status_level(status: str | None) -> int:
    key = str(status or '').strip().lower()
    if key in {'error', 'critical'}:
        return 2
    if key in {'warn', 'warning'}:
        return 1
    return 0


def _check_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    checks = list(payload.get('checks') or []) if isinstance(payload, dict) else []
    out: dict[str, dict[str, Any]] = {}
    for item in checks:
        if isinstance(item, dict):
            name = str(item.get('name') or '').strip()
            if name:
                out[name] = item
    return out


def _critical_preflight_issues(practice: dict[str, Any]) -> list[dict[str, Any]]:
    idx = _check_index(practice)
    out: list[dict[str, Any]] = []
    for name in sorted(PRACTICE_BOOTSTRAP_CRITICAL_CHECKS):
        item = idx.get(name)
        if not isinstance(item, dict):
            continue
        if _status_level(str(item.get('status') or 'ok')) >= 1:
            out.append(item)
    return out


def _round_eligible(practice: dict[str, Any], *, critical_issues: list[dict[str, Any]] | None = None) -> bool:
    issues = critical_issues if critical_issues is not None else _critical_preflight_issues(practice)
    return bool(practice.get('ok')) and not issues


def _warn_names(practice: dict[str, Any]) -> set[str]:
    idx = _check_index(practice)
    return {name for name, item in idx.items() if _status_level(str(item.get('status') or 'ok')) == 1}


def _doctor_blockers(practice: dict[str, Any]) -> set[str]:
    doctor = dict(practice.get('doctor') or {}) if isinstance(practice, dict) else {}
    return {str(item) for item in list(doctor.get('blockers') or []) if item not in (None, '')}


def _doctor_warnings(practice: dict[str, Any]) -> set[str]:
    doctor = dict(practice.get('doctor') or {}) if isinstance(practice, dict) else {}
    return {str(item) for item in list(doctor.get('warnings') or []) if item not in (None, '')}


def _intelligence_warnings(practice: dict[str, Any]) -> set[str]:
    intelligence = dict(practice.get('intelligence') or {}) if isinstance(practice, dict) else {}
    return {str(item) for item in list(intelligence.get('warnings') or []) if item not in (None, '')}


def _write_bootstrap_report_files(*, repo_root: Path, scope_tag: str, payload: dict[str, Any], at_utc: str) -> dict[str, str]:
    base = repo_root / 'runs' / 'tests' / 'practice_bootstraps'
    base.mkdir(parents=True, exist_ok=True)
    stamp = str(at_utc or _now_iso()).replace(':', '').replace('-', '').replace('+00:00', 'Z')
    latest_path = base / f'practice_bootstrap_latest_{scope_tag}.json'
    report_path = base / f'practice_bootstrap_{stamp}_{scope_tag}.json'
    body = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    latest_path.write_text(body, encoding='utf-8')
    report_path.write_text(body, encoding='utf-8')
    return {
        'latest_report_path': str(latest_path),
        'report_path': str(report_path),
    }


def _run_asset_prepare(*, repo_root: Path, config_path: str | Path | None, asset: str, interval_sec: int, lookback_candles: int) -> dict[str, Any]:
    from ..control.commands import asset_prepare_payload

    return asset_prepare_payload(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        lookback_candles=lookback_candles,
    )


def _refresh_intelligence_state(*, repo_root: Path, config_path: str | Path | None, asset: str, interval_sec: int, rebuild_pack: bool) -> dict[str, Any]:
    try:
        payload = refresh_config_intelligence(
            repo_root=repo_root,
            config_path=config_path,
            asset=str(asset),
            interval_sec=int(interval_sec),
            rebuild_pack=bool(rebuild_pack),
            materialize_portfolio=True,
            write_legacy_portfolio=False,
        )
        return {'ok': bool(payload.get('ok')), 'payload': payload, 'rebuild_pack': bool(rebuild_pack)}
    except Exception as exc:
        return {
            'ok': False,
            'rebuild_pack': bool(rebuild_pack),
            'payload': {
                'ok': False,
                'message': 'intelligence_refresh_exception',
                'error': f'{type(exc).__name__}:{exc}',
            },
        }


def _needs_asset_prepare(practice: dict[str, Any], *, force_prepare: bool) -> bool:
    if force_prepare:
        return True
    blockers = _doctor_blockers(practice)
    return bool({'dataset_ready', 'market_context'} & blockers)


def _needs_pack_rebuild(practice: dict[str, Any], *, asset_prepare_ran: bool) -> bool:
    if asset_prepare_ran:
        return True
    warnings = _intelligence_warnings(practice)
    return bool({'pack_artifact', 'latest_eval_artifact'} & warnings)


def _needs_soak(
    practice: dict[str, Any],
    *,
    force_soak: bool,
    skip_soak: bool,
    asset_prepare_ran: bool,
) -> bool:
    if skip_soak:
        return False
    if force_soak or asset_prepare_ran:
        return True
    soak_status = str(((practice.get('soak') or {}).get('status') or 'warn')).lower()
    warnings = _doctor_warnings(practice)
    return bool(soak_status != 'ok' or 'control_freshness' in warnings or not bool(practice.get('ok')))


def build_practice_bootstrap_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    lookback_candles: int = 2000,
    soak_cycles: int = 3,
    force_prepare: bool = False,
    force_soak: bool = False,
    skip_soak: bool = False,
    max_stake_amount: float = DEFAULT_MAX_STAKE,
    soak_stale_after_sec: int | None = None,
    write_artifact: bool = True,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()

    pre_practice = build_practice_readiness_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        max_stake_amount=max_stake_amount,
        soak_stale_after_sec=soak_stale_after_sec,
        write_artifact=True,
    )
    critical_issues = _critical_preflight_issues(pre_practice)
    pre_round_eligible = _round_eligible(pre_practice, critical_issues=critical_issues)

    asset_prepare_action = 'skipped'
    asset_prepare_payload: dict[str, Any] | None = None
    intelligence_refresh_after_prepare: dict[str, Any] | None = None
    intelligence_refresh_after_soak: dict[str, Any] | None = None
    soak_action = 'skipped'
    soak_summary: dict[str, Any] | None = None
    phase = 'preflight'
    blocked_reason: str | None = None

    post_practice = pre_practice
    post_critical_issues = list(critical_issues)
    post_round_eligible = pre_round_eligible

    if critical_issues:
        phase = 'blocked_preflight'
        blocked_reason = 'critical_preflight_blockers'
    else:
        need_prepare = _needs_asset_prepare(pre_practice, force_prepare=bool(force_prepare))
        if need_prepare:
            phase = 'asset_prepare'
            asset_prepare_action = 'ran'
            asset_prepare_payload = _run_asset_prepare(
                repo_root=repo,
                config_path=ctx.config.config_path,
                asset=ctx.config.asset,
                interval_sec=int(ctx.config.interval_sec),
                lookback_candles=int(lookback_candles),
            )
            if not bool((asset_prepare_payload or {}).get('ok')):
                blocked_reason = 'asset_prepare_failed'
        else:
            asset_prepare_action = 'reused_existing_artifacts'

        if blocked_reason is None:
            phase = 'intelligence_refresh_prepare'
            intelligence_refresh_after_prepare = _refresh_intelligence_state(
                repo_root=repo,
                config_path=ctx.config.config_path,
                asset=ctx.config.asset,
                interval_sec=int(ctx.config.interval_sec),
                rebuild_pack=_needs_pack_rebuild(pre_practice, asset_prepare_ran=(asset_prepare_action == 'ran')),
            )

            need_soak = _needs_soak(
                pre_practice,
                force_soak=bool(force_soak),
                skip_soak=bool(skip_soak),
                asset_prepare_ran=(asset_prepare_action == 'ran'),
            )
            if need_soak:
                phase = 'runtime_soak'
                soak_summary = build_runtime_soak_summary(
                    repo_root=repo,
                    config_path=ctx.config.config_path,
                    asset=ctx.config.asset,
                    interval_sec=int(ctx.config.interval_sec),
                    max_cycles=max(1, int(soak_cycles)),
                    write_artifact=True,
                )
                soak_action = 'ran'
                if int((soak_summary or {}).get('exit_code') or 0) != 0:
                    blocked_reason = 'runtime_soak_failed'
            elif skip_soak:
                soak_action = 'disabled'
            else:
                soak_action = 'reused_fresh'

        if blocked_reason is None:
            phase = 'intelligence_refresh_post_soak'
            intelligence_refresh_after_soak = _refresh_intelligence_state(
                repo_root=repo,
                config_path=ctx.config.config_path,
                asset=ctx.config.asset,
                interval_sec=int(ctx.config.interval_sec),
                rebuild_pack=False,
            )
            phase = 'post_bootstrap_practice'
            post_practice = build_practice_readiness_payload(
                repo_root=repo,
                config_path=ctx.config.config_path,
                max_stake_amount=max_stake_amount,
                soak_stale_after_sec=soak_stale_after_sec,
                write_artifact=True,
            )
            post_critical_issues = _critical_preflight_issues(post_practice)
            post_round_eligible = _round_eligible(post_practice, critical_issues=post_critical_issues)
            if not post_round_eligible:
                blocked_reason = 'critical_post_bootstrap_blockers' if post_critical_issues else 'practice_not_ready_after_bootstrap'
                phase = 'blocked_post_bootstrap'
            else:
                phase = 'ready'

    post_warn_names = _warn_names(post_practice)
    ready_for_practice_green = bool(post_practice.get('ready_for_practice')) and bool((post_practice.get('doctor') or {}).get('ready_for_practice'))
    round_eligible = blocked_reason is None and post_round_eligible

    if blocked_reason is not None:
        severity = 'error'
    elif ready_for_practice_green:
        severity = 'ok'
    else:
        severity = 'warn'

    recommended_next_steps: list[str] = []
    if blocked_reason == 'critical_preflight_blockers':
        recommended_next_steps.append('Corrigir os blockers estruturais do profile de practice antes de tentar bootstrap (conta PRACTICE, stake, limites, guard e escopo controlado).')
    elif blocked_reason == 'asset_prepare_failed':
        recommended_next_steps.append('Revisar o step asset_prepare; sem dataset/market_context frescos o doctor e o practice continuam bloqueados.')
    elif blocked_reason == 'runtime_soak_failed':
        recommended_next_steps.append('Revisar o soak e os artifacts de loop/health antes de insistir na prática controlada.')
    elif blocked_reason in {'practice_not_ready_after_bootstrap', 'critical_post_bootstrap_blockers'}:
        recommended_next_steps.append('Abrir practice.json e doctor.json gerados após o bootstrap para entender por que o scope ainda não ficou pronto.')
    elif 'intelligence_surface' in post_warn_names:
        recommended_next_steps.append('Bootstrap concluído, mas a practice ainda não ficou verde por avisos de intelligence; revise intelligence/retrain antes de considerar o scope totalmente pronto.')
    else:
        recommended_next_steps.append('Bootstrap concluído; confirme doctor/practice verdes e siga para practice-round para gerar a evidência operacional completa.')

    payload = {
        'at_utc': _now_iso(),
        'kind': 'practice_bootstrap',
        'ok': severity != 'error',
        'severity': severity,
        'ready_for_practice_green': ready_for_practice_green,
        'round_eligible': round_eligible,
        'phase': phase,
        'blocked_reason': blocked_reason,
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': ctx.scope.scope_tag,
        },
        'pre_practice': {
            'ready_for_practice': bool(pre_practice.get('ready_for_practice')),
            'round_eligible': pre_round_eligible,
            'severity': pre_practice.get('severity'),
            'critical_issues': critical_issues,
            'payload': pre_practice,
        },
        'asset_prepare': {
            'action': asset_prepare_action,
            'payload': asset_prepare_payload,
            'lookback_candles': int(lookback_candles),
        },
        'intelligence_refresh': {
            'after_prepare': intelligence_refresh_after_prepare,
            'after_soak': intelligence_refresh_after_soak,
        },
        'soak': {
            'action': soak_action,
            'requested_cycles': None if skip_soak else max(1, int(soak_cycles)),
            'summary': soak_summary,
        },
        'post_practice': {
            'ready_for_practice': bool(post_practice.get('ready_for_practice')),
            'round_eligible': post_round_eligible,
            'severity': post_practice.get('severity'),
            'critical_issues': post_critical_issues,
            'payload': post_practice,
        },
        'recommended_next_steps': recommended_next_steps,
    }

    report_files = _write_bootstrap_report_files(
        repo_root=repo,
        scope_tag=str(ctx.scope.scope_tag),
        payload=payload,
        at_utc=str(payload.get('at_utc') or _now_iso()),
    )
    payload['artifacts'] = {
        'control_path': str(repo / 'runs' / 'control' / ctx.scope.scope_tag / 'practice_bootstrap.json'),
        **report_files,
    }

    if write_artifact:
        write_control_artifact(
            repo_root=repo,
            asset=ctx.config.asset,
            interval_sec=ctx.config.interval_sec,
            name='practice_bootstrap',
            payload=payload,
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Prepare the controlled PRACTICE scope until doctor/practice can turn green.')
    parser.add_argument('--repo-root', default='.')
    parser.add_argument('--config', required=True)
    parser.add_argument('--lookback-candles', type=int, default=2000)
    parser.add_argument('--soak-cycles', type=int, default=3)
    parser.add_argument('--force-prepare', action='store_true')
    parser.add_argument('--force-soak', action='store_true')
    parser.add_argument('--skip-soak', action='store_true')
    parser.add_argument('--max-stake-amount', type=float, default=DEFAULT_MAX_STAKE)
    parser.add_argument('--soak-stale-after-sec', type=int, default=None)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    payload = build_practice_bootstrap_payload(
        repo_root=args.repo_root,
        config_path=args.config,
        lookback_candles=int(args.lookback_candles),
        soak_cycles=int(args.soak_cycles),
        force_prepare=bool(args.force_prepare),
        force_soak=bool(args.force_soak),
        skip_soak=bool(args.skip_soak),
        max_stake_amount=float(args.max_stake_amount),
        soak_stale_after_sec=args.soak_stale_after_sec,
        write_artifact=True,
    )
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str) if args.json else json.dumps(payload, ensure_ascii=False, default=str)
    print(text)
    return 0 if bool(payload.get('round_eligible')) else 2


__all__ = ['build_practice_bootstrap_payload', 'main', 'PRACTICE_BOOTSTRAP_CRITICAL_CHECKS']
