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


def _write_config(repo: Path) -> None:
    (repo / 'runs').mkdir(parents=True, exist_ok=True)
    (repo / 'config.yaml').write_text(
        'data:\n  asset: EURUSD-OTC\n  interval_sec: 300\n  timezone: UTC\n',
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


def main() -> None:
    if pacing_allowed(k=3, pacing_enabled=True, sec_of_day=0) != 1:
        _fail('pacing_allowed start-of-day mismatch')
    if pacing_allowed(k=3, pacing_enabled=True, sec_of_day=8 * 3600) != 2:
        _fail('pacing_allowed 08:00 mismatch')
    if next_pacing_slot_seconds(k=3, allowed_now=1) != 28800:
        _fail('next_pacing_slot_seconds for 1/3 mismatch')
    _ok('pacing helpers ok')

    tmp = Path(tempfile.mkdtemp(prefix='thalor_quota_smoke_'))
    try:
        _write_config(tmp)
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

        py = ROOT / '.venv' / 'Scripts' / 'python.exe'
        if not py.exists():
            py = Path(sys.executable)
        env = dict(os.environ)
        env['PYTHONPATH'] = str(SRC) + ((env.get('PYTHONPATH') and (os.pathsep + env['PYTHONPATH'])) or '')
        env['TOPK_PACING_ENABLE'] = '1'
        cp = subprocess.run([str(py), '-m', 'natbin.runtime_daemon', '--repo-root', str(tmp), '--topk', '3', '--quota-json'], cwd=str(ROOT), capture_output=True, text=True, env=env)
        if cp.returncode != 0:
            _fail(f'runtime_daemon --quota-json returned {cp.returncode}: {cp.stderr}')
        try:
            payload = json.loads(cp.stdout)
        except Exception as e:
            _fail(f'runtime_daemon --quota-json not json: {e}')
        if payload.get('kind') != MAX_K_REACHED:
            _fail(f'expected daemon quota-json max_k, got {payload}')
        _ok('runtime_daemon --quota-json ok')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
