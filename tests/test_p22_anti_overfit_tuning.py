from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from natbin.intelligence.fit import fit_intelligence_pack
from natbin.intelligence.paths import anti_overfit_tuning_path, pack_path
from natbin.intelligence.tuning import tune_anti_overfit

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
    rows = []
    for i in range(120):
        rows.append({'ts': base_ts + i * INTERVAL, 'y_open_close': 1.0 if i % 2 == 0 else 0.0})
    pd.DataFrame(rows).to_csv(path, index=False)


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



def test_tune_anti_overfit_selects_relief_variant_when_objective_improves() -> None:
    summary = {
        'per_window': [
            {'topk_hit_weighted': 0.35, 'topk_taken': 20},
            {'topk_hit_weighted': 0.38, 'topk_taken': 20},
            {'topk_hit_weighted': 0.36, 'topk_taken': 20},
        ]
    }
    rows = [{'ts': 1773136800 + i * INTERVAL, 'correct': i % 2 == 0} for i in range(80)]
    tuning, selected = tune_anti_overfit(
        summary_payload=summary,
        summary_source_kind='multiwindow_summary',
        training_rows=rows,
        timezone_name='UTC',
        base_min_robustness=0.50,
        base_min_windows=3,
        base_gap_penalty_weight=0.10,
        tuning_enable=True,
        min_robustness_floor=0.45,
        window_flex=1,
        gap_penalty_flex=0.03,
        recent_rows_min=40,
        objective_min_delta=0.01,
    )
    assert (tuning.get('baseline') or {}).get('report', {}).get('accepted') is False
    assert selected.get('accepted') is True
    assert tuning['selected_variant'] != 'baseline'
    assert bool(tuning.get('improved')) is True



def test_fit_intelligence_pack_materializes_tuning_artifact_and_updates_pack(tmp_path: Path, monkeypatch) -> None:
    cfg_path = _write_config(tmp_path)
    signals_db = tmp_path / 'runs' / 'signals' / SCOPE_TAG / 'live_signals.sqlite3'
    dataset_path = tmp_path / 'data' / 'datasets' / SCOPE_TAG / 'dataset.csv'
    multiwindow_summary = tmp_path / 'tune' / 'summary.json'
    _write_signals_db(signals_db)
    _write_dataset(dataset_path)
    _write_multiwindow_summary(multiwindow_summary)

    monkeypatch.chdir(tmp_path)
    pack, out = fit_intelligence_pack(
        repo_root=tmp_path,
        config_path=cfg_path,
        asset=ASSET,
        interval_sec=INTERVAL,
        lookback_days=2,
        signals_db_path=signals_db,
        dataset_path=dataset_path,
        multiwindow_summary_path=multiwindow_summary,
    )

    assert out == pack_path(repo_root=tmp_path, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
    tuning_path = anti_overfit_tuning_path(repo_root=tmp_path, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
    assert tuning_path.exists()
    tuning_payload = json.loads(tuning_path.read_text(encoding='utf-8'))
    assert tuning_payload['selected_variant'] == pack['anti_overfit_tuning']['selected_variant']
    assert pack['anti_overfit']['available'] is True
    assert pack['anti_overfit']['accepted'] is True
    assert pack['anti_overfit_tuning']['selected_variant'] != 'baseline'
    assert bool(pack['metadata']['anti_overfit_source']['tuned']) is True
