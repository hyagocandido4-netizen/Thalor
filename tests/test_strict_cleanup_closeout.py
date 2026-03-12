from __future__ import annotations

import json
from pathlib import Path

from natbin.config.paths import resolve_config_path
from natbin.control.plan import build_context
from natbin.ops.lockfile import acquire_lock, release_lock
from natbin.runtime.daemon import run_once
from natbin.runtime.scope import daemon_lock_path, repo_scope


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / 'src' / 'natbin'


def test_legacy_patch_scripts_removed() -> None:
    banned = [
        REPO_ROOT / 'scripts' / 'setup' / 'phase2_1_patch.ps1',
        REPO_ROOT / 'scripts' / 'setup' / 'phase2_1_patch_v2.ps1',
        REPO_ROOT / 'scripts' / 'setup' / 'phase2_1_fix_main.ps1',
        REPO_ROOT / 'scripts' / 'tools' / 'package_w_cleanup.ps1',
    ]
    assert not any(p.exists() for p in banned)


def test_root_shims_are_canonical() -> None:
    expected = {
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
        'dsio.py': '.research.dsio',
        'train_walkforward.py': '.research.train_walkforward',
    }
    for name, target in expected.items():
        txt = (SRC_ROOT / name).read_text(encoding='utf-8', errors='replace')
        assert 'Compatibility shim.' in txt
        assert f'from {target} import *' in txt


def test_effective_config_control_artifact_emitted_per_context() -> None:
    cfg = resolve_config_path(repo_root=REPO_ROOT)
    ctx = build_context(repo_root=REPO_ROOT, config_path=cfg, dump_snapshot=True)
    latest = Path(ctx.scoped_paths['effective_config'])
    control = Path(ctx.scoped_paths['effective_config_control'])
    snapshot_raw = ctx.scoped_paths.get('effective_config_snapshot')
    assert latest.exists()
    assert control.exists()
    if snapshot_raw:
        assert Path(snapshot_raw).exists()
    payload = json.loads(control.read_text(encoding='utf-8'))
    assert payload['latest_path'] == str(latest)
    assert 'generated_at_utc' in payload
    assert 'cycle_id' in payload
    assert isinstance(payload.get('resolved_config'), dict)


def test_run_once_respects_daemon_lock() -> None:
    cfg = resolve_config_path(repo_root=REPO_ROOT)
    scope = repo_scope(config_path=cfg, repo_root=REPO_ROOT)
    lock_path = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=REPO_ROOT / 'runs')
    res = acquire_lock(lock_path, owner={'test': 'strict_cleanup'})
    assert res.acquired
    try:
        payload = run_once(repo_root=REPO_ROOT, topk=1, lookback_candles=10, stop_on_failure=False)
        assert payload['ok'] is False
        assert str(payload['message']).startswith('lock_exists:')
        assert payload['lock_mode'] == 'once'
    finally:
        release_lock(lock_path)


def test_internal_source_avoids_root_compat_imports() -> None:
    banned = [
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
    offenders: list[str] = []
    for py in SRC_ROOT.rglob('*.py'):
        if py.parent == SRC_ROOT:
            continue
        txt = py.read_text(encoding='utf-8', errors='replace')
        for needle in banned:
            if needle in txt:
                offenders.append(f"{py.relative_to(REPO_ROOT)}::{needle}")
    assert not offenders, offenders
