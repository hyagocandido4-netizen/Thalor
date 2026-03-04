from __future__ import annotations

"""Quota and pacing helpers for the Python runtime daemon.

Package K extracts the quota/pacing policy into a pure Python module so the
Python-native daemon can reason about day budget and next wake-up time without
re-implementing fragile shell logic.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import math
import os
from typing import Any
from zoneinfo import ZoneInfo

from .runtime_repos import RuntimeTradeLedger
from .runtime_scope import repo_scope
from .summary_paths import repo_timezone_name


@dataclass(frozen=True)
class QuotaSnapshot:
    kind: str
    day: str
    executed: int
    allowed_now: int
    allowed_total: int
    budget_left_now: int
    budget_left_total: int
    pacing_enabled: bool
    asset: str
    interval_sec: int
    timezone: str
    sec_of_day: int
    now_local_iso: str
    next_at: str
    next_wake_utc: str | None
    sleep_sec: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


OPEN = 'open'
MAX_K_REACHED = 'max_k_reached_today'
PACING_QUOTA_REACHED = 'pacing_quota_reached'


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    s = str(raw).strip().lower()
    if s == '':
        return bool(default)
    return s not in ('0', 'false', 'f', 'no', 'n', 'off', '')


def pacing_allowed(*, k: int, pacing_enabled: bool, sec_of_day: int) -> int:
    k = max(1, int(k))
    if (not pacing_enabled) or k <= 1:
        return k
    sec = min(86400, max(0, int(sec_of_day)))
    frac_day = min(1.0, max(0.0, float(sec) / 86400.0))
    return min(k, max(1, int(math.floor(float(k) * frac_day)) + 1))


def next_pacing_slot_seconds(*, k: int, allowed_now: int) -> int | None:
    k = max(1, int(k))
    allowed_now = max(1, int(allowed_now))
    if allowed_now >= k:
        return None
    # The next increment happens when floor(k * frac_day) + 1 > allowed_now
    # => frac_day >= allowed_now / k
    return int(math.ceil((float(allowed_now) / float(k)) * 86400.0))


def compute_quota_day_context(*, tz_name: str, now_utc: datetime | None = None) -> tuple[datetime, str, int]:
    now = now_utc or datetime.now(UTC)
    try:
        tz = ZoneInfo(str(tz_name or 'UTC'))
    except Exception:
        tz = UTC
    local = now.astimezone(tz)
    sec = local.hour * 3600 + local.minute * 60 + local.second
    return local, local.strftime('%Y-%m-%d'), int(sec)


def _fmt_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec='seconds')


def _next_repo_day_sleep(*, now_local: datetime, offset_sec: int) -> tuple[str, str | None, int]:
    next_local_day = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    wake_local = next_local_day + timedelta(seconds=max(0, int(offset_sec)))
    wake_utc = wake_local.astimezone(UTC)
    sleep_sec = max(0, int((wake_utc - now_local.astimezone(UTC)).total_seconds()))
    return '', _fmt_utc(wake_utc), sleep_sec


def _next_pacing_sleep(*, now_local: datetime, k: int, allowed_now: int, offset_sec: int) -> tuple[str, str | None, int]:
    target_sec = next_pacing_slot_seconds(k=k, allowed_now=allowed_now)
    if target_sec is None:
        return '', None, 0
    base_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    wake_local = base_local + timedelta(seconds=int(target_sec)) + timedelta(seconds=max(0, int(offset_sec)))
    wake_utc = wake_local.astimezone(UTC)
    sleep_sec = max(0, int((wake_utc - now_local.astimezone(UTC)).total_seconds()))
    return wake_local.strftime('%H:%M'), _fmt_utc(wake_utc), sleep_sec


def build_quota_snapshot(
    repo_root: str | Path = '.',
    *,
    topk: int,
    now_utc: datetime | None = None,
    pacing_enabled: bool | None = None,
    sleep_align_offset_sec: int = 3,
) -> QuotaSnapshot:
    repo_root = Path(repo_root).resolve()
    config_path = repo_root / 'config.yaml'
    scope = repo_scope(config_path=str(config_path))
    tz_name = repo_timezone_name(config_path=str(config_path))
    local_now, day, sec = compute_quota_day_context(tz_name=tz_name, now_utc=now_utc)
    if pacing_enabled is None:
        pacing_enabled = _env_truthy('TOPK_PACING_ENABLE', default=False)
    k = max(1, int(topk))

    ledger = RuntimeTradeLedger(
        signals_db=repo_root / 'runs' / 'live_signals.sqlite3',
        state_db=repo_root / 'runs' / 'live_topk_state.sqlite3',
        default_interval=scope.interval_sec,
    )
    executed = ledger.executed_today_count(scope.asset, scope.interval_sec, day)
    allowed_now = pacing_allowed(k=k, pacing_enabled=bool(pacing_enabled), sec_of_day=sec)
    budget_left_total = max(0, k - int(executed))
    budget_left_now = budget_left_total if not pacing_enabled else max(0, int(allowed_now) - int(executed))

    kind = OPEN
    next_at = ''
    next_wake_utc: str | None = None
    sleep_sec = 0

    if int(executed) >= k:
        kind = MAX_K_REACHED
        next_at, next_wake_utc, sleep_sec = _next_repo_day_sleep(now_local=local_now, offset_sec=sleep_align_offset_sec)
        allowed_now = k
        budget_left_now = 0
    elif bool(pacing_enabled) and int(executed) >= int(allowed_now) and int(executed) < k:
        kind = PACING_QUOTA_REACHED
        next_at, next_wake_utc, sleep_sec = _next_pacing_sleep(
            now_local=local_now,
            k=k,
            allowed_now=int(allowed_now),
            offset_sec=sleep_align_offset_sec,
        )
        budget_left_now = 0

    return QuotaSnapshot(
        kind=str(kind),
        day=str(day),
        executed=int(executed),
        allowed_now=int(allowed_now),
        allowed_total=int(k),
        budget_left_now=int(budget_left_now),
        budget_left_total=int(budget_left_total),
        pacing_enabled=bool(pacing_enabled),
        asset=str(scope.asset),
        interval_sec=int(scope.interval_sec),
        timezone=str(tz_name),
        sec_of_day=int(sec),
        now_local_iso=str(local_now.isoformat(timespec='seconds')),
        next_at=str(next_at),
        next_wake_utc=next_wake_utc,
        sleep_sec=int(sleep_sec),
    )


__all__ = [
    'QuotaSnapshot',
    'OPEN',
    'MAX_K_REACHED',
    'PACING_QUOTA_REACHED',
    'pacing_allowed',
    'next_pacing_slot_seconds',
    'compute_quota_day_context',
    'build_quota_snapshot',
]
