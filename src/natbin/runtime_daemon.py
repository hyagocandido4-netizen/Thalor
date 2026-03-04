from __future__ import annotations

"""Python-native runtime daemon foundation.

Package J introduces an additive Python daemon that can run the same auto-cycle
plan defined in :mod:`natbin.runtime_cycle` on a loop. The existing PowerShell
scheduler remains the primary production entrypoint for now; this module exists
so future packages can thin the shell layer without losing behaviour or
observability.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import argparse
import json
import os
import time
from typing import Any

from .runtime_cycle import OK, build_auto_cycle_plan, report_from_plan, run_plan
from .runtime_quota import OPEN as QUOTA_OPEN, build_quota_snapshot
from .runtime_scope import daemon_lock_path, repo_scope


@dataclass(frozen=True)
class SleepPlan:
    reason: str
    sleep_sec: int
    next_wake_utc: str


@dataclass(frozen=True)
class DaemonStatus:
    phase: str
    ok: bool | None
    now_utc: str
    next_wake_utc: str | None
    sleep_reason: str | None
    steps: list[dict[str, Any]] | None = None
    outcomes: list[dict[str, Any]] | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _fmt_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec='seconds')


def _parse_now_utc(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except Exception as e:  # pragma: no cover - defensive/CLI only
        raise SystemExit(f'invalid --now-utc: {s!r} ({e})')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def compute_next_candle_sleep(interval_sec: int, *, offset_sec: int = 3, now_utc: datetime | None = None) -> SleepPlan:
    now = now_utc or _utcnow()
    epoch = int(now.timestamp())
    interval_sec = max(1, int(interval_sec))
    offset_sec = max(0, int(offset_sec))
    nxt = ((epoch // interval_sec) + 1) * interval_sec + offset_sec
    nxt_dt = datetime.fromtimestamp(nxt, tz=UTC)
    sleep_sec = max(0, int((nxt_dt - now).total_seconds()))
    return SleepPlan(reason='next_candle', sleep_sec=sleep_sec, next_wake_utc=_fmt_utc(nxt_dt) or '')


def compute_day_reset_sleep(tz_name: str, *, offset_sec: int = 3, now_utc: datetime | None = None) -> SleepPlan:
    now = now_utc or _utcnow()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(str(tz_name or 'UTC'))
    except Exception:
        tz = UTC
    local = now.astimezone(tz)
    next_local_day = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    next_utc = next_local_day.astimezone(UTC) + timedelta(seconds=max(0, int(offset_sec)))
    sleep_sec = max(0, int((next_utc - now).total_seconds()))
    return SleepPlan(reason='next_repo_day', sleep_sec=sleep_sec, next_wake_utc=_fmt_utc(next_utc) or '')


def classify_report_ok(report: dict[str, Any]) -> bool:
    return bool(report.get('ok'))


def acquire_lock(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def run_once(*, repo_root: str | Path = '.', topk: int = 3, lookback_candles: int = 2000, stop_on_failure: bool = True) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    plan = build_auto_cycle_plan(repo_root, topk=topk, lookback_candles=lookback_candles)
    outcomes = run_plan(plan, stop_on_failure=stop_on_failure)
    rep = asdict(report_from_plan(repo_root, plan, outcomes))
    rep['phase'] = 'cycle'
    rep['now_utc'] = _fmt_utc(_utcnow())
    try:
        rep['quota_snapshot'] = build_quota_snapshot(repo_root, topk=topk).as_dict()
    except Exception as e:  # pragma: no cover - diagnostic only
        rep['quota_snapshot_error'] = str(e)
    return rep


def run_daemon(*, repo_root: str | Path = '.', topk: int = 3, lookback_candles: int = 2000, max_cycles: int | None = None, sleep_align_offset_sec: int = 3, stop_on_failure: bool = True, quota_aware_sleep: bool = False) -> int:
    repo_root = Path(repo_root).resolve()
    scope = repo_scope(config_path=str(repo_root / 'config.yaml'))
    lock_path = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=repo_root / 'runs')
    if not acquire_lock(lock_path):
        print(json.dumps({'phase': 'startup', 'ok': False, 'message': f'lock_exists:{lock_path.name}'}, ensure_ascii=False))
        return 3
    cycles = 0
    try:
        while True:
            if quota_aware_sleep:
                snap = build_quota_snapshot(repo_root, topk=topk, sleep_align_offset_sec=sleep_align_offset_sec)
                if snap.kind != QUOTA_OPEN:
                    print(json.dumps({'phase': 'quota', 'ok': True, 'quota': snap.as_dict()}, ensure_ascii=False))
                    cycles += 1
                    if max_cycles is not None and cycles >= int(max_cycles):
                        break
                    sleep_plan = SleepPlan(reason=snap.kind, sleep_sec=int(snap.sleep_sec), next_wake_utc=str(snap.next_wake_utc or ''))
                    print(json.dumps({'phase': 'sleep', 'reason': sleep_plan.reason, 'sleep_sec': sleep_plan.sleep_sec, 'next_wake_utc': sleep_plan.next_wake_utc}, ensure_ascii=False))
                    time.sleep(max(0, int(sleep_plan.sleep_sec)))
                    continue
            rep = run_once(repo_root=repo_root, topk=topk, lookback_candles=lookback_candles, stop_on_failure=stop_on_failure)
            print(json.dumps(rep, ensure_ascii=False))
            cycles += 1
            if max_cycles is not None and cycles >= int(max_cycles):
                break
            sleep_plan = compute_next_candle_sleep(scope.interval_sec, offset_sec=sleep_align_offset_sec)
            print(json.dumps({'phase': 'sleep', 'reason': sleep_plan.reason, 'sleep_sec': sleep_plan.sleep_sec, 'next_wake_utc': sleep_plan.next_wake_utc}, ensure_ascii=False))
            time.sleep(max(0, int(sleep_plan.sleep_sec)))
        return 0
    finally:
        release_lock(lock_path)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Python-native daemon for the Thalor runtime cycle (Package J foundation).')
    p.add_argument('--repo-root', default='.')
    p.add_argument('--topk', type=int, default=3)
    p.add_argument('--lookback-candles', type=int, default=2000)
    p.add_argument('--once', action='store_true', help='Run a single cycle and exit')
    p.add_argument('--max-cycles', type=int, default=None, help='Loop for N cycles and exit')
    p.add_argument('--sleep-align-offset-sec', type=int, default=3)
    p.add_argument('--quota-aware-sleep', action='store_true', help='Use Python quota/pacing precheck to sleep until next relevant window/day')
    p.add_argument('--quota-json', action='store_true', help='Emit current quota snapshot JSON and exit')
    p.add_argument('--now-utc', default=None, help='Optional ISO8601 UTC override for deterministic quota/sleep evaluation in tests/debugging')
    p.add_argument('--no-stop-on-failure', action='store_true')
    p.add_argument('--plan-json', action='store_true', help='Emit the canonical plan JSON and exit')
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    repo_root = Path(ns.repo_root).resolve()
    now_override = _parse_now_utc(ns.now_utc)
    if ns.plan_json:
        plan = build_auto_cycle_plan(repo_root, topk=ns.topk, lookback_candles=ns.lookback_candles)
        rep = asdict(report_from_plan(repo_root, plan))
        rep['daemon_capable'] = True
        rep['quota_capable'] = True
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    if ns.quota_json:
        snap = build_quota_snapshot(
            repo_root,
            topk=ns.topk,
            sleep_align_offset_sec=ns.sleep_align_offset_sec,
            now_utc=now_override,
        )
        print(json.dumps(snap.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if ns.once:
        rep = run_once(repo_root=repo_root, topk=ns.topk, lookback_candles=ns.lookback_candles, stop_on_failure=not ns.no_stop_on_failure)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if classify_report_ok(rep) else 2
    return run_daemon(repo_root=repo_root, topk=ns.topk, lookback_candles=ns.lookback_candles, max_cycles=ns.max_cycles, sleep_align_offset_sec=ns.sleep_align_offset_sec, stop_on_failure=not ns.no_stop_on_failure, quota_aware_sleep=bool(ns.quota_aware_sleep))


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
