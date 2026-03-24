from __future__ import annotations

import csv
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from ...config.env import env_int
from ...runtime.scope import live_signals_csv_path as scoped_live_signals_csv_path
from ...runtime_migrations import ensure_executed_state_db as _ensure_executed_state_db
from ...runtime_migrations import ensure_signals_v2 as _ensure_signals_v2
from ...runtime_repos import RuntimeTradeLedger, SignalsRepository, preserve_existing_trade
from ...state.summary_paths import sanitize_asset


BASE_FIELDS = [
    'dt_local',
    'day',
    'ts',
    'interval_sec',
    'proba_up',
    'conf',
    'score',
    'gate_mode',
    'regime_ok',
    'thresh_on',
    'threshold',
    'k',
    'rank_in_day',
    'executed_today',
    'budget_left',
    'action',
    'reason',
    'blockers',
    'close',
    'payout',
    'ev',
    'market_context_stale',
    'market_context_fail_closed',
]
META_FIELDS = [
    'asset',
    'model_version',
    'train_rows',
    'train_end_ts',
    'best_source',
    'tune_dir',
    'feat_hash',
    'gate_version',
    'meta_model',
]
ALL_FIELDS = BASE_FIELDS + META_FIELDS
TRADE_ACTIONS = {'CALL', 'PUT'}


def _env_path(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def resolve_signals_db_path(default: str | Path = 'runs/live_signals.sqlite3') -> Path:
    override = _env_path('THALOR_SIGNALS_DB_PATH') or _env_path('SIGNALS_DB_PATH')
    p = Path(override) if override else Path(default)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def resolve_state_db_path(default: str | Path = 'runs/live_topk_state.sqlite3') -> Path:
    override = _env_path('THALOR_STATE_DB_PATH') or _env_path('STATE_DB_PATH')
    p = Path(override) if override else Path(default)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def runtime_ledger() -> RuntimeTradeLedger:
    return RuntimeTradeLedger(
        signals_db=resolve_signals_db_path('runs/live_signals.sqlite3'),
        state_db=resolve_state_db_path('runs/live_topk_state.sqlite3'),
        default_interval=env_int('SIGNALS_INTERVAL_SEC', '300'),
    )


def ensure_signals_v2(con: sqlite3.Connection) -> None:
    """Compatibility wrapper over the explicit runtime migration module."""
    _ensure_signals_v2(con, default_interval=env_int('SIGNALS_INTERVAL_SEC', '300'))


def ensure_state_db(con: sqlite3.Connection) -> None:
    """Compatibility wrapper over the explicit runtime migration module."""
    _ensure_executed_state_db(con, default_interval=env_int('SIGNALS_INTERVAL_SEC', '300'))


def signal_pk(row: dict[str, Any]) -> tuple[str, str, int, int]:
    day = str(row.get('day') or '')
    asset = str(row.get('asset') or '')
    try:
        interval_sec = int(row.get('interval_sec') or 0)
    except Exception:
        interval_sec = 0
    try:
        ts = int(row.get('ts') or 0)
    except Exception:
        ts = 0
    return day, asset, interval_sec, ts


def write_sqlite_signal(row: dict[str, Any], db_path: str = 'runs/live_signals.sqlite3') -> None:
    repo = SignalsRepository(db_path=resolve_signals_db_path(db_path), default_interval=env_int('SIGNALS_INTERVAL_SEC', '300'))
    repo.write_row(row)


def _default_live_signals_csv_path(row: dict[str, Any]) -> str:
    day = str(row.get('day') or '')
    asset = str(row.get('asset') or 'UNKNOWN')
    try:
        interval_sec = int(row.get('interval_sec') or env_int('SIGNALS_INTERVAL_SEC', '300'))
    except Exception:
        interval_sec = env_int('SIGNALS_INTERVAL_SEC', '300')
    if day:
        return str(scoped_live_signals_csv_path(day=day, asset=asset, interval_sec=interval_sec, out_dir='runs'))
    asset_tag = sanitize_asset(asset)
    return str(Path('runs') / f'live_signals_v2_{asset_tag}_{int(interval_sec)}s.csv')


def _parse_builtin_live_signals_filename(name: str) -> tuple[str | None, str | None, int | None]:
    m = re.match(r'^live_signals_v2_(\d{8})_(.+)_(\d+)s\.csv$', name)
    if m:
        day_tag, asset_tag, interval_tag = m.groups()
        try:
            return day_tag, asset_tag, int(interval_tag)
        except Exception:
            return day_tag, asset_tag, None
    m = re.match(r'^live_signals_v2_(\d{8})\.csv$', name)
    if m:
        return m.group(1), None, None
    m = re.match(r'^live_signals_v2_(.+)_(\d+)s\.csv$', name)
    if m:
        asset_tag, interval_tag = m.groups()
        try:
            return None, asset_tag, int(interval_tag)
        except Exception:
            return None, asset_tag, None
    return None, None, None


def resolve_live_signals_csv_path(row: dict[str, Any]) -> str:
    default_path = _default_live_signals_csv_path(row)
    override = os.getenv('LIVE_SIGNALS_PATH', '').strip()
    if not override:
        return default_path

    row_day = str(row.get('day') or '').replace('-', '')
    row_asset = sanitize_asset(str(row.get('asset') or 'UNKNOWN'))
    try:
        row_interval = int(row.get('interval_sec') or env_int('SIGNALS_INTERVAL_SEC', '300'))
    except Exception:
        row_interval = env_int('SIGNALS_INTERVAL_SEC', '300')

    try:
        name = Path(override).name
        o_day, o_asset, o_interval = _parse_builtin_live_signals_filename(name)
        is_builtin = name.startswith('live_signals_v2_') and name.endswith('.csv')
        if is_builtin:
            if o_day and row_day and o_day != row_day:
                return default_path
            if o_asset and row_asset and o_asset != row_asset:
                return default_path
            if o_interval and int(o_interval) != int(row_interval):
                return default_path
    except Exception:
        return default_path

    return override


def append_csv(row: dict[str, Any]) -> str:
    path = resolve_live_signals_csv_path(row)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def read_header(pp: Path) -> list[str] | None:
        if not pp.exists():
            return None
        try:
            with pp.open('r', encoding='utf-8', newline='') as f:
                r = csv.reader(f)
                return next(r, None)
        except Exception:
            return None

    def read_rows(pp: Path) -> list[dict[str, Any]]:
        if not pp.exists():
            return []
        try:
            with pp.open('r', encoding='utf-8', newline='') as f:
                r = csv.DictReader(f)
                rows: list[dict[str, Any]] = []
                for rr in r:
                    if not rr:
                        continue
                    rows.append({k: rr.get(k, '') for k in ALL_FIELDS})
                return rows
        except Exception:
            return []

    def normalize_ts(v: Any) -> str:
        try:
            return str(int(float(str(v).strip())))
        except Exception:
            return str(v or '')

    def normalize_interval(v: Any) -> str:
        try:
            return str(int(float(str(v).strip())))
        except Exception:
            return str(v or '')

    header = read_header(p)
    if header and header != ALL_FIELDS:
        p = p.with_name(p.stem + '_meta' + p.suffix)

    incoming = {k: row.get(k, '') for k in ALL_FIELDS}
    day, asset, interval_sec, ts = signal_pk(row)
    target_key = (day, asset, str(interval_sec), str(ts))
    last_err: Exception | None = None

    for attempt in range(8):
        try:
            rows = read_rows(p)
            idx = None
            existing_action = None
            for i, rr in enumerate(rows):
                rr_key = (
                    str(rr.get('day') or ''),
                    str(rr.get('asset') or ''),
                    normalize_interval(rr.get('interval_sec')),
                    normalize_ts(rr.get('ts')),
                )
                if rr_key == target_key:
                    idx = i
                    existing_action = rr.get('action')
                    break

            if idx is not None and preserve_existing_trade(existing_action, incoming.get('action')):
                return str(p)

            if idx is None:
                rows.append(incoming)
            else:
                rows[idx] = incoming

            tmp = p.with_suffix(p.suffix + '.tmp')
            with tmp.open('w', encoding='utf-8', newline='') as f:
                w = csv.DictWriter(f, fieldnames=ALL_FIELDS)
                w.writeheader()
                for rr in rows:
                    w.writerow({k: rr.get(k, '') for k in ALL_FIELDS})
            tmp.replace(p)
            return str(p)
        except PermissionError as e:
            last_err = e
            time.sleep(0.25 * (attempt + 1))

    if last_err is not None:
        raise last_err
    raise RuntimeError(f'append_csv failed for {p}')


def state_path() -> Path:
    return resolve_state_db_path(Path('runs') / 'live_topk_state.sqlite3')


def signals_db_path() -> Path:
    return resolve_signals_db_path(Path('runs') / 'live_signals.sqlite3')


def fetch_trade_rows_from_signals(asset: str, interval_sec: int, day: str, *, ts: int | None = None) -> list[sqlite3.Row]:
    return runtime_ledger().signals.fetch_trade_rows(asset, interval_sec, day, ts=ts)


def heal_state_from_signals(asset: str, interval_sec: int, day: str, *, ts: int | None = None) -> int:
    return runtime_ledger().heal_state_from_signals(asset, interval_sec, day, ts=ts, log=True)


def count_state_only(asset: str, interval_sec: int, day: str) -> int:
    return runtime_ledger().state.count_day(asset, interval_sec, day)


def last_state_ts_only(asset: str, interval_sec: int, day: str) -> int | None:
    return runtime_ledger().state.last_ts(asset, interval_sec, day)


def already_state_only(asset: str, interval_sec: int, day: str, ts: int) -> bool:
    return runtime_ledger().state.exists(asset, interval_sec, day, int(ts))


def executed_today_count(asset: str, interval_sec: int, day: str) -> int:
    return runtime_ledger().executed_today_count(asset, interval_sec, day)


def last_executed_ts(asset: str, interval_sec: int, day: str) -> int | None:
    return runtime_ledger().last_executed_ts(asset, interval_sec, day)


def already_executed(asset: str, interval_sec: int, day: str, ts: int) -> bool:
    return runtime_ledger().already_executed(asset, interval_sec, day, int(ts))


def mark_executed(asset: str, interval_sec: int, day: str, ts: int, action: str, conf: float, score: float) -> None:
    runtime_ledger().mark_executed(asset, interval_sec, day, int(ts), action, float(conf), float(score))


__all__ = [
    'ALL_FIELDS',
    'BASE_FIELDS',
    'META_FIELDS',
    'TRADE_ACTIONS',
    'already_executed',
    'already_state_only',
    'append_csv',
    'count_state_only',
    'ensure_signals_v2',
    'ensure_state_db',
    'executed_today_count',
    'fetch_trade_rows_from_signals',
    'heal_state_from_signals',
    'last_executed_ts',
    'last_state_ts_only',
    'mark_executed',
    'resolve_live_signals_csv_path',
    'resolve_signals_db_path',
    'resolve_state_db_path',
    'runtime_ledger',
    'signal_pk',
    'signals_db_path',
    'state_path',
    'write_sqlite_signal',
]
