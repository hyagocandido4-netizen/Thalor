from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

from .commands import (
    asset_candidate_payload,
    asset_prepare_payload,
    health_payload,
    observe_payload,
    orders_payload,
    plan_payload,
    portfolio_observe_payload,
    portfolio_plan_payload,
    portfolio_status_payload,
    precheck_payload,
    quota_payload,
    reconcile_payload,
    status_payload,
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

    sp_health = sub.add_parser('health', help='Emit health payload')
    _add_common(sp_health)
    sp_health.add_argument('--json', action='store_true')
    sp_health.add_argument('--topk', type=int, default=3)

    sp_orders = sub.add_parser('orders', help='Inspect Package N execution intents')
    _add_common(sp_orders)
    sp_orders.add_argument('--json', action='store_true')
    sp_orders.add_argument('--limit', type=int, default=20)

    sp_reconcile = sub.add_parser('reconcile', help='Run Package N reconciliation now')
    _add_common(sp_reconcile)
    sp_reconcile.add_argument('--json', action='store_true')

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
    known = {'status', 'plan', 'quota', 'precheck', 'health', 'observe', 'orders', 'reconcile', 'portfolio', 'asset', 'ops'}
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

    if ns.command == 'health':
        payload = health_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), topk=ns.topk)
        _print(payload, as_json=bool(ns.json))
        return 0

    if ns.command == 'orders':
        payload = orders_payload(repo_root=_common_repo_root(ns), config_path=_common_config(ns), limit=ns.limit)
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
