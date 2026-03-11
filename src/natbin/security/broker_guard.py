from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..runtime.execution_policy import ensure_utc_iso


@dataclass(frozen=True)
class BrokerGuardDecision:
    allowed: bool
    reason: str | None
    scope_tag: str
    account_mode: str
    mode: str
    checked_at_utc: str
    checked_at_local: str
    window_open: bool
    spacing_open: bool
    rate_open: bool
    state_path: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@contextmanager

def _file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt  # type: ignore
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl  # type: ignore
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield fh
    finally:
        try:
            if os.name == 'nt':
                import msvcrt  # type: ignore
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # type: ignore
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass



def _parse_hhmm(value: str) -> tuple[int, int]:
    text = str(value or '').strip()
    hh, mm = text.split(':', 1)
    h = int(hh)
    m = int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError(f'invalid HH:MM: {value!r}')
    return h, m



def _resolve_guard_cfg(ctx) -> dict[str, Any]:
    resolved = ctx.resolved_config if isinstance(ctx.resolved_config, dict) else {}
    sec = dict((resolved or {}).get('security') or {})
    return dict(sec.get('guard') or {})



def _execution_mode(ctx) -> str:
    resolved = ctx.resolved_config if isinstance(ctx.resolved_config, dict) else {}
    execution = dict((resolved or {}).get('execution') or {})
    return str(execution.get('mode') or 'disabled').strip().lower()



def _account_mode(ctx) -> str:
    resolved = ctx.resolved_config if isinstance(ctx.resolved_config, dict) else {}
    execution = dict((resolved or {}).get('execution') or {})
    return str(execution.get('account_mode') or 'PRACTICE').upper()



def _state_path(repo_root: str | Path, ctx) -> Path:
    cfg = _resolve_guard_cfg(ctx)
    path = Path(str(cfg.get('state_path') or 'runs/security/broker_guard_state.json'))
    if not path.is_absolute():
        path = Path(repo_root).resolve() / path
    return path



def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {'global_submit_times_utc': [], 'updated_at_utc': None}
    lock_path = path.with_suffix(path.suffix + '.lock')
    with _file_lock(lock_path):
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault('global_submit_times_utc', [])
    raw.setdefault('updated_at_utc', None)
    raw.setdefault('last_submit_at_utc', None)
    return raw



def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    lock_path = path.with_suffix(path.suffix + '.lock')
    with _file_lock(lock_path):
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        tmp.replace(path)



def _coerce_utc(value: Any) -> datetime | None:
    iso = ensure_utc_iso(value)
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)



def _local_now(now_utc: datetime, timezone_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(str(timezone_name or 'UTC'))
    except Exception:
        tz = UTC
    return now_utc.astimezone(tz)



def _window_open(now_local: datetime, *, start_hhmm: str, end_hhmm: str) -> bool:
    sh, sm = _parse_hhmm(start_hhmm)
    eh, em = _parse_hhmm(end_hhmm)
    cur = now_local.hour * 60 + now_local.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end



def read_guard_state(*, repo_root: str | Path = '.', ctx=None, state_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(state_path) if state_path is not None else _state_path(repo_root, ctx)
    if not path.is_absolute():
        path = Path(repo_root).resolve() / path
    state = _load_state(path)
    state['state_path'] = str(path)
    return state



def evaluate_submit_guard(*, repo_root: str | Path = '.', ctx, now_utc: datetime | None = None) -> BrokerGuardDecision:
    cfg = _resolve_guard_cfg(ctx)
    enabled = bool(cfg.get('enabled', True))
    live_only = bool(cfg.get('live_only', True))
    mode = _execution_mode(ctx)
    account_mode = _account_mode(ctx)
    scope_tag = str(getattr(ctx.scope, 'scope_tag', '') or (ctx.resolved_config or {}).get('scope_tag') or '')
    path = _state_path(repo_root, ctx)
    if now_utc is None:
        now_utc = datetime.now(UTC)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    else:
        now_utc = now_utc.astimezone(UTC)
    now_local = _local_now(now_utc, str(getattr(ctx.config, 'timezone', 'UTC')))

    if not enabled:
        return BrokerGuardDecision(
            allowed=True,
            reason=None,
            scope_tag=scope_tag,
            account_mode=account_mode,
            mode=mode,
            checked_at_utc=now_utc.isoformat(timespec='seconds'),
            checked_at_local=now_local.isoformat(timespec='seconds'),
            window_open=True,
            spacing_open=True,
            rate_open=True,
            state_path=str(path),
            details={'enabled': False},
        )
    if live_only and mode != 'live':
        return BrokerGuardDecision(
            allowed=True,
            reason=None,
            scope_tag=scope_tag,
            account_mode=account_mode,
            mode=mode,
            checked_at_utc=now_utc.isoformat(timespec='seconds'),
            checked_at_local=now_local.isoformat(timespec='seconds'),
            window_open=True,
            spacing_open=True,
            rate_open=True,
            state_path=str(path),
            details={'enabled': True, 'live_only': True, 'skipped': 'non_live_mode'},
        )

    state = _load_state(path)
    recent: list[str] = []
    for item in list(state.get('global_submit_times_utc') or []):
        dt = _coerce_utc(item)
        if dt is None:
            continue
        if now_utc - dt <= timedelta(minutes=1):
            recent.append(dt.isoformat(timespec='seconds'))

    blocked_days = {int(x) for x in list(cfg.get('blocked_weekdays_local') or [])}
    weekday_ok = now_local.weekday() not in blocked_days
    window_open = True
    if bool(cfg.get('time_filter_enable', False)):
        window_open = weekday_ok and _window_open(
            now_local,
            start_hhmm=str(cfg.get('allowed_start_local') or '00:00'),
            end_hhmm=str(cfg.get('allowed_end_local') or '23:59'),
        )

    last_submit_dt = _coerce_utc(state.get('last_submit_at_utc'))
    spacing_sec = max(0, int(cfg.get('min_submit_spacing_sec') or 0))
    spacing_open = True
    spacing_remaining_sec = 0
    if last_submit_dt is not None and spacing_sec > 0:
        elapsed = (now_utc - last_submit_dt).total_seconds()
        if elapsed < spacing_sec:
            spacing_open = False
            spacing_remaining_sec = int(round(spacing_sec - elapsed))

    max_per_minute = max(1, int(cfg.get('max_submit_per_minute') or 1))
    rate_open = len(recent) < max_per_minute

    reason = None
    if not window_open:
        reason = 'security_time_filter_closed'
    elif not spacing_open:
        reason = 'security_submit_spacing'
    elif not rate_open:
        reason = 'security_submit_rate_limit'

    return BrokerGuardDecision(
        allowed=reason is None,
        reason=reason,
        scope_tag=scope_tag,
        account_mode=account_mode,
        mode=mode,
        checked_at_utc=now_utc.isoformat(timespec='seconds'),
        checked_at_local=now_local.isoformat(timespec='seconds'),
        window_open=window_open,
        spacing_open=spacing_open,
        rate_open=rate_open,
        state_path=str(path),
        details={
            'recent_submit_count_1m': len(recent),
            'max_submit_per_minute': max_per_minute,
            'min_submit_spacing_sec': spacing_sec,
            'spacing_remaining_sec': spacing_remaining_sec,
            'blocked_weekdays_local': sorted(blocked_days),
            'allowed_start_local': str(cfg.get('allowed_start_local') or '00:00'),
            'allowed_end_local': str(cfg.get('allowed_end_local') or '23:59'),
            'last_submit_at_utc': state.get('last_submit_at_utc'),
        },
    )



def note_submit_attempt(*, repo_root: str | Path = '.', ctx, transport_status: str | None = None, now_utc: datetime | None = None) -> dict[str, Any]:
    cfg = _resolve_guard_cfg(ctx)
    enabled = bool(cfg.get('enabled', True))
    live_only = bool(cfg.get('live_only', True))
    mode = _execution_mode(ctx)
    path = _state_path(repo_root, ctx)
    if now_utc is None:
        now_utc = datetime.now(UTC)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    else:
        now_utc = now_utc.astimezone(UTC)

    state = _load_state(path)
    state.setdefault('global_submit_times_utc', [])
    recent: list[str] = []
    for item in list(state.get('global_submit_times_utc') or []):
        dt = _coerce_utc(item)
        if dt is None:
            continue
        if now_utc - dt <= timedelta(minutes=1):
            recent.append(dt.isoformat(timespec='seconds'))
    if enabled and (not live_only or mode == 'live'):
        recent.append(now_utc.isoformat(timespec='seconds'))
        state['last_submit_at_utc'] = now_utc.isoformat(timespec='seconds')
        state['last_transport_status'] = str(transport_status or '')
    state['global_submit_times_utc'] = recent
    state['updated_at_utc'] = now_utc.isoformat(timespec='seconds')
    _save_state(path, state)
    state['state_path'] = str(path)
    return state
