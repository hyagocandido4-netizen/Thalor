from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config.execution_mode import execution_mode_uses_broker_submit
from ..ops.structured_log import append_jsonl
from ..runtime.broker_surface import build_context, execution_cfg, execution_repo_path
from ..runtime.execution_signal import latest_trade_row
from ..runtime.execution_policy import ensure_utc_iso
from ..portfolio.correlation import resolve_correlation_group
from ..state.control_repo import write_control_artifact
from ..state.execution_repo import ExecutionRepository


@dataclass(frozen=True)
class ProtectionDecision:
    allowed: bool
    action: str
    reason: str | None
    scope_tag: str
    account_mode: str
    mode: str
    provider: str
    checked_at_utc: str
    checked_at_local: str
    session_name: str | None
    session_open: bool
    global_spacing_open: bool
    asset_spacing_open: bool
    rate_15m_global_open: bool
    rate_15m_asset_open: bool
    rate_60m_global_open: bool
    rate_60m_asset_open: bool
    day_budget_global_open: bool
    day_budget_asset_open: bool
    correlation_open: bool
    cluster_key: str | None
    volatility_score: float | None
    volatility_source: str | None
    recommended_delay_sec: float
    applied_delay_sec: float | None = None
    state_path: str | None = None
    decision_log_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

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
    hh, mm = str(value or '').strip().split(':', 1)
    h = int(hh)
    m = int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError(f'invalid HH:MM: {value!r}')
    return h, m


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


def _local_now(now_utc: datetime, timezone_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(str(timezone_name or 'UTC'))
    except Exception:
        tz = UTC
    return now_utc.astimezone(tz)


def _execution_mode(ctx) -> str:
    return str(execution_cfg(ctx).get('mode') or 'disabled').strip().lower()


def _account_mode(ctx) -> str:
    return str(execution_cfg(ctx).get('account_mode') or 'PRACTICE').upper()


def _provider(ctx) -> str:
    return str(execution_cfg(ctx).get('provider') or 'fake').strip().lower()


def _security_cfg(ctx) -> dict[str, Any]:
    resolved = ctx.resolved_config if isinstance(ctx.resolved_config, dict) else {}
    return dict((resolved or {}).get('security') or {})


def _guard_cfg(ctx) -> dict[str, Any]:
    return dict(_security_cfg(ctx).get('guard') or {})


def _protection_cfg(ctx) -> dict[str, Any]:
    return dict(_security_cfg(ctx).get('protection') or {})


def _state_path(repo_root: str | Path, ctx) -> Path:
    cfg = _protection_cfg(ctx)
    path = Path(str(cfg.get('state_path') or 'runs/security/account_protection_state.json'))
    if not path.is_absolute():
        path = Path(repo_root).resolve() / path
    return path


def _decision_log_path(repo_root: str | Path, ctx) -> Path:
    cfg = _protection_cfg(ctx)
    path = Path(str(cfg.get('decision_log_path') or 'runs/logs/account_protection.jsonl'))
    if not path.is_absolute():
        path = Path(repo_root).resolve() / path
    return path


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            'updated_at_utc': None,
            'global_submit_times_utc': [],
            'scope_submit_times_utc': {},
            'cluster_submit_times_utc': {},
            'last_submit_global_at_utc': None,
            'last_submit_by_scope_utc': {},
            'last_submit_by_cluster_utc': {},
        }
    lock_path = path.with_suffix(path.suffix + '.lock')
    with _file_lock(lock_path):
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault('updated_at_utc', None)
    raw.setdefault('global_submit_times_utc', [])
    raw.setdefault('scope_submit_times_utc', {})
    raw.setdefault('cluster_submit_times_utc', {})
    raw.setdefault('last_submit_global_at_utc', None)
    raw.setdefault('last_submit_by_scope_utc', {})
    raw.setdefault('last_submit_by_cluster_utc', {})
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


def _clean_recent(items: list[Any], *, now_utc: datetime, keep_sec: int) -> list[str]:
    out: list[str] = []
    for item in list(items or []):
        dt = _coerce_utc(item)
        if dt is None:
            continue
        if (now_utc - dt).total_seconds() <= max(1, int(keep_sec)):
            out.append(dt.isoformat(timespec='seconds'))
    return out


def _scope_cluster_key(ctx, latest_trade: dict[str, Any] | None) -> str | None:
    if isinstance(latest_trade, dict):
        value = str(latest_trade.get('cluster_key') or '').strip()
        if value:
            return resolve_correlation_group(str(ctx.config.asset), value)
    try:
        from ..config.loader import load_thalor_config

        cfg = load_thalor_config(config_path=ctx.config.config_path, repo_root=ctx.repo_root)
        for item in list(getattr(cfg, 'assets', []) or []):
            try:
                asset_ok = str(getattr(item, 'asset', '') or '') == str(ctx.config.asset)
                interval_ok = int(getattr(item, 'interval_sec', 0) or 0) == int(ctx.config.interval_sec)
            except Exception:
                continue
            if not asset_ok or not interval_ok:
                continue
            value = str(getattr(item, 'cluster_key', '') or '').strip() or 'default'
            return resolve_correlation_group(str(ctx.config.asset), value)
    except Exception:
        pass
    return resolve_correlation_group(str(ctx.config.asset), 'default')


def _market_context(ctx) -> dict[str, Any] | None:
    path_raw = getattr(ctx, 'scoped_paths', {}).get('market_context') if hasattr(ctx, 'scoped_paths') else None
    if not path_raw:
        return None
    path = Path(path_raw)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_score(value: Any) -> float | None:
    if value in (None, ''):
        return None
    try:
        score = float(value)
    except Exception:
        return None
    if score < 0:
        score = abs(score)
    if score <= 1.0:
        return max(0.0, min(1.0, score))
    if score <= 10.0:
        return max(0.0, min(1.0, score / 10.0))
    return 1.0


def _volatility_score(latest_trade: dict[str, Any] | None, market_context: dict[str, Any] | None) -> tuple[float | None, str | None]:
    keys = ['volatility_score', 'f_vol48', 'volatility', 'f_atr14', 'atr']
    if isinstance(latest_trade, dict):
        for key in keys:
            value = _normalize_score(latest_trade.get(key))
            if value is not None:
                return value, f'latest_trade:{key}'
    if isinstance(market_context, dict):
        for key in keys:
            value = _normalize_score(market_context.get(key))
            if value is not None:
                return value, f'market_context:{key}'
    return 0.5, 'neutral'


def _seeded_unit(scope_tag: str, signal_ts: int | None, checked_at_minute: str) -> float:
    base = f'{scope_tag}|{int(signal_ts or 0)}|{checked_at_minute}'
    digest = hashlib.sha1(base.encode('utf-8')).hexdigest()[:8]
    return int(digest, 16) / float(0xFFFFFFFF)


def _session_eval(now_local: datetime, *, sessions_cfg: dict[str, Any], guard_cfg: dict[str, Any]) -> tuple[bool, str | None, dict[str, Any]]:
    weekday = int(now_local.weekday())
    blocked_days = {int(x) for x in list(sessions_cfg.get('blocked_weekdays_local') or [])}
    if bool(sessions_cfg.get('inherit_guard_window', True)) and bool(guard_cfg.get('time_filter_enable', False)):
        blocked_days.update(int(x) for x in list(guard_cfg.get('blocked_weekdays_local') or []))
    if weekday in blocked_days:
        return False, None, {'blocked_weekdays_local': sorted(blocked_days)}

    session_name = None
    session_open = True
    windows = list(sessions_cfg.get('windows') or [])
    if bool(sessions_cfg.get('enabled', True)):
        session_open = False
        if windows:
            for raw in windows:
                if not isinstance(raw, dict):
                    continue
                start_local = str(raw.get('start_local') or '00:00')
                end_local = str(raw.get('end_local') or '23:59')
                if _window_open(now_local, start_hhmm=start_local, end_hhmm=end_local):
                    session_name = str(raw.get('name') or 'session')
                    session_open = True
                    break
        else:
            session_open = True
    if session_open and bool(sessions_cfg.get('inherit_guard_window', True)) and bool(guard_cfg.get('time_filter_enable', False)):
        guard_open = _window_open(
            now_local,
            start_hhmm=str(guard_cfg.get('allowed_start_local') or '00:00'),
            end_hhmm=str(guard_cfg.get('allowed_end_local') or '23:59'),
        )
        if not guard_open:
            session_open = False
            if session_name is None:
                session_name = 'guard_window'
    return session_open, session_name, {'blocked_weekdays_local': sorted(blocked_days)}


def _time_of_day_extra(now_local: datetime, cadence_cfg: dict[str, Any]) -> float:
    hour = int(now_local.hour)
    if 0 <= hour < 6:
        return float(cadence_cfg.get('overnight_extra_sec') or 0.0)
    if 6 <= hour < 11:
        return float(cadence_cfg.get('early_morning_extra_sec') or 0.0)
    if 11 <= hour < 16:
        return float(cadence_cfg.get('midday_extra_sec') or 0.0)
    if 16 <= hour < 23:
        return float(cadence_cfg.get('evening_extra_sec') or 0.0)
    return float(cadence_cfg.get('overnight_extra_sec') or 0.0)


def _log_decision(path: Path, payload: dict[str, Any]) -> None:
    safe = dict(payload)
    append_jsonl(path, safe)


def evaluate_account_protection(
    *,
    repo_root: str | Path = '.',
    ctx,
    latest_trade: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
    write_artifact: bool = True,
) -> ProtectionDecision:
    repo_root = Path(repo_root).resolve()
    cfg = _protection_cfg(ctx)
    guard_cfg = _guard_cfg(ctx)
    mode = _execution_mode(ctx)
    provider = _provider(ctx)
    account_mode = _account_mode(ctx)
    scope_tag = str(getattr(getattr(ctx, 'scope', None), 'scope_tag', '') or '')
    state_path = _state_path(repo_root, ctx)
    log_path = _decision_log_path(repo_root, ctx)
    if now_utc is None:
        now_utc = datetime.now(UTC)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    else:
        now_utc = now_utc.astimezone(UTC)
    now_local = _local_now(now_utc, str(getattr(ctx.config, 'timezone', 'UTC')))

    if latest_trade is None:
        latest_trade = latest_trade_row(repo_root=repo_root, ctx=ctx)
    market_context = _market_context(ctx)
    cluster_key = _scope_cluster_key(ctx, latest_trade)

    if not bool(cfg.get('enabled', False)):
        decision = ProtectionDecision(
            allowed=True,
            action='allow',
            reason=None,
            scope_tag=scope_tag,
            account_mode=account_mode,
            mode=mode,
            provider=provider,
            checked_at_utc=now_utc.isoformat(timespec='seconds'),
            checked_at_local=now_local.isoformat(timespec='seconds'),
            session_name=None,
            session_open=True,
            global_spacing_open=True,
            asset_spacing_open=True,
            rate_15m_global_open=True,
            rate_15m_asset_open=True,
            rate_60m_global_open=True,
            rate_60m_asset_open=True,
            day_budget_global_open=True,
            day_budget_asset_open=True,
            correlation_open=True,
            cluster_key=cluster_key,
            volatility_score=None,
            volatility_source=None,
            recommended_delay_sec=0.0,
            state_path=str(state_path),
            decision_log_path=str(log_path),
            details={'enabled': False},
        )
    elif bool(cfg.get('live_submit_only', True)) and not execution_mode_uses_broker_submit(mode):
        decision = ProtectionDecision(
            allowed=True,
            action='allow',
            reason=None,
            scope_tag=scope_tag,
            account_mode=account_mode,
            mode=mode,
            provider=provider,
            checked_at_utc=now_utc.isoformat(timespec='seconds'),
            checked_at_local=now_local.isoformat(timespec='seconds'),
            session_name=None,
            session_open=True,
            global_spacing_open=True,
            asset_spacing_open=True,
            rate_15m_global_open=True,
            rate_15m_asset_open=True,
            rate_60m_global_open=True,
            rate_60m_asset_open=True,
            day_budget_global_open=True,
            day_budget_asset_open=True,
            correlation_open=True,
            cluster_key=cluster_key,
            volatility_score=None,
            volatility_source=None,
            recommended_delay_sec=0.0,
            state_path=str(state_path),
            decision_log_path=str(log_path),
            details={'enabled': True, 'skipped': 'non_broker_submit_mode'},
        )
    else:
        repo = ExecutionRepository(execution_repo_path(repo_root))
        state = _load_state(state_path)
        keep_sec = max(7200, int((_protection_cfg(ctx).get('pacing') or {}).get('min_spacing_asset_sec') or 0) * 4)
        global_times = _clean_recent(list(state.get('global_submit_times_utc') or []), now_utc=now_utc, keep_sec=keep_sec)
        scope_times_map = dict(state.get('scope_submit_times_utc') or {})
        scope_times = _clean_recent(list(scope_times_map.get(scope_tag) or []), now_utc=now_utc, keep_sec=keep_sec)
        last_global_dt = _coerce_utc((state.get('last_submit_global_at_utc') or (global_times[-1] if global_times else None)))
        last_scope_dt = _coerce_utc((dict(state.get('last_submit_by_scope_utc') or {}).get(scope_tag) or (scope_times[-1] if scope_times else None)))

        sessions_cfg = dict(cfg.get('sessions') or {})
        session_open, session_name, session_extra = _session_eval(now_local, sessions_cfg=sessions_cfg, guard_cfg=guard_cfg)

        pacing_cfg = dict(cfg.get('pacing') or {})
        min_spacing_global_sec = max(0, int(pacing_cfg.get('min_spacing_global_sec') or 0))
        min_spacing_asset_sec = max(0, int(pacing_cfg.get('min_spacing_asset_sec') or 0))
        global_spacing_open = True
        asset_spacing_open = True
        global_spacing_remaining_sec = 0
        asset_spacing_remaining_sec = 0
        if last_global_dt is not None and min_spacing_global_sec > 0:
            elapsed = (now_utc - last_global_dt).total_seconds()
            if elapsed < min_spacing_global_sec:
                global_spacing_open = False
                global_spacing_remaining_sec = int(round(min_spacing_global_sec - elapsed))
        if last_scope_dt is not None and min_spacing_asset_sec > 0:
            elapsed = (now_utc - last_scope_dt).total_seconds()
            if elapsed < min_spacing_asset_sec:
                asset_spacing_open = False
                asset_spacing_remaining_sec = int(round(min_spacing_asset_sec - elapsed))

        recent_15m_global = len([x for x in global_times if (now_utc - _coerce_utc(x)).total_seconds() <= 900 if _coerce_utc(x) is not None])
        recent_60m_global = len([x for x in global_times if (now_utc - _coerce_utc(x)).total_seconds() <= 3600 if _coerce_utc(x) is not None])
        recent_15m_asset = len([x for x in scope_times if (now_utc - _coerce_utc(x)).total_seconds() <= 900 if _coerce_utc(x) is not None])
        recent_60m_asset = len([x for x in scope_times if (now_utc - _coerce_utc(x)).total_seconds() <= 3600 if _coerce_utc(x) is not None])

        max_15m_global = max(1, int(pacing_cfg.get('max_submit_15m_global') or 1))
        max_15m_asset = max(1, int(pacing_cfg.get('max_submit_15m_asset') or 1))
        max_60m_global = max(1, int(pacing_cfg.get('max_submit_60m_global') or 1))
        max_60m_asset = max(1, int(pacing_cfg.get('max_submit_60m_asset') or 1))
        max_day_global = max(1, int(pacing_cfg.get('max_submit_day_global') or 1))
        max_day_asset = max(1, int(pacing_cfg.get('max_submit_day_asset') or 1))

        rate_15m_global_open = recent_15m_global < max_15m_global
        rate_15m_asset_open = recent_15m_asset < max_15m_asset
        rate_60m_global_open = recent_60m_global < max_60m_global
        rate_60m_asset_open = recent_60m_asset < max_60m_asset

        day_local = now_local.strftime('%Y-%m-%d')
        day_global_count = repo.count_consuming_intents_global(day=day_local)
        day_asset_count = repo.count_consuming_intents(asset=str(ctx.config.asset), interval_sec=int(ctx.config.interval_sec), day=day_local)
        day_budget_global_open = int(day_global_count) < max_day_global
        day_budget_asset_open = int(day_asset_count) < max_day_asset

        correlation_cfg = dict(cfg.get('correlation') or {})
        correlation_open = True
        cluster_open_count = 0
        cluster_pending_count = 0
        if bool(correlation_cfg.get('enabled', True)) and cluster_key:
            cluster_open_count = repo.count_active_intents_by_cluster(cluster_key=cluster_key, states=['accepted_open'], exclude_scope_tag=scope_tag)
            cluster_pending_count = repo.count_active_intents_by_cluster(cluster_key=cluster_key, states=['submitted_unknown'], exclude_scope_tag=scope_tag)
            if bool(correlation_cfg.get('block_same_cluster_active', True)):
                correlation_open = (
                    cluster_open_count < max(1, int(correlation_cfg.get('max_active_per_cluster') or 1))
                    and cluster_pending_count < max(1, int(correlation_cfg.get('max_pending_per_cluster') or 1))
                )

        volatility_score, volatility_source = _volatility_score(latest_trade, market_context)
        cadence_cfg = dict(cfg.get('cadence') or {})
        cadence_enabled = bool(cadence_cfg.get('enabled', True))
        min_delay_sec = float(cadence_cfg.get('min_delay_sec') or 0.0)
        max_delay_sec = float(cadence_cfg.get('max_delay_sec') or 0.0)
        base_delay_sec = min_delay_sec
        tod_extra_sec = _time_of_day_extra(now_local, cadence_cfg) if cadence_enabled else 0.0
        volatility_extra_sec = (float(volatility_score or 0.0) * float(cadence_cfg.get('volatility_extra_sec') or 0.0)) if cadence_enabled else 0.0
        recent_submit_extra_sec = (min(recent_60m_global, 5) * float(cadence_cfg.get('recent_submit_weight_sec') or 0.0)) if cadence_enabled else 0.0
        jitter_sec = 0.0
        if cadence_enabled and float(cadence_cfg.get('jitter_max_sec') or 0.0) > 0:
            signal_ts = 0
            if isinstance(latest_trade, dict):
                try:
                    signal_ts = int(latest_trade.get('ts') or 0)
                except Exception:
                    signal_ts = 0
            jitter_sec = _seeded_unit(scope_tag, signal_ts, now_local.strftime('%Y-%m-%dT%H:%M')) * float(cadence_cfg.get('jitter_max_sec') or 0.0)
        recommended_delay_sec = 0.0
        if cadence_enabled:
            recommended_delay_sec = max(min_delay_sec, base_delay_sec + tod_extra_sec + volatility_extra_sec + recent_submit_extra_sec + jitter_sec)
            recommended_delay_sec = round(min(max_delay_sec, recommended_delay_sec), 3)

        reason = None
        if not session_open:
            reason = 'protection_session_closed'
        elif not global_spacing_open:
            reason = 'protection_global_spacing'
        elif not asset_spacing_open:
            reason = 'protection_asset_spacing'
        elif not rate_15m_global_open:
            reason = 'protection_global_rate_15m'
        elif not rate_15m_asset_open:
            reason = 'protection_asset_rate_15m'
        elif not rate_60m_global_open:
            reason = 'protection_global_rate_60m'
        elif not rate_60m_asset_open:
            reason = 'protection_asset_rate_60m'
        elif not day_budget_global_open:
            reason = 'protection_global_day_cap'
        elif not day_budget_asset_open:
            reason = 'protection_asset_day_cap'
        elif not correlation_open:
            reason = 'protection_correlation_cluster_active'

        action = 'block' if reason is not None else ('delay' if recommended_delay_sec > 0 else 'allow')
        decision = ProtectionDecision(
            allowed=reason is None,
            action=action,
            reason=reason,
            scope_tag=scope_tag,
            account_mode=account_mode,
            mode=mode,
            provider=provider,
            checked_at_utc=now_utc.isoformat(timespec='seconds'),
            checked_at_local=now_local.isoformat(timespec='seconds'),
            session_name=session_name,
            session_open=session_open,
            global_spacing_open=global_spacing_open,
            asset_spacing_open=asset_spacing_open,
            rate_15m_global_open=rate_15m_global_open,
            rate_15m_asset_open=rate_15m_asset_open,
            rate_60m_global_open=rate_60m_global_open,
            rate_60m_asset_open=rate_60m_asset_open,
            day_budget_global_open=day_budget_global_open,
            day_budget_asset_open=day_budget_asset_open,
            correlation_open=correlation_open,
            cluster_key=cluster_key,
            volatility_score=volatility_score,
            volatility_source=volatility_source,
            recommended_delay_sec=recommended_delay_sec if reason is None else 0.0,
            state_path=str(state_path),
            decision_log_path=str(log_path),
            details={
                'enabled': True,
                'session': session_extra | {'name': session_name},
                'recent_submit_count_15m_global': recent_15m_global,
                'recent_submit_count_15m_asset': recent_15m_asset,
                'recent_submit_count_60m_global': recent_60m_global,
                'recent_submit_count_60m_asset': recent_60m_asset,
                'min_spacing_global_sec': min_spacing_global_sec,
                'min_spacing_asset_sec': min_spacing_asset_sec,
                'global_spacing_remaining_sec': global_spacing_remaining_sec,
                'asset_spacing_remaining_sec': asset_spacing_remaining_sec,
                'max_submit_15m_global': max_15m_global,
                'max_submit_15m_asset': max_15m_asset,
                'max_submit_60m_global': max_60m_global,
                'max_submit_60m_asset': max_60m_asset,
                'max_submit_day_global': max_day_global,
                'max_submit_day_asset': max_day_asset,
                'day_submit_count_global': day_global_count,
                'day_submit_count_asset': day_asset_count,
                'cluster_open_count': cluster_open_count,
                'cluster_pending_count': cluster_pending_count,
                'delay_components': {
                    'base_delay_sec': round(base_delay_sec, 3),
                    'time_of_day_extra_sec': round(tod_extra_sec, 3),
                    'volatility_extra_sec': round(volatility_extra_sec, 3),
                    'recent_submit_extra_sec': round(recent_submit_extra_sec, 3),
                    'jitter_sec': round(jitter_sec, 3),
                },
                'behavior_metrics': {
                    'cadence_pressure': round(min(1.0, (recent_60m_global / max(1, max_60m_global))), 3),
                    'volatility_score': volatility_score,
                    'volatility_source': volatility_source,
                },
            },
        )

    payload = decision.as_dict()
    payload['kind'] = 'account_protection'
    _log_decision(log_path, payload)
    if write_artifact:
        try:
            write_control_artifact(
                repo_root=repo_root,
                asset=str(ctx.config.asset),
                interval_sec=int(ctx.config.interval_sec),
                name='protection',
                payload=payload,
            )
        except Exception:
            pass
    return decision


def apply_recommended_delay(decision: ProtectionDecision, *, sleep_fn=time.sleep) -> ProtectionDecision:
    if not decision.allowed:
        return decision
    delay = round(float(decision.recommended_delay_sec or 0.0), 3)
    if delay <= 0:
        return decision
    sleep_fn(delay)
    updated = replace(decision, applied_delay_sec=delay)
    if updated.decision_log_path:
        try:
            _log_decision(Path(updated.decision_log_path), {'kind': 'account_protection_delay_applied', **updated.as_dict()})
        except Exception:
            pass
    return updated


def note_protection_submit_attempt(
    *,
    repo_root: str | Path = '.',
    ctx,
    cluster_key: str | None = None,
    transport_status: str | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    cfg = _protection_cfg(ctx)
    mode = _execution_mode(ctx)
    if not bool(cfg.get('enabled', False)):
        return {'enabled': False}
    if bool(cfg.get('live_submit_only', True)) and not execution_mode_uses_broker_submit(mode):
        return {'enabled': True, 'skipped': 'non_broker_submit_mode'}
    repo_root = Path(repo_root).resolve()
    path = _state_path(repo_root, ctx)
    if now_utc is None:
        now_utc = datetime.now(UTC)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    else:
        now_utc = now_utc.astimezone(UTC)
    keep_sec = 7200
    state = _load_state(path)
    now_iso = now_utc.isoformat(timespec='seconds')
    scope_tag = str(getattr(getattr(ctx, 'scope', None), 'scope_tag', '') or '')
    global_times = _clean_recent(list(state.get('global_submit_times_utc') or []), now_utc=now_utc, keep_sec=keep_sec)
    global_times.append(now_iso)
    state['global_submit_times_utc'] = global_times
    state['last_submit_global_at_utc'] = now_iso

    scope_times_map = dict(state.get('scope_submit_times_utc') or {})
    scope_times = _clean_recent(list(scope_times_map.get(scope_tag) or []), now_utc=now_utc, keep_sec=keep_sec)
    scope_times.append(now_iso)
    scope_times_map[scope_tag] = scope_times
    state['scope_submit_times_utc'] = scope_times_map
    last_scope = dict(state.get('last_submit_by_scope_utc') or {})
    last_scope[scope_tag] = now_iso
    state['last_submit_by_scope_utc'] = last_scope

    if cluster_key:
        cluster_times_map = dict(state.get('cluster_submit_times_utc') or {})
        cluster_times = _clean_recent(list(cluster_times_map.get(str(cluster_key)) or []), now_utc=now_utc, keep_sec=keep_sec)
        cluster_times.append(now_iso)
        cluster_times_map[str(cluster_key)] = cluster_times
        state['cluster_submit_times_utc'] = cluster_times_map
        last_cluster = dict(state.get('last_submit_by_cluster_utc') or {})
        last_cluster[str(cluster_key)] = now_iso
        state['last_submit_by_cluster_utc'] = last_cluster

    state['updated_at_utc'] = now_iso
    state['last_transport_status'] = str(transport_status or '')
    _save_state(path, state)
    if cfg.get('decision_log_path') not in (None, ''):
        try:
            _log_decision(_decision_log_path(repo_root, ctx), {
                'kind': 'account_protection_submit_noted',
                'scope_tag': scope_tag,
                'cluster_key': cluster_key,
                'at_utc': now_iso,
                'transport_status': str(transport_status or ''),
            })
        except Exception:
            pass
    state['state_path'] = str(path)
    return state


def build_account_protection_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    now_utc: datetime | None = None,
    write_artifact: bool = True,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    latest = latest_trade_row(repo_root=repo_root, ctx=ctx)
    decision = evaluate_account_protection(
        repo_root=repo_root,
        ctx=ctx,
        latest_trade=latest,
        now_utc=now_utc,
        write_artifact=write_artifact,
    )
    payload = decision.as_dict()
    payload['kind'] = 'account_protection'
    payload['latest_trade'] = latest
    payload['enabled'] = bool(execution_cfg(ctx).get('enabled'))
    payload['provider'] = _provider(ctx)
    payload['mode'] = _execution_mode(ctx)
    return payload


__all__ = [
    'ProtectionDecision',
    'apply_recommended_delay',
    'build_account_protection_payload',
    'evaluate_account_protection',
    'note_protection_submit_attempt',
]
