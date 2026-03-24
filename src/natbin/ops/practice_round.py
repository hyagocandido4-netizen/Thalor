from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..incidents.reporting import incident_report_payload
from ..runtime.soak import build_runtime_soak_summary
from ..intelligence.refresh import refresh_config_intelligence
from ..state.control_repo import write_control_artifact
from .live_validation import (
    ValidationResult,
    build_validation_plan,
    ensure_env,
    run_validation_step,
    summarize_results,
    write_validation_report,
)
from .practice_bootstrap import build_practice_bootstrap_payload
from .practice_readiness import DEFAULT_MAX_STAKE, build_practice_readiness_payload


PRACTICE_ROUND_CRITICAL_CHECKS = {
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


def _compact_result(item: ValidationResult) -> dict[str, Any]:
    out = {
        'name': item.name,
        'returncode': int(item.returncode),
        'duration_sec': float(item.duration_sec),
        'started_at_utc': item.started_at_utc,
        'finished_at_utc': item.finished_at_utc,
        'required': bool(item.required),
        'note': item.note,
        'potentially_submits': bool(item.potentially_submits),
        'payload': item.payload,
    }
    if item.stderr:
        out['stderr_tail'] = str(item.stderr).strip()[-500:]
    return out


def _result_map(results: list[ValidationResult]) -> dict[str, ValidationResult]:
    return {item.name: item for item in results}


def _reconcile_result_ok(reconcile_payload: dict[str, Any] | None) -> bool | None:
    if not isinstance(reconcile_payload, dict):
        return None
    summary = reconcile_payload.get('summary') if isinstance(reconcile_payload.get('summary'), dict) else None
    detail = reconcile_payload.get('detail') if isinstance(reconcile_payload.get('detail'), dict) else None
    if isinstance(summary, dict) and 'ok' in summary:
        return bool(summary.get('ok'))
    if isinstance(detail, dict) and str(detail.get('reason') or '') == 'no_pending_intents' and not list(detail.get('errors') or []):
        return True
    if not isinstance(summary, dict):
        return None
    errors = list(summary.get('errors') or [])
    if errors:
        return False
    try:
        pending_before = int(summary.get('pending_before') or 0)
    except Exception:
        pending_before = 0
    try:
        ambiguous = int(summary.get('ambiguous_matches') or 0)
    except Exception:
        ambiguous = 0
    try:
        new_orphans = int(summary.get('new_orphans') or 0)
    except Exception:
        new_orphans = 0
    if pending_before == 0 and ambiguous == 0 and new_orphans == 0:
        return True
    return True if ambiguous == 0 and new_orphans == 0 and not errors else False


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
    for name in sorted(PRACTICE_ROUND_CRITICAL_CHECKS):
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


def _write_round_report_files(*, repo_root: Path, scope_tag: str, payload: dict[str, Any], at_utc: str) -> dict[str, str]:
    base = repo_root / 'runs' / 'tests' / 'practice_rounds'
    base.mkdir(parents=True, exist_ok=True)
    stamp = str(at_utc or _now_iso()).replace(':', '').replace('-', '').replace('+00:00', 'Z')
    latest_path = base / f'practice_round_latest_{scope_tag}.json'
    report_path = base / f'practice_round_{stamp}_{scope_tag}.json'
    body = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    latest_path.write_text(body, encoding='utf-8')
    report_path.write_text(body, encoding='utf-8')
    return {
        'latest_report_path': str(latest_path),
        'report_path': str(report_path),
    }


def _observe_summary(results: list[ValidationResult]) -> dict[str, Any]:
    by_name = _result_map(results)
    observe_payload = (by_name.get('observe_once_practice_live').payload if by_name.get('observe_once_practice_live') else None) or {}
    orders_payload = (by_name.get('orders_after_practice').payload if by_name.get('orders_after_practice') else None) or {}
    reconcile_payload = (by_name.get('reconcile_after_practice').payload if by_name.get('reconcile_after_practice') else None) or {}
    incidents_payload = (by_name.get('incidents_after_practice').payload if by_name.get('incidents_after_practice') else None) or {}

    latest_intent = observe_payload.get('latest_intent') if isinstance(observe_payload, dict) else None
    if not isinstance(latest_intent, dict):
        recent = list((orders_payload.get('recent_intents') or []) if isinstance(orders_payload, dict) else [])
        latest_intent = recent[0] if recent else None

    submit_attempt = observe_payload.get('submit_attempt') if isinstance(observe_payload, dict) else None
    exec_summary = observe_payload.get('execution_summary') if isinstance(observe_payload, dict) else None
    if not isinstance(exec_summary, dict):
        exec_summary = orders_payload.get('summary') if isinstance(orders_payload, dict) else None

    reconcile_summary = reconcile_payload.get('summary') if isinstance(reconcile_payload, dict) else None
    incidents_severity = incidents_payload.get('severity') if isinstance(incidents_payload, dict) else None

    return {
        'intent_created': bool((observe_payload or {}).get('intent_created')) if isinstance(observe_payload, dict) else False,
        'blocked_reason': (observe_payload or {}).get('blocked_reason') if isinstance(observe_payload, dict) else None,
        'latest_intent_state': (latest_intent or {}).get('intent_state') if isinstance(latest_intent, dict) else None,
        'latest_intent_id': (latest_intent or {}).get('intent_id') if isinstance(latest_intent, dict) else None,
        'submit_transport_status': (submit_attempt or {}).get('transport_status') if isinstance(submit_attempt, dict) else None,
        'external_order_id': (submit_attempt or {}).get('external_order_id') if isinstance(submit_attempt, dict) else None,
        'execution_summary': exec_summary if isinstance(exec_summary, dict) else None,
        'reconcile_ok': _reconcile_result_ok(reconcile_payload if isinstance(reconcile_payload, dict) else None),
        'reconcile_summary': reconcile_summary if isinstance(reconcile_summary, dict) else None,
        'incidents_severity': str(incidents_severity or 'ok'),
        'no_trade_is_not_error': True,
    }


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


def build_practice_round_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    python_exe: str | None = None,
    soak_cycles: int = 3,
    force_soak: bool = False,
    skip_soak: bool = False,
    max_stake_amount: float = DEFAULT_MAX_STAKE,
    soak_stale_after_sec: int | None = None,
    force_send_alerts: bool = False,
    incident_limit: int = 20,
    window_hours: int = 24,
    write_artifact: bool = True,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    py = str(python_exe or sys.executable)

    bootstrap = build_practice_bootstrap_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        soak_cycles=int(soak_cycles),
        force_prepare=False,
        force_soak=bool(force_soak),
        skip_soak=bool(skip_soak),
        max_stake_amount=max_stake_amount,
        soak_stale_after_sec=soak_stale_after_sec,
        write_artifact=True,
    )

    pre_practice = dict((bootstrap.get('pre_practice') or {}).get('payload') or {})
    critical_issues = list((bootstrap.get('pre_practice') or {}).get('critical_issues') or [])
    pre_round_eligible = bool((bootstrap.get('pre_practice') or {}).get('round_eligible'))

    validation_results: list[ValidationResult] = []
    validation_report_path: str | None = None
    validation_summary: dict[str, Any] | None = None
    validation_required_passed = False
    incident_report: dict[str, Any] | None = None

    soak_summary = dict((bootstrap.get('soak') or {}).get('summary') or {}) if isinstance((bootstrap.get('soak') or {}).get('summary'), dict) else (bootstrap.get('soak') or {}).get('summary')
    soak_action = str((bootstrap.get('soak') or {}).get('action') or 'skipped')
    intelligence_refresh_after_soak = (bootstrap.get('intelligence_refresh') or {}).get('after_soak')
    intelligence_refresh_after_validation: dict[str, Any] | None = None
    post_practice = dict((bootstrap.get('post_practice') or {}).get('payload') or {})
    post_critical_issues = list((bootstrap.get('post_practice') or {}).get('critical_issues') or [])
    post_round_eligible = bool((bootstrap.get('post_practice') or {}).get('round_eligible'))
    blocked_reason: str | None = (bootstrap.get('blocked_reason') or None) if not bool(bootstrap.get('round_eligible')) else None
    phase = str(bootstrap.get('phase') or 'bootstrap')

    if blocked_reason is None and bool(bootstrap.get('round_eligible')):
        phase = 'validation_practice'
        plan = build_validation_plan(
            stage='practice',
            repo_root=repo,
            config_path=str(ctx.config.config_path),
            asset=str(ctx.config.asset),
            interval_sec=int(ctx.config.interval_sec),
            include_baseline_tests=False,
            force_send_alerts=bool(force_send_alerts),
        )
        env = ensure_env(repo)
        for spec in plan.specs:
            result = run_validation_step(py, spec, repo, env)
            validation_results.append(result)
            if result.returncode != 0 and spec.required:
                break
        report_path_obj = write_validation_report(
            repo_root=repo,
            plan=plan,
            python_exe=py,
            results=validation_results,
            allow_live_submit=False,
            ack_live=None,
        )
        validation_report_path = str(report_path_obj)
        validation_summary = summarize_results(validation_results)
        validation_required_passed = validation_summary.get('failed_required', 0) == 0
        intelligence_refresh_after_validation = _refresh_intelligence_state(
            repo_root=repo,
            config_path=ctx.config.config_path,
            asset=ctx.config.asset,
            interval_sec=int(ctx.config.interval_sec),
            rebuild_pack=False,
        )
        phase = 'post_round'
        post_practice = build_practice_readiness_payload(
            repo_root=repo,
            config_path=ctx.config.config_path,
            max_stake_amount=max_stake_amount,
            soak_stale_after_sec=soak_stale_after_sec,
            write_artifact=True,
        )
        post_critical_issues = _critical_preflight_issues(post_practice)
        post_round_eligible = _round_eligible(post_practice, critical_issues=post_critical_issues)
        incident_report = incident_report_payload(
            repo_root=repo,
            config_path=ctx.config.config_path,
            limit=int(incident_limit),
            window_hours=int(window_hours),
            write_artifact=True,
            stage='practice',
        )
    elif blocked_reason is not None:
        phase = 'blocked_bootstrap'

    observe = _observe_summary(validation_results)
    incident_severity = str((incident_report or {}).get('severity') or 'ok')
    post_warn_names = _warn_names(post_practice)
    intelligence_summary = dict((post_practice.get('intelligence') or {}).get('summary') or {}) if isinstance(post_practice.get('intelligence'), dict) else {}
    feedback_blocked = bool(intelligence_summary.get('portfolio_feedback_blocked'))

    severity = 'ok'
    if critical_issues or post_critical_issues or blocked_reason or not bool(post_practice.get('ok')) or not validation_required_passed:
        severity = 'error'
    elif _status_level(str(post_practice.get('severity') or 'ok')) >= 1 or _status_level(incident_severity) >= 1:
        severity = 'warn'

    round_ok = blocked_reason is None and bool(post_practice.get('ok')) and not post_critical_issues and validation_required_passed

    recommended_next_steps: list[str] = []
    if critical_issues:
        recommended_next_steps.append('Corrigir os blockers estruturais do READY-1 antes de tentar soak/observe em PRACTICE.')
    elif blocked_reason in {'critical_post_soak_blockers', 'critical_post_bootstrap_blockers'}:
        recommended_next_steps.append('Revisar practice.json: o scope ficou operacionalmente válido, mas ainda há checks críticos em aviso/erro após o bootstrap.')
    elif blocked_reason in {'practice_not_ready_after_soak', 'practice_not_ready_after_bootstrap'}:
        recommended_next_steps.append('Revisar practice.json e production_doctor para entender por que o scope ainda não ficou pronto após o bootstrap.')
    elif not validation_required_passed:
        recommended_next_steps.append('Abrir o report de controlled_live_validation e corrigir o primeiro passo obrigatório que falhou.')
    elif feedback_blocked:
        recommended_next_steps.append('Rodada operacional concluída, mas o scope segue em no-trade por gate de intelligence/regime; o próximo passo é tratar o retrain recomendado antes de insistir em execução.')
    elif 'intelligence_surface' in post_warn_names:
        recommended_next_steps.append('Rodada operacional concluída com avisos de intelligence; revisar practice.json / intelligence.json antes de insistir em execução.')
    elif _status_level(incident_severity) >= 1:
        recommended_next_steps.append('Revisar o incident_report gerado antes de repetir a rodada de PRACTICE.')
    else:
        recommended_next_steps.append('Se quiser repetir a evidência, rode novamente o practice round na mesma scope; se a estabilidade persistir, o próximo trilho é o preflight controlado em REAL com drain ligado.')

    payload = {
        'at_utc': _now_iso(),
        'kind': 'practice_round',
        'ok': severity != 'error',
        'severity': severity,
        'round_ok': round_ok,
        'phase': phase,
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': ctx.scope.scope_tag,
        },
        'bootstrap': bootstrap,
        'blocked_reason': blocked_reason,
        'pre_practice': {
            'ready_for_practice': bool(pre_practice.get('ready_for_practice')),
            'round_eligible': pre_round_eligible,
            'severity': pre_practice.get('severity'),
            'soak': pre_practice.get('soak'),
            'critical_issues': critical_issues,
            'payload': pre_practice,
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
            'soak': post_practice.get('soak'),
            'critical_issues': post_critical_issues,
            'payload': post_practice,
        },
        'intelligence_refresh': {
            'after_soak': intelligence_refresh_after_soak,
            'after_validation': intelligence_refresh_after_validation,
        },
        'validation': {
            'stage': 'practice',
            'summary': validation_summary,
            'required_passed': bool(validation_required_passed),
            'report_path': validation_report_path,
            'results': [_compact_result(item) for item in validation_results],
            'observe': observe,
        },
        'incident_report': {
            'severity': (incident_report or {}).get('severity') if isinstance(incident_report, dict) else None,
            'ok': (incident_report or {}).get('ok') if isinstance(incident_report, dict) else None,
            'artifacts': (incident_report or {}).get('artifacts') if isinstance(incident_report, dict) else None,
            'recommended_actions': (incident_report or {}).get('recommended_actions') if isinstance(incident_report, dict) else None,
        },
        'recommended_next_steps': recommended_next_steps,
    }

    report_files = _write_round_report_files(repo_root=repo, scope_tag=str(ctx.scope.scope_tag), payload=payload, at_utc=str(payload.get('at_utc') or _now_iso()))
    payload['artifacts'] = {
        'control_path': str(repo / 'runs' / 'control' / ctx.scope.scope_tag / 'practice_round.json'),
        'validation_report_path': validation_report_path,
        **report_files,
    }

    if write_artifact:
        write_control_artifact(
            repo_root=repo,
            asset=ctx.config.asset,
            interval_sec=ctx.config.interval_sec,
            name='practice_round',
            payload=payload,
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description='Run the controlled operational round in PRACTICE mode.')
    parser.add_argument('--repo-root', default='.', help='Repository root path.')
    parser.add_argument('--config', required=True, help='Config YAML for the practice profile.')
    parser.add_argument('--python', dest='python_exe', default=sys.executable, help='Python executable used for validation subprocesses.')
    parser.add_argument('--soak-cycles', type=int, default=3, help='Number of runtime soak cycles when a fresh soak is required.')
    parser.add_argument('--force-soak', action='store_true', help='Force a new runtime soak even if the latest soak is fresh.')
    parser.add_argument('--skip-soak', action='store_true', help='Do not run a soak automatically before the practice round.')
    parser.add_argument('--max-stake-amount', type=float, default=DEFAULT_MAX_STAKE, help='Recommended max stake for practice gating.')
    parser.add_argument('--soak-stale-after-sec', type=int, default=None, help='Override freshness window for the READY-1 soak gate.')
    parser.add_argument('--force-send-alerts', action='store_true', help='For the optional alerts step, actually send instead of queue-only where supported.')
    parser.add_argument('--incident-limit', type=int, default=20)
    parser.add_argument('--window-hours', type=int, default=24)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    payload = build_practice_round_payload(
        repo_root=args.repo_root,
        config_path=args.config,
        python_exe=args.python_exe,
        soak_cycles=int(args.soak_cycles),
        force_soak=bool(args.force_soak),
        skip_soak=bool(args.skip_soak),
        max_stake_amount=float(args.max_stake_amount),
        soak_stale_after_sec=args.soak_stale_after_sec,
        force_send_alerts=bool(args.force_send_alerts),
        incident_limit=int(args.incident_limit),
        window_hours=int(args.window_hours),
        write_artifact=True,
    )
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str) if args.json else json.dumps(payload, ensure_ascii=False, default=str)
    print(text)
    return 0 if bool(payload.get('round_ok')) else 2


__all__ = ['build_practice_round_payload', 'main', 'PRACTICE_ROUND_CRITICAL_CHECKS']
