from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd

from natbin.intelligence.fit import fit_intelligence_pack
from natbin.intelligence.paths import anti_overfit_tuning_path

ASSET = 'EURUSD-OTC'
INTERVAL = 300
SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join([
            'version: "2.0"',
            'multi_asset:',
            '  enabled: true',
            '  partition_data_paths: true',
            'assets:',
            f'  - asset: {ASSET}',
            f'    interval_sec: {INTERVAL}',
            '    timezone: UTC',
            'intelligence:',
            '  enabled: true',
            '  artifact_dir: runs/intelligence',
            '  learned_gating_enable: true',
            '  learned_gating_min_rows: 20',
            '  anti_overfit_enable: true',
            '  anti_overfit_min_robustness: 0.50',
            '  anti_overfit_min_windows: 3',
            '  anti_overfit_gap_penalty_weight: 0.10',
            '  anti_overfit_tuning_enable: true',
            '  anti_overfit_tuning_min_robustness_floor: 0.45',
            '  anti_overfit_tuning_window_flex: 1',
            '  anti_overfit_tuning_gap_penalty_flex: 0.03',
            '  anti_overfit_tuning_recent_rows_min: 40',
            '  anti_overfit_tuning_objective_min_delta: 0.01',
            '',
        ]),
        encoding='utf-8',
    )
    return cfg


def _write_signals_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            'CREATE TABLE signals_v2 (ts INTEGER, asset TEXT, interval_sec INTEGER, action TEXT, proba_up REAL, conf REAL, score REAL, ev REAL, payout REAL, reason TEXT, executed_today INTEGER)'
        )
        base_ts = 1773136800
        for i in range(120):
            con.execute(
                'INSERT INTO signals_v2 (ts, asset, interval_sec, action, proba_up, conf, score, ev, payout, reason, executed_today) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (base_ts + i * INTERVAL, ASSET, INTERVAL, 'CALL', 0.64 if i % 2 == 0 else 0.36, 0.58, 0.44, 0.02, 0.80, 'topk_emit', i % 3),
            )
        con.commit()
    finally:
        con.close()


def _write_dataset(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_ts = 1773136800
    pd.DataFrame([
        {'ts': base_ts + i * INTERVAL, 'y_open_close': 1.0 if i % 2 == 0 else 0.0}
        for i in range(120)
    ]).to_csv(path, index=False)


def _write_multiwindow_summary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'per_window': [
            {'topk_hit_weighted': 0.35, 'topk_taken': 20},
            {'topk_hit_weighted': 0.38, 'topk_taken': 20},
            {'topk_hit_weighted': 0.36, 'topk_taken': 20},
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_p22_tuning_') as tmp:
        repo_root = Path(tmp)
        cfg_path = _write_config(repo_root)
        signals_db = repo_root / 'runs' / 'signals' / SCOPE_TAG / 'live_signals.sqlite3'
        dataset_path = repo_root / 'data' / 'datasets' / SCOPE_TAG / 'dataset.csv'
        summary_path = repo_root / 'tune' / 'summary.json'
        _write_signals_db(signals_db)
        _write_dataset(dataset_path)
        _write_multiwindow_summary(summary_path)

        pack, _ = fit_intelligence_pack(
            repo_root=repo_root,
            config_path=cfg_path,
            asset=ASSET,
            interval_sec=INTERVAL,
            lookback_days=2,
            signals_db_path=signals_db,
            dataset_path=dataset_path,
            multiwindow_summary_path=summary_path,
        )
        tuning_path = anti_overfit_tuning_path(repo_root=repo_root, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
        assert tuning_path.exists(), 'anti_overfit_tuning.json missing'
        tuning = json.loads(tuning_path.read_text(encoding='utf-8'))
        assert bool(pack['anti_overfit']['available']) is True, pack
        assert bool(pack['anti_overfit']['accepted']) is True, pack
        assert str(tuning.get('selected_variant') or 'baseline') != 'baseline', tuning
        assert bool(tuning.get('improved')) is True, tuning
    print('p22_anti_overfit_tuning_1_smoke: OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
