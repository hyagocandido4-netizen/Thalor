from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

from .commands import (
    asset_candidate_payload,
    asset_prepare_payload,
    alerts_flush_payload,
    alerts_payload,
    alerts_release_payload,
    alerts_test_payload,
    backup_payload,
    check_order_status_payload,
    doctor_payload,
    execute_order_payload,
    incidents_alert_payload,
    incidents_drill_payload,
    incidents_payload,
    incidents_report_payload,
    intelligence_payload,
    intelligence_refresh_payload,
    monte_carlo_payload,
    health_payload,
    healthcheck_payload,
    observe_payload,
    practice_payload,
    practice_bootstrap_payload,
    practice_round_payload,
    retrain_run_payload,
    retrain_status_payload,
    orders_payload,
    plan_payload,
    portfolio_observe_payload,
    portfolio_plan_payload,
    portfolio_status_payload,
    precheck_payload,
    protection_payload,
    quota_payload,
    reconcile_payload,
    release_payload,
    retention_payload,
    security_payload,
    status_payload,
    sync_payload,
)
from .models import ObserveRequest


def _parse_now_utc(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except Exception as exc:  # pragma: no cover - CLI guard
        raise SystemExit(f'invalid --now-utc: {s!r} ({exc})') from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _print(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(json.dumps(payload, ensure_ascii=False, default=str))


def _common_repo_root(ns) -> str:
    local = getattr(ns, 'repo_root', None)
    global_v = getattr(ns, 'global_repo_root', None)
    if local not in (None, '.', ''):
        return str(local)
    if global_v not in (None, '', '.'): 
        return str(global_v)
    return '.'


def _common_config(ns) -> str | None:
    local = getattr(ns, 'config', None)
    global_v = getattr(ns, 'global_config', None)
    if local not in (None, ''):
        return str(local)
    if global_v not in (None, ''):
        return str(global_v)
    return None


def _build_parser() -> argparse.ArgumentParser:
    def _add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument('--repo-root', default='.', help='Repository root')
        sp.add_argument('--config', default=None, help='Optional path to config/base.yaml or config.yaml')

    p = argparse.ArgumentParser(description='Thalor Package M control plane')
    p.add_argument('--repo-root', dest='global_repo_root', default='.', help='Repository root')
    p.add_argument('--config', dest='global_config', default=None, help='Optional path to config/base.yaml or config.yaml')
    sub = p.add_subparsers(dest='command')

    sp_status = sub.add_parser('status', help='Describe current control-plane state')
    _add_common(sp_status)
    sp_status.add_argument('--json', action='store_true')
    sp_status.add_argument('--topk', type=int, default=3)

    sp_plan = sub.add_parser('plan', help='Emit the canonical auto-cycle plan')
    _add_common(sp_plan)
    sp_plan.add_argument('--json', action='store_true')
    sp_plan.add_argument('--topk', type=int, default=3)
    sp_plan.add_argument('--lookback-candles', type=int, default=2000)

    sp_quota = sub.add_parser('quota', help='Emit the current quota snapshot')
    _add_common(sp_quota)
    sp_quota.add_argument('--json', action='store_true')
    sp_quota.add_argument('--topk', type=int, default=3)
    sp_quota.add_argument('--sleep-align-offset-sec', type=int, default=3)
    sp_quota.add_argument('--now-utc', default=None)

    sp_pre = sub.add_parser('precheck', help='Evaluate runtime failsafe + quota precheck')
    _add_common(sp_pre)
    sp_pre.add_argument('--json', action='store_true')
    sp_pre.add_argument('--topk', type=int, default=3)
    sp_pre.add_argument('--sleep-align-offset-sec', type=int, default=3)
    sp_pre.add_argument('--now-utc', default=None)
    sp_pre.add_argument('--enforce-market-context', action='store_true')

    sp_healthcheck = sub.add_parser('healthcheck', help='Container/VPS healthcheck payload with Docker-friendly exit code')
    _add_common(sp_healthcheck)
    sp_healthcheck.add_argument('--json', action='store_true')

    sp_backup = sub.add_parser('backup', help='Create a production backup archive for runs/logs/databases')
    _add_common(sp_backup)
    sp_backup.add_argument('--json', action='store_true')
    sp_backup.add_argument('--dry-run', action='store_true')

    sp_health = sub.add_parser('health', help='Emit health payload')
    _add_common(sp_health)
    sp_health.add_argument('--json', action='store_true')
    sp_health.add_argument('--topk', type=int, default=3)

    sp_security = sub.add_parser('security', help='Emit security / secret posture payload')
    _add_common(sp_security)
    sp_security.add_argument('--json', action='store_true')

    sp_protection = sub.add_parser('protection', help='Emit account protection / responsible pacing payload')
    _add_common(sp_protection)
    sp_protection.add_argument('--json', action='store_true')

    sp_monte_carlo = sub.add_parser('monte-carlo', aliases=['monte_carlo'], help='Run realistic Monte Carlo projection from historical realized trades')
    _add_common(sp_monte_carlo)
    sp_monte_carlo.add_argument('--json', action='store_true')
    sp_monte_carlo.add_argument('--initial-capital-brl', type=float, default=None)
    sp_monte_carlo.add_argument('--horizon-days', type=int, default=None)
    sp_monte_carlo.add_argument('--trials', type=int, default=None)
    sp_monte_carlo.add_argument('--rng-seed', type=int, default=None)

    sp_sync = sub.add_parser('sync', help='Canonicalize / compare the current repo workspace state (SYNC-1)')
    _add_common(sp_sync)
    sp_sync.add_argument('--json', action='store_true')
    sp_sync.add_argument('--base-ref', default='origin/main')
    sp_sync.add_argument('--write-manifest', action='store_true')
    sp_sync.add_argument('--manifest-json-path', default=None)
    sp_sync.add_argument('--manifest-md-path', default=None)
    sp_sync.add_argument('--strict-clean', action='store_true')
    sp_sync.add_argument('--strict-base-ref', action='store_true')
    sp_sync.add_argument('--freeze-docs', action='store_true')
    sp_sync.add_argument('--strict', action='store_true')

    sp_intelligence = sub.add_parser('intelligence', help='Emit per-scope intelligence operational surface')
    _add_common(sp_intelligence)
    sp_intelligence.add_argument('--json', action='store_true')

    sp_intelligence_refresh = sub.add_parser('intelligence-refresh', help='Rebuild/evaluate intelligence artifacts for the current config/profile')
    _add_common(sp_intelligence_refresh)
    sp_intelligence_refresh.add_argument('--json', action='store_true')
    sp_intelligence_refresh.add_argument('--asset', default=None)
    sp_intelligence_refresh.add_argument('--interval-sec', type=int, default=None)
    sp_intelligence_refresh.add_argument('--no-rebuild-pack', action='store_true')
    sp_intelligence_refresh.add_argument('--no-materialize-portfolio', action='store_true')

    sp_doctor = sub.add_parser('doctor', help='Emit H9 production doctor / live-readiness payload')
    _add_common(sp_doctor)
    sp_doctor.add_argument('--json', action='store_true')
    sp_doctor.add_argument('--probe-broker', action='store_true')
    sp_doctor.add_argument('--relaxed', action='store_true')
    sp_doctor.add_argument('--market-context-max-age-sec', type=int, default=None)
    sp_doctor.add_argument('--min-dataset-rows', type=int, default=100)

    sp_retention = sub.add_parser('retention', help='Preview/apply runtime artifact retention (H9)')
    _add_common(sp_retention)
    sp_retention.add_argument('--json', action='store_true')
    sp_retention.add_argument('--apply', action='store_true')
    sp_retention.add_argument('--days', type=int, default=None)
    sp_retention.add_argument('--keep-effective-config-snapshots', type=int, default=20)
    sp_retention.add_argument('--list-limit', type=int, default=50)

    sp_release = sub.add_parser('release', help='Emit M7 release readiness / production checklist payload')
    _add_common(sp_release)
    sp_release.add_argument('--json', action='store_true')

    sp_practice = sub.add_parser('practice', help='Emit READY-1 controlled practice readiness payload')
    _add_common(sp_practice)
    sp_practice.add_argument('--json', action='store_true')
    sp_practice.add_argument('--max-stake-amount', type=float, default=5.0)
    sp_practice.add_argument('--soak-stale-after-sec', type=int, default=None)

    sp_practice_bootstrap = sub.add_parser('practice-bootstrap', help='Bootstrap the controlled PRACTICE scope until doctor/practice can turn green')
    _add_common(sp_practice_bootstrap)
    sp_practice_bootstrap.add_argument('--json', action='store_true')
    sp_practice_bootstrap.add_argument('--lookback-candles', type=int, default=2000)
    sp_practice_bootstrap.add_argument('--soak-cycles', type=int, default=3)
    sp_practice_bootstrap.add_argument('--force-prepare', action='store_true')
    sp_practice_bootstrap.add_argument('--force-soak', action='store_true')
    sp_practice_bootstrap.add_argument('--skip-soak', action='store_true')
    sp_practice_bootstrap.add_argument('--max-stake-amount', type=float, default=5.0)
    sp_practice_bootstrap.add_argument('--soak-stale-after-sec', type=int, default=None)

    sp_practice_round = sub.add_parser('practice-round', help='Run the controlled operational round in PRACTICE mode')
    _add_common(sp_practice_round)
    sp_practice_round.add_argument('--json', action='store_true')
    sp_practice_round.add_argument('--soak-cycles', type=int, default=3)
    sp_practice_round.add_argument('--force-soak', action='store_true')
    sp_practice_round.add_argument('--skip-soak', action='store_true')
    sp_practice_round.add_argument('--max-stake-amount', type=float, default=5.0)
    sp_practice_round.add_argument('--soak-stale-after-sec', type=int, default=None)
    sp_practice_round.add_argument('--force-send-alerts', action='store_true')
    sp_practice_round.add_argument('--incident-limit', type=int, default=20)
    sp_practice_round.add_argument('--window-hours', type=int, default=24)

    sp_incidents = sub.add_parser('incidents', help='Incident status / report / alert / drill (M7.1)')
    _add_common(sp_incidents)
    incidents_sub = sp_incidents.add_subparsers(dest='incidents_cmd', required=True)

    sp_i_status = incidents_sub.add_parser('status', help='Show current incident posture / recent incident feed')
    _add_common(sp_i_status)
    sp_i_status.add_argument('--json', action='store_true')
    sp_i_status.add_argument('--limit', type=int, default=20)
    sp_i_status.add_argument('--window-hours', type=int, default=24)

    sp_i_report = incidents_sub.add_parser('report', help='Build an incident report artifact for the current scope')
    _add_common(sp_i_report)
    sp_i_report.add_argument('--json', action='store_true')
    sp_i_report.add_argument('--limit', type=int, default=20)
    sp_i_report.add_argument('--window-hours', type=int, default=24)

    sp_i_alert = incidents_sub.add_parser('alert', help='Queue/send a Telegram summary for current incidents')
    _add_common(sp_i_alert)
    sp_i_alert.add_argument('--json', action='store_true')
    sp_i_alert.add_argument('--limit', type=int, default=20)
    sp_i_alert.add_argument('--window-hours', type=int, default=24)
    sp_i_alert.add_argument('--force-send', action='store_true')

    sp_i_drill = incidents_sub.add_parser('drill', help='Emit a no-side-effect incident drill checklist')
    _add_common(sp_i_drill)
    sp_i_drill.add_argument('--json', action='store_true')
    sp_i_drill.add_argument('--scenario', default='broker_down', choices=['broker_down', 'db_lock', 'market_context_stale', 'alert_queue'])

    sp_alerts = sub.add_parser('alerts', help='Telegram / alerting operations (M7)')
    _add_common(sp_alerts)
    alerts_sub = sp_alerts.add_subparsers(dest='alerts_cmd', required=True)

    sp_alerts_status = alerts_sub.add_parser('status', help='Show alerting status / recent outbox')
    _add_common(sp_alerts_status)
    sp_alerts_status.add_argument('--json', action='store_true')
    sp_alerts_status.add_argument('--limit', type=int, default=20)

    sp_alerts_test = alerts_sub.add_parser('test', help='Emit a test Telegram alert (queued or sent)')
    _add_common(sp_alerts_test)
    sp_alerts_test.add_argument('--json', action='store_true')
    sp_alerts_test.add_argument('--force-send', action='store_true')

    sp_alerts_release = alerts_sub.add_parser('release', help='Emit a release-readiness Telegram alert (queued or sent)')
    _add_common(sp_alerts_release)
    sp_alerts_release.add_argument('--json', action='store_true')
    sp_alerts_release.add_argument('--force-send', action='store_true')

    sp_alerts_flush = alerts_sub.add_parser('flush', help='Retry queued/failed Telegram alerts')
    _add_common(sp_alerts_flush)
    sp_alerts_flush.add_argument('--json', action='store_true')
    sp_alerts_flush.add_argument('--limit', type=int, default=20)

    sp_orders = sub.add_parser('orders', help='Inspect Package N execution intents')
    _add_common(sp_orders)
    sp_orders.add_argument('--json', action='store_true')
    sp_orders.add_argument('--limit', type=int, default=20)

    sp_execute_order = sub.add_parser('execute-order', aliases=['execute_order'], help='Create/submit one order from the latest trade signal (safe by default: PRACTICE unless explicitly configured otherwise)')
    _add_common(sp_execute_order)
    sp_execute_order.add_argument('--json', action='store_true')

    sp_check_order_status = sub.add_parser('check-order-status', aliases=['check_order_status'], help='Refresh and inspect one broker order snapshot by external order id')
    _add_common(sp_check_order_status)
    sp_check_order_status.add_argument('--json', action='store_true')
    sp_check_order_status.add_argument('--external-order-id', required=True)
    sp_check_order_status.add_argument('--no-refresh', action='store_true')

    sp_reconcile = sub.add_parser('reconcile', help='Run Package N reconciliation now')
    _add_common(sp_reconcile)
    sp_reconcile.add_argument('--json', action='store_true')


    sp_retrain = sub.add_parser('retrain', help='Retrain operations / review for the current scope(s)')
    _add_common(sp_retrain)
    retrain_sub = sp_retrain.add_subparsers(dest='retrain_cmd', required=True)

    sp_retrain_status = retrain_sub.add_parser('status', help='Show retrain plan/status/review for the current scope(s)')
    _add_common(sp_retrain_status)
    sp_retrain_status.add_argument('--json', action='store_true')
    sp_retrain_status.add_argument('--asset', default=None)
    sp_retrain_status.add_argument('--interval-sec', type=int, default=None)

    sp_retrain_run = retrain_sub.add_parser('run', help='Execute a scoped retrain/review cycle')
    _add_common(sp_retrain_run)
    sp_retrain_run.add_argument('--json', action='store_true')
    sp_retrain_run.add_argument('--asset', default=None)
    sp_retrain_run.add_argument('--interval-sec', type=int, default=None)
    sp_retrain_run.add_argument('--force', action='store_true')
    sp_retrain_run.add_argument('--promote-threshold', type=float, default=0.5)

    sp_observe = sub.add_parser('observe', help='Run the runtime cycle via the control plane')
    _add_common(sp_observe)
    sp_observe.add_argument('--json', action='store_true')
    sp_observe.add_argument('--once', action='store_true')
    sp_observe.add_argument('--max-cycles', type=int, default=None)
    sp_observe.add_argument('--topk', type=int, default=3)
    sp_observe.add_argument('--lookback-candles', type=int, default=2000)
    sp_observe.add_argument('--sleep-align-offset-sec', type=int, default=3)
    sp_observe.add_argument('--quota-aware-sleep', action='store_true')
    sp_observe.add_argument('--precheck-market-context', action='store_true')
    sp_observe.add_argument('--no-stop-on-failure', action='store_true')

    # --- Package O: portfolio control plane ---
    sp_portfolio = sub.add_parser('portfolio', help='Multi-asset portfolio runtime')
    _add_common(sp_portfolio)
    psub = sp_portfolio.add_subparsers(dest='portfolio_cmd', required=True)

    sp_p_status = psub.add_parser('status', help='Describe portfolio state/scopes')
    _add_common(sp_p_status)
    sp_p_status.add_argument('--json', action='store_true')

    sp_p_plan = psub.add_parser('plan', help='Emit portfolio plan (phases + scopes)')
    _add_common(sp_p_plan)
    sp_p_plan.add_argument('--json', action='store_true')

    sp_p_observe = psub.add_parser('observe', help='Run portfolio observe loop')
    _add_common(sp_p_observe)
    sp_p_observe.add_argument('--json', action='store_true')
    sp_p_observe.add_argument('--once', action='store_true')
    sp_p_observe.add_argument('--max-cycles', type=int, default=None)
    sp_p_observe.add_argument('--topk', type=int, default=3)
    sp_p_observe.add_argument('--lookback-candles', type=int, default=2000)
    sp_p_observe.add_argument('--quota-aware-sleep', action='store_true')
    sp_p_observe.add_argument('--precheck-market-context', action='store_true')
    sp_p_observe.add_argument('--no-stop-on-failure', action='store_true')

    # --- Package O: single-asset helpers (useful for debugging portfolio) ---
    sp_asset = sub.add_parser('asset', help='Per-asset helpers used by the portfolio runtime')
    _add_common(sp_asset)
    asub = sp_asset.add_subparsers(dest='asset_cmd', required=True)

    sp_a_prepare = asub.add_parser('prepare', help='Prepare one asset scope (collect + dataset + market_context)')
    _add_common(sp_a_prepare)
    sp_a_prepare.add_argument('--json', action='store_true')
    sp_a_prepare.add_argument('--asset', required=True)
    sp_a_prepare.add_argument('--interval-sec', type=int, default=300)
    sp_a_prepare.add_argument('--lookback-candles', type=int, default=2000)

    sp_a_candidate = asub.add_parser('candidate', help='Run observe_once for one scope (execution disabled)')
    _add_common(sp_a_candidate)
    sp_a_candidate.add_argument('--json', action='store_true')
    sp_a_candidate.add_argument('--asset', required=True)
    sp_a_candidate.add_argument('--interval-sec', type=int, default=300)
    sp_a_candidate.add_argument('--topk', type=int, default=3)
    sp_a_candidate.add_argument('--lookback-candles', type=int, default=2000)

    # --- Package P: operations / live controls ---
    sp_ops = sub.add_parser('ops', help='Production operations (kill-switch, drain mode)')
    _add_common(sp_ops)
    ops_sub = sp_ops.add_subparsers(dest='ops_cmd', required=True)

    sp_ks = ops_sub.add_parser('killswitch', help='Kill-switch gate (blocks new trades)')
    ks_sub = sp_ks.add_subparsers(dest='op', required=True)
    ks_sub.add_parser('status', help='Show status')
    ks_on = ks_sub.add_parser('on', help='Enable kill-switch')
    ks_on.add_argument('--reason', default=None)
    ks_off = ks_sub.add_parser('off', help='Disable kill-switch')
    ks_off.add_argument('--reason', default=None)

    sp_dr = ops_sub.add_parser('drain', help='Drain mode (reconcile ok, no new submits)')
    dr_sub = sp_dr.add_subparsers(dest='op', required=True)
    dr_sub.add_parser('status', help='Show status')
    dr_on = dr_sub.add_parser('on', help='Enable drain mode')
    dr_on.add_argument('--reason', default=None)
    dr_off = dr_sub.add_parser('off', help='Disable drain mode')
    dr_off.add_argument('--reason', default=None)

    return p


def main(argv: list[str] | None = None) -> int:
    known = {'status', 'plan', 'quota', 'precheck', 'health', 'healthcheck', 'backup', 'security', 'protection', 'monte-carlo', 'monte_carlo', 'sync', 'intelligence', 'doctor', 'retention', 'release', 'practice', 'practice-bootstrap', 'practice-round', 'retrain', 'incidents', 'alerts', 'observe', 'orders', 'execute-order', 'execute_order', 'check-order-status', 'check_order_status', 'reconcile', 'portfolio', 'asset', 'ops'}
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        raw = ['status']
    elif raw[0] not in known and not any(arg in known for arg in raw):
        prefix: list[str] = []
        i = 0
        while i < len(raw):
            arg = raw[i]
            if arg in {'--repo-root', '--config'}:
                prefix.append(arg)
                if i + 1 < len(raw):
                    prefix.append(raw[i + 1])
                    i += 2
                    continue
            if arg.startswith('--'):
                break
            break
        raw = [*prefix, 'status', *raw[len(prefix):]]
    ns = _build_parser().parse_args(raw)

    if ns.command == 'status':
        payload = status_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), topk=ns.topk)
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command == 'plan':
        payload = plan_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            topk=ns.topk,
            lookback_candles=ns.lookback_candles,
        )
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command == 'quota':
        payload = quota_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            topk=ns.topk,
            sleep_align_offset_sec=ns.sleep_align_offset_sec,
            now_utc=_parse_now_utc(ns.now_utc),
        )
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command == 'precheck':
        payload = precheck_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            topk=ns.topk,
            sleep_align_offset_sec=ns.sleep_align_offset_sec,
            now_utc=_parse_now_utc(ns.now_utc),
            enforce_market_context=bool(ns.enforce_market_context),
        )
        _print(payload, as_json=bool(ns.json))
        return 0 if not payload.get('blocked') else 2

    if ns.command == 'healthcheck':
        payload = healthcheck_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('ok')) else 2

    if ns.command == 'backup':
        payload = backup_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), dry_run=bool(getattr(ns, 'dry_run', False)))
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('ok')) else 2

    if ns.command == 'health':
        payload = health_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), topk=ns.topk)
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command == 'security':
        payload = security_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command == 'protection':
        payload = protection_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('allowed', True)) else 2

    if ns.command == 'monte-carlo':
        payload = monte_carlo_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            initial_capital_brl=getattr(ns, 'initial_capital_brl', None),
            horizon_days=getattr(ns, 'horizon_days', None),
            trials=getattr(ns, 'trials', None),
            rng_seed=getattr(ns, 'rng_seed', None),
        )
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('ok')) else 2

    if ns.command == 'sync':
        legacy_sync_mode = (
            not bool(getattr(ns, 'freeze_docs', False))
            and not bool(getattr(ns, 'strict', False))
            and (
                bool(getattr(ns, 'write_manifest', False))
                or getattr(ns, 'manifest_json_path', None) not in (None, '')
                or getattr(ns, 'manifest_md_path', None) not in (None, '')
                or bool(getattr(ns, 'strict_clean', False))
                or bool(getattr(ns, 'strict_base_ref', False))
                or str(getattr(ns, 'base_ref', 'origin/main') or 'origin/main') not in {'', 'origin/main'}
            )
        )
        payload = sync_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            base_ref=str(getattr(ns, 'base_ref', 'origin/main') or 'origin/main'),
            write_manifest=bool(getattr(ns, 'write_manifest', False)),
            manifest_json_path=getattr(ns, 'manifest_json_path', None),
            manifest_md_path=getattr(ns, 'manifest_md_path', None),
            freeze_docs=bool(getattr(ns, 'freeze_docs', False)),
            strict=bool(getattr(ns, 'strict', False)),
            use_legacy_repo_sync=legacy_sync_mode,
        )
        _print(payload, as_json=bool(ns.json))
        if legacy_sync_mode:
            if bool(getattr(ns, 'strict_base_ref', False)) and not bool((payload.get('base_ref') or {}).get('exists')):
                return 2
            if bool(getattr(ns, 'strict_clean', False)) and str(payload.get('status')) not in {'clean', 'no_git'}:
                return 2
        return 0 if bool(payload.get('ok', True)) else 2

    if ns.command == 'intelligence':
        payload = intelligence_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('ok', True)) else 2

    if ns.command == 'intelligence-refresh':
        payload = intelligence_refresh_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            asset=getattr(ns, 'asset', None),
            interval_sec=getattr(ns, 'interval_sec', None),
            rebuild_pack=not bool(getattr(ns, 'no_rebuild_pack', False)),
            materialize_portfolio=not bool(getattr(ns, 'no_materialize_portfolio', False)),
        )
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('ok', True)) else 2

    if ns.command == 'doctor':
        payload = doctor_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            probe_broker=bool(getattr(ns, 'probe_broker', False)),
            relaxed=bool(getattr(ns, 'relaxed', False)),
            market_context_max_age_sec=getattr(ns, 'market_context_max_age_sec', None),
            min_dataset_rows=int(getattr(ns, 'min_dataset_rows', 100) or 100),
        )
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('ok')) else 2

    if ns.command == 'retention':
        payload = retention_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            apply=bool(getattr(ns, 'apply', False)),
            days=getattr(ns, 'days', None),
            keep_effective_config_snapshots=int(getattr(ns, 'keep_effective_config_snapshots', 20) or 20),
            list_limit=int(getattr(ns, 'list_limit', 50) or 50),
        )
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('ok')) else 2

    if ns.command == 'release':
        payload = release_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('ok')) else 2

    if ns.command == 'practice':
        payload = practice_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            max_stake_amount=float(getattr(ns, 'max_stake_amount', 5.0) or 5.0),
            soak_stale_after_sec=getattr(ns, 'soak_stale_after_sec', None),
        )
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('ok')) else 2

    if ns.command == 'practice-bootstrap':
        payload = practice_bootstrap_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            lookback_candles=int(getattr(ns, 'lookback_candles', 2000) or 2000),
            soak_cycles=int(getattr(ns, 'soak_cycles', 3) or 3),
            force_prepare=bool(getattr(ns, 'force_prepare', False)),
            force_soak=bool(getattr(ns, 'force_soak', False)),
            skip_soak=bool(getattr(ns, 'skip_soak', False)),
            max_stake_amount=float(getattr(ns, 'max_stake_amount', 5.0) or 5.0),
            soak_stale_after_sec=getattr(ns, 'soak_stale_after_sec', None),
        )
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('round_eligible')) else 2

    if ns.command == 'practice-round':
        payload = practice_round_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            soak_cycles=int(getattr(ns, 'soak_cycles', 3) or 3),
            force_soak=bool(getattr(ns, 'force_soak', False)),
            skip_soak=bool(getattr(ns, 'skip_soak', False)),
            max_stake_amount=float(getattr(ns, 'max_stake_amount', 5.0) or 5.0),
            soak_stale_after_sec=getattr(ns, 'soak_stale_after_sec', None),
            force_send_alerts=bool(getattr(ns, 'force_send_alerts', False)),
            incident_limit=int(getattr(ns, 'incident_limit', 20) or 20),
            window_hours=int(getattr(ns, 'window_hours', 24) or 24),
        )
        _print(payload, as_json=bool(ns.json))
        return 0 if bool(payload.get('round_ok')) else 2


    if ns.command == 'retrain':
        cmd = getattr(ns, 'retrain_cmd', None)
        if cmd == 'status':
            payload = retrain_status_payload(
                repo_root=_common_repo_root(ns),
                config_path=_common_config(ns),
                asset=getattr(ns, 'asset', None),
                interval_sec=getattr(ns, 'interval_sec', None),
            )
            _print(payload, as_json=bool(ns.json))
            return 0 if bool(payload.get('ok', True)) else 2
        if cmd == 'run':
            payload = retrain_run_payload(
                repo_root=_common_repo_root(ns),
                config_path=_common_config(ns),
                asset=getattr(ns, 'asset', None),
                interval_sec=getattr(ns, 'interval_sec', None),
                force=bool(getattr(ns, 'force', False)),
                promote_threshold=float(getattr(ns, 'promote_threshold', 0.5) or 0.5),
            )
            _print(payload, as_json=bool(ns.json))
            return 0 if bool(payload.get('ok', True)) else 2

    if ns.command == 'incidents':
        cmd = getattr(ns, 'incidents_cmd', None)
        if cmd == 'status':
            payload = incidents_payload(
                repo_root=_common_repo_root(ns),
                config_path=_common_config(ns),
                limit=int(getattr(ns, 'limit', 20) or 20),
                window_hours=int(getattr(ns, 'window_hours', 24) or 24),
            )
            _print(payload, as_json=bool(ns.json))
            return 0 if bool(payload.get('ok', True)) else 2
        if cmd == 'report':
            payload = incidents_report_payload(
                repo_root=_common_repo_root(ns),
                config_path=_common_config(ns),
                limit=int(getattr(ns, 'limit', 20) or 20),
                window_hours=int(getattr(ns, 'window_hours', 24) or 24),
            )
            _print(payload, as_json=bool(ns.json))
            return 0 if bool(payload.get('ok', True)) else 2
        if cmd == 'alert':
            payload = incidents_alert_payload(
                repo_root=_common_repo_root(ns),
                config_path=_common_config(ns),
                limit=int(getattr(ns, 'limit', 20) or 20),
                window_hours=int(getattr(ns, 'window_hours', 24) or 24),
                force_send=bool(getattr(ns, 'force_send', False)),
            )
            _print(payload, as_json=bool(ns.json))
            return 0
        if cmd == 'drill':
            payload = incidents_drill_payload(
                repo_root=_common_repo_root(ns),
                config_path=_common_config(ns),
                scenario=str(getattr(ns, 'scenario', 'broker_down') or 'broker_down'),
            )
            _print(payload, as_json=bool(ns.json))
            return 0
        raise SystemExit(f'unknown incidents cmd: {cmd!r}')

    if ns.command == 'alerts':
        cmd = getattr(ns, 'alerts_cmd', None)
        if cmd == 'status':
            payload = alerts_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), limit=int(getattr(ns, 'limit', 20) or 20))
            _print(payload, as_json=bool(ns.json))
            return 0
        if cmd == 'test':
            payload = alerts_test_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), force_send=bool(getattr(ns, 'force_send', False)))
            _print(payload, as_json=bool(ns.json))
            return 0
        if cmd == 'release':
            payload = alerts_release_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), force_send=bool(getattr(ns, 'force_send', False)))
            _print(payload, as_json=bool(ns.json))
            return 0
        if cmd == 'flush':
            payload = alerts_flush_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), limit=int(getattr(ns, 'limit', 20) or 20))
            _print(payload, as_json=bool(ns.json))
            return 0 if bool((payload.get('flush') or payload).get('ok', True)) else 2
        raise SystemExit(f'unknown alerts cmd: {cmd!r}')

    if ns.command == 'orders':
        payload = orders_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), limit=ns.limit)
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command in {'execute-order', 'execute_order'}:
        payload = execute_order_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command in {'check-order-status', 'check_order_status'}:
        payload = check_order_status_payload(
            repo_root=_common_repo_root(ns),
            config_path=_common_config(ns),
            external_order_id=ns.external_order_id,
            refresh=not bool(ns.no_refresh),
        )
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command == 'reconcile':
        payload = reconcile_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command == 'observe':
        request = ObserveRequest(
            once=bool(ns.once),
            max_cycles=ns.max_cycles,
            topk=ns.topk,
            lookback_candles=ns.lookback_candles,
            stop_on_failure=not bool(ns.no_stop_on_failure),
            quota_aware_sleep=bool(ns.quota_aware_sleep),
            precheck_market_context=bool(ns.precheck_market_context),
            sleep_align_offset_sec=ns.sleep_align_offset_sec,
        )
        code, payload = observe_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), request=request)
        _print(payload, as_json=bool(ns.json))
        return int(code)

    if ns.command == 'portfolio':
        cmd = getattr(ns, 'portfolio_cmd', None)
        if cmd == 'status':
            payload = portfolio_status_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
            _print(payload, as_json=bool(ns.json))
            return 0
        if cmd == 'plan':
            payload = portfolio_plan_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
            _print(payload, as_json=bool(ns.json))
            return 0
        if cmd == 'observe':
            request = ObserveRequest(
                once=bool(ns.once),
                max_cycles=ns.max_cycles,
                topk=ns.topk,
                lookback_candles=ns.lookback_candles,
                stop_on_failure=not bool(getattr(ns, 'no_stop_on_failure', False)),
                quota_aware_sleep=bool(getattr(ns, 'quota_aware_sleep', False)),
                precheck_market_context=bool(getattr(ns, 'precheck_market_context', False)),
            )
            code, payload = portfolio_observe_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), request=request)
            _print(payload, as_json=bool(ns.json))
            return int(code)

    if ns.command == 'asset':
        cmd = getattr(ns, 'asset_cmd', None)
        if cmd == 'prepare':
            payload = asset_prepare_payload(
                repo_root=_common_repo_root(ns),
                config_path=_common_config(ns),
                asset=str(ns.asset),
                interval_sec=int(ns.interval_sec),
                lookback_candles=int(ns.lookback_candles),
            )
            _print(payload, as_json=bool(ns.json))
            return 0 if bool(payload.get('ok')) else 2
        if cmd == 'candidate':
            payload = asset_candidate_payload(
                repo_root=_common_repo_root(ns),
                config_path=_common_config(ns),
                asset=str(ns.asset),
                interval_sec=int(ns.interval_sec),
                topk=int(ns.topk),
                lookback_candles=int(ns.lookback_candles),
            )
            _print(payload, as_json=bool(ns.json))
            return 0 if bool(payload.get('ok')) else 2

    if ns.command == 'ops':
        from .ops import (
            drain_mode_off,
            drain_mode_on,
            gate_status,
            kill_switch_off,
            kill_switch_on,
        )

        cmd = str(getattr(ns, 'ops_cmd', ''))
        op = str(getattr(ns, 'op', 'status'))
        if cmd == 'killswitch':
            if op == 'on':
                payload = kill_switch_on(
                    repo_root=_common_repo_root(ns),
                    config_path=_common_config(ns),
                    reason=getattr(ns, 'reason', None),
                )
            elif op == 'off':
                payload = kill_switch_off(repo_root=_common_repo_root(ns), config_path=_common_config(ns), reason=getattr(ns, 'reason', None))
            else:
                payload = gate_status(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
            _print(payload, as_json=True)
            return 0
        if cmd == 'drain':
            if op == 'on':
                payload = drain_mode_on(
                    repo_root=_common_repo_root(ns),
                    config_path=_common_config(ns),
                    reason=getattr(ns, 'reason', None),
                )
            elif op == 'off':
                payload = drain_mode_off(repo_root=_common_repo_root(ns), config_path=_common_config(ns), reason=getattr(ns, 'reason', None))
            else:
                payload = gate_status(repo_root=_common_repo_root(ns), config_path=_common_config(ns))
            _print(payload, as_json=True)
            return 0
        raise SystemExit(f'unknown ops cmd: {cmd!r}')

    raise SystemExit(f'unknown command: {ns.command!r}')
