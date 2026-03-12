from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from natbin.control.plan import build_context
from natbin.ops.artifact_retention import build_retention_payload


def _touch(path: Path, body: str = 'x\n', *, days_old: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    if days_old > 0:
        ts = (datetime.now(UTC) - timedelta(days=days_old)).timestamp()
        os.utime(path, (ts, ts))


def _seed_repo(repo: Path) -> Path:
    cfg = repo / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  runtime_retention_days: 7',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    build_context(repo_root=repo, config_path=cfg, dump_snapshot=False)
    return cfg


def test_artifact_retention_preview_and_apply(tmp_path: Path) -> None:
    cfg = _seed_repo(tmp_path)

    _touch(tmp_path / 'runs' / 'logs' / 'runtime_old.log', days_old=10)
    _touch(tmp_path / 'runs' / 'tests' / 'suite_old.json', '{}\n', days_old=12)
    _touch(tmp_path / 'runs' / 'incidents' / 'reports' / 'incident_report_old.json', '{}\n', days_old=15)
    _touch(tmp_path / 'runs' / 'daily_summary_20240101_EURUSD-OTC_300s.json', '{}\n', days_old=30)

    cfg_dir = tmp_path / 'runs' / 'config'
    _touch(cfg_dir / 'effective_config_20260101_EURUSD-OTC_300s_010101.json', '{}\n', days_old=20)
    _touch(cfg_dir / 'effective_config_20260102_EURUSD-OTC_300s_020202.json', '{}\n', days_old=18)
    _touch(cfg_dir / 'effective_config_20260103_EURUSD-OTC_300s_030303.json', '{}\n', days_old=16)

    preview = build_retention_payload(
        repo_root=tmp_path,
        config_path=cfg,
        apply=False,
        days=7,
        keep_effective_config_snapshots=1,
    )
    assert preview['candidates_total'] >= 4
    cats = dict(preview['categories'])
    assert cats.get('runtime_log', 0) >= 1
    assert cats.get('effective_config_snapshot', 0) >= 1

    applied = build_retention_payload(
        repo_root=tmp_path,
        config_path=cfg,
        apply=True,
        days=7,
        keep_effective_config_snapshots=1,
    )
    assert applied['ok'] is True
    assert applied['deleted_total'] >= 4
    assert not (tmp_path / 'runs' / 'logs' / 'runtime_old.log').exists()
    assert not (tmp_path / 'runs' / 'tests' / 'suite_old.json').exists()
    assert (cfg_dir / 'effective_config_20260103_EURUSD-OTC_300s_030303.json').exists()
