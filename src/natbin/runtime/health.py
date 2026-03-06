from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runtime_perf import write_text_if_changed
from .scope import build_scope, loop_status_path as scoped_loop_status_path

try:
    from .scope import health_snapshot_path as scoped_health_snapshot_path
except Exception:
    def scoped_health_snapshot_path(*, asset: str, interval_sec: int, out_dir: str | Path = 'runs') -> Path:
        scope = build_scope(asset, interval_sec)
        return Path(out_dir) / 'health' / f"health_latest_{scope.scope_tag}.json"


def _fmt_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _safe(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, dict):
        return {str(k): _safe(val) for k, val in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_safe(x) for x in v]
    try:
        json.dumps(v)
        return v
    except Exception:
        return str(v)


def build_status_payload(*, asset: str, interval_sec: int, phase: str, state: str, message: str, next_wake_utc: str | None = None, sleep_reason: str | None = None, report: dict[str, Any] | None = None, quota: dict[str, Any] | None = None, failsafe: dict[str, Any] | None = None, market_context: dict[str, Any] | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        'at_utc': _fmt_utc(now),
        'phase': str(phase),
        'state': str(state),
        'message': str(message),
        'asset': str(asset),
        'interval_sec': int(interval_sec),
        'next_wake_utc': str(next_wake_utc) if next_wake_utc else None,
        'sleep_reason': str(sleep_reason) if sleep_reason else None,
        'quota': _safe(quota or {}),
        'failsafe': _safe(failsafe or {}),
        'market_context': _safe(market_context or {}),
        'report': _safe(report or {}),
    }


def write_status_payload(*, asset: str, interval_sec: int, payload: dict[str, Any], out_dir: str | Path = 'runs') -> Path:
    p = scoped_loop_status_path(asset=asset, interval_sec=interval_sec, out_dir=out_dir)
    write_text_if_changed(p, json.dumps(_safe(payload), indent=2, ensure_ascii=False), encoding='utf-8')
    return p


def build_health_payload(*, asset: str, interval_sec: int, state: str, message: str, quota: dict[str, Any] | None = None, failsafe: dict[str, Any] | None = None, market_context: dict[str, Any] | None = None, last_cycle_ok: bool | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        'at_utc': _fmt_utc(now),
        'asset': str(asset),
        'interval_sec': int(interval_sec),
        'state': str(state),
        'message': str(message),
        'last_cycle_ok': last_cycle_ok,
        'quota': _safe(quota or {}),
        'failsafe': _safe(failsafe or {}),
        'market_context': _safe(market_context or {}),
    }


def write_health_payload(*, asset: str, interval_sec: int, payload: dict[str, Any], out_dir: str | Path = 'runs') -> Path:
    p = scoped_health_snapshot_path(asset=asset, interval_sec=interval_sec, out_dir=out_dir)
    write_text_if_changed(p, json.dumps(_safe(payload), indent=2, ensure_ascii=False), encoding='utf-8')
    return p
