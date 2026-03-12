#!/usr/bin/env python
from __future__ import annotations

import json
import os
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / 'src' / 'natbin'

BANNED_FILES = [
    REPO_ROOT / 'scripts' / 'setup' / 'phase2_1_patch.ps1',
    REPO_ROOT / 'scripts' / 'setup' / 'phase2_1_patch_v2.ps1',
    REPO_ROOT / 'scripts' / 'setup' / 'phase2_1_fix_main.ps1',
    REPO_ROOT / 'scripts' / 'tools' / 'package_w_cleanup.ps1',
]

ROOT_SHIMS = {
    'config2.py': '.config.legacy',
    'settings.py': '.config.settings',
    'envutil.py': '.config.env',
    'db.py': '.state.db',
    'runtime_perf.py': '.runtime.perf',
    'runtime_observability.py': '.runtime.observability',
    'backfill_candles.py': '.usecases.backfill_candles',
    'collect_candles.py': '.usecases.collect_candles',
    'refresh_daily_summary.py': '.usecases.refresh_daily_summary',
    'validate_gaps.py': '.usecases.validate_gaps',
    'paper_backtest.py': '.research.paper_backtest',
    'paper_backtest_v2.py': '.research.paper_backtest_v2',
    'paper_backtest_v3.py': '.research.paper_backtest_v3',
    'paper_multiwindow_v3.py': '.research.paper_multiwindow_v3',
    'paper_pnl_backtest.py': '.research.paper_pnl_backtest',
    'paper_topk_multiwindow.py': '.research.paper_topk_multiwindow',
    'paper_topk_perday_multiwindow.py': '.research.paper_topk_perday_multiwindow',
    'paper_tune_v2.py': '.research.paper_tune_v2',
    'risk_report.py': '.research.risk_report',
    'sweep_thresholds.py': '.research.sweep_thresholds',
    'train_walkforward.py': '.research.train_walkforward',
    'tune_multiwindow_topk.py': '.research.tune_multiwindow_topk',
    'dsio.py': '.research.dsio',
}

BANNED_IMPORT_PATTERNS = [
    'from natbin.settings import',
    'from natbin.db import',
    'from natbin.envutil import',
    'from natbin.config2 import',
    'from natbin.runtime_observability import',
    'from natbin.runtime_scope import',
    'from natbin.runtime_perf import',
    'from natbin.summary_paths import',
    'from natbin.iq_client import',
]


def _ok(msg: str) -> None:
    print(f'[strict-cleanup][OK] {msg}')


def _fail(msg: str) -> None:
    raise SystemExit(f'[strict-cleanup][FAIL] {msg}')


for path in BANNED_FILES:
    if path.exists():
        _fail(f'banned legacy patch file still exists: {path.relative_to(REPO_ROOT)}')
_ok('legacy patch scripts removed')

for name, target in ROOT_SHIMS.items():
    path = SRC_ROOT / name
    if not path.exists():
        _fail(f'missing shim: src/natbin/{name}')
    txt = path.read_text(encoding='utf-8', errors='replace')
    if 'Compatibility shim.' not in txt or f'from {target} import *' not in txt:
        _fail(f'root shim not normalized: src/natbin/{name}')
_ok('root shims normalized to canonical subpackages')

for py in SRC_ROOT.rglob('*.py'):
    if py.parent == SRC_ROOT:
        continue
    txt = py.read_text(encoding='utf-8', errors='replace')
    for pat in BANNED_IMPORT_PATTERNS:
        if pat in txt:
            _fail(f'non-canonical import in {py.relative_to(REPO_ROOT)}: {pat}')
_ok('internal source imports use canonical subpackages')

# effective_config and lock contracts
from natbin.control.plan import build_context
from natbin.config.paths import resolve_config_path
from natbin.runtime.daemon import run_once
from natbin.runtime.scope import repo_scope, daemon_lock_path
from natbin.ops.lockfile import acquire_lock, release_lock

cfg_path = resolve_config_path(repo_root=REPO_ROOT)
ctx = build_context(repo_root=REPO_ROOT, config_path=cfg_path, dump_snapshot=True)
latest = Path(ctx.scoped_paths['effective_config'])
control = Path(ctx.scoped_paths['effective_config_control'])
snapshot = Path(ctx.scoped_paths.get('effective_config_snapshot') or '')
if not latest.exists():
    _fail('effective_config latest was not emitted by build_context')
if not control.exists():
    _fail('effective_config control artifact was not emitted by build_context')
if ctx.scoped_paths.get('effective_config_snapshot') and not snapshot.exists():
    _fail('effective_config snapshot path advertised but file not found')
obj = json.loads(control.read_text(encoding='utf-8'))
for key in ('generated_at_utc', 'cycle_id', 'latest_path', 'resolved_config', 'scope'):
    if key not in obj:
        _fail(f'effective_config control artifact missing key: {key}')
_ok('effective_config native cycle artifacts emitted')

scope = repo_scope(config_path=cfg_path, repo_root=REPO_ROOT)
lock_path = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=REPO_ROOT / 'runs')
res = acquire_lock(lock_path, owner={'smoke': 'strict_cleanup'})
if not res.acquired:
    _fail('could not acquire daemon lock for smoke precondition')
try:
    rep = run_once(repo_root=REPO_ROOT, topk=1, lookback_candles=10, stop_on_failure=False)
    if not isinstance(rep, dict) or not str(rep.get('message', '')).startswith('lock_exists:') or str(rep.get('lock_mode')) != 'once':
        _fail(f'run_once did not honor scheduler lock: {rep!r}')
finally:
    release_lock(lock_path)
_ok('run_once honors scheduler lock')

print('[strict-cleanup] ALL OK')
