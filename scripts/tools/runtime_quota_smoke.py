#!/usr/bin/env python
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.runtime_cycle import repo_python_executable
from natbin.runtime_quota import (
    MAX_K_REACHED,
    OPEN,
    PACING_QUOTA_REACHED,
    build_quota_snapshot,
    next_pacing_slot_seconds,
    pacing_allowed,
)
from natbin.runtime_repos import SignalsRepository


def _ok(msg: str) -> None:
    print(f"[smoke][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[smoke][FAIL] {msg}")
    raise SystemExit(2)


def _write_legacy_config(repo: Path, *, asset: str = 'EURUSD-OTC', interval_sec: int = 300, timezone: str = 'UTC') -> None:
    (repo / 'runs').mkdir(parents=True, exist_ok=True)
    (repo / 'config.yaml').write_text(
        f'data:\n  asset: {asset}\n  interval_sec: {int(interval_sec)}\n  timezone: {timezone}\n',
        encoding='utf-8',
    )


def _write_modern_config(repo: Path, *, asset: str = 'GBPUSD-OTC', interval_sec: int = 60, timezone: str = 'UTC') -> None:
    (repo / 'runs').mkdir(parents=True, exist_ok=True)
    (repo / 'config').mkdir(parents=True, exist_ok=True)
    (repo / 'config' / 'base.yaml').write_text(
        '\n'.join([
            'version: "2.0"',
            'assets:',
            f'  - asset: {asset}',
            f'    interval_sec: {int(interval_sec)}',
            f'    timezone: {timezone}',
            '',
        ]),
        encoding='utf-8',
    )


def _trade_row(day: str, ts: int) -> dict[str, object]:
    return {
        'dt_local': f'{day} 00:00:00',
        'day': day,
        'asset': 'EURUSD-OTC',
        'interval_sec': 300,
        'ts': int(ts),
        'proba_up': 0.51,
        'conf': 0.51,
        'score': 0.61,
        'gate_mode': 'cp_meta_iso',
        'gate_mode_requested': 'cp_meta_iso',
        'gate_fail_closed': 0,
        'gate_fail_detail': '',
        'regime_ok': 1,
        'thresh_on': 'ev',
        'threshold': 0.02,
        'k': 3,
        'rank_in_day': 1,
        'executed_today': 1,
        'budget_left': 2,
        'action': 'CALL',
        'reason': 'topk_emit',
        'blockers': '',
        'close': 1.0,
        'payout': 0.85,
        'ev': 0.2,
        'model_version': 'smoke',
        'train_rows': 10,
        'train_end_ts': int(ts),
        'best_source': 'smoke',
        'tune_dir': '',
        'feat_hash': 'smoke',
        'gate_version': 'smoke',
        'meta_model': 'hgb',
        'market_context_stale': 0,
        'market_context_fail_closed': 0,
    }


def _assert_quota_json(repo: Path, *, now_utc: datetime, expected_kind: str, expected_asset: str, expected_interval_sec: int) -> None:
    py = Path(repo_python_executable(ROOT))
    env = dict(os.environ)
    env['PYTHONPATH'] = str(SRC) + ((env.get('PYTHONPATH') and (os.pathsep + env['PYTHONPATH'])) or '')
    env['TOPK_PACING_ENABLE'] = '1'
    cp = subprocess.run([
        str(py),
        '-m', 'natbin.runtime_daemon',
        '--repo-root', str(repo),
        '--topk', '3',
        '--quota-json',
        '--now-utc', now_utc.isoformat(timespec='seconds'),
    ], cwd=str(ROOT), capture_output=True, text=True, env=env)
    if cp.returncode != 0:
        _fail(f'runtime_daemon --quota-json returned {cp.returncode}: {cp.stderr}')
    try:
        payload = json.loads(cp.stdout)
    except Exception as e:
        _fail(f'runtime_daemon --quota-json not json: {e}')
    if payload.get('kind') != expected_kind:
        _fail(f'expected daemon quota-json kind={expected_kind}, got {payload}')
    if payload.get('asset') != expected_asset or int(payload.get('interval_sec') or 0) != int(expected_interval_sec):
        _fail(f'expected daemon quota-json scope={expected_asset}/{expected_interval_sec}, got {payload}')


def main() -> None:
    if pacing_allowed(k=3, pacing_enabled=True, sec_of_day=0) != 1:
        _fail('pacing_allowed start-of-day mismatch')
    if pacing_allowed(k=3, pacing_enabled=True, sec_of_day=8 * 3600) != 2:
        _fail('pacing_allowed 08:00 mismatch')
    if next_pacing_slot_seconds(k=3, allowed_now=1) != 28800:
        _fail('next_pacing_slot_seconds for 1/3 mismatch')
    _ok('pacing helpers ok')

    tmp = Path(tempfile.mkdtemp(prefix='thalor_quota_smoke_'))
    tmp_modern = Path(tempfile.mkdtemp(prefix='thalor_quota_modern_smoke_'))
    try:
        _write_legacy_config(tmp)
        now_utc = datetime(2026, 3, 3, 7, 0, 0, tzinfo=UTC)
        snap0 = build_quota_snapshot(tmp, topk=3, now_utc=now_utc, pacing_enabled=True)
        if snap0.kind != OPEN or snap0.allowed_now != 1 or snap0.executed != 0:
            _fail(f'expected open snapshot at 07:00, got {snap0.as_dict()}')
        _ok('open quota snapshot ok')

        repo = SignalsRepository(tmp / 'runs' / 'live_signals.sqlite3', default_interval=300)
        repo.write_row(_trade_row('2026-03-03', 1772492400))
        snap1 = build_quota_snapshot(tmp, topk=3, now_utc=now_utc, pacing_enabled=True)
        if snap1.kind != PACING_QUOTA_REACHED or snap1.next_at != '08:00':
            _fail(f'expected pacing quota snapshot, got {snap1.as_dict()}')
        _ok('pacing quota snapshot ok')

        repo.write_row(_trade_row('2026-03-03', 1772492700))
        repo.write_row(_trade_row('2026-03-03', 1772493000))
        snap2 = build_quota_snapshot(tmp, topk=3, now_utc=now_utc, pacing_enabled=True)
        if snap2.kind != MAX_K_REACHED or snap2.budget_left_total != 0 or not snap2.next_wake_utc:
            _fail(f'expected max_k snapshot, got {snap2.as_dict()}')
        _ok('max_k quota snapshot ok')

        _assert_quota_json(
            tmp,
            now_utc=now_utc,
            expected_kind=MAX_K_REACHED,
            expected_asset='EURUSD-OTC',
            expected_interval_sec=300,
        )
        _ok('runtime_daemon --quota-json ok (legacy config.yaml)')

        _write_modern_config(tmp_modern, asset='GBPUSD-OTC', interval_sec=60, timezone='UTC')
        modern_now_utc = datetime(2026, 3, 3, 7, 0, 0, tzinfo=UTC)
        modern_snap = build_quota_snapshot(tmp_modern, topk=2, now_utc=modern_now_utc, pacing_enabled=False)
        if modern_snap.kind != OPEN:
            _fail(f'expected open snapshot for config/base.yaml repo, got {modern_snap.as_dict()}')
        if modern_snap.asset != 'GBPUSD-OTC' or modern_snap.interval_sec != 60 or modern_snap.timezone != 'UTC':
            _fail(f'expected config/base.yaml scoped quota snapshot, got {modern_snap.as_dict()}')
        _ok('config/base.yaml quota snapshot ok')

        _assert_quota_json(
            tmp_modern,
            now_utc=modern_now_utc,
            expected_kind=OPEN,
            expected_asset='GBPUSD-OTC',
            expected_interval_sec=60,
        )
        _ok('runtime_daemon --quota-json ok (config/base.yaml)')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp_modern, ignore_errors=True)

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
