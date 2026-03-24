from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from natbin.intelligence.fit import fit_intelligence_pack
from natbin.intelligence.paths import (
    anti_overfit_data_summary_path,
    anti_overfit_summary_path,
    anti_overfit_tuning_path,
    anti_overfit_tuning_review_path,
    latest_eval_path,
    pack_path,
    retrain_plan_path,
    retrain_review_path,
    retrain_status_path,
)
from natbin.intelligence.recovery import synthesize_multiwindow_summary_from_signal_rows
from natbin.ops.retrain_ops import _artifact_paths, _metrics_from_paths
from natbin.state.summary_paths import daily_summary_path

ASSET = 'EURUSD-OTC'
INTERVAL = 300
SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join([
            'version: "2.0"',
            'runtime:',
            '  profile: live_controlled_practice',
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
            '  anti_overfit_tuning_recent_rows_min: 20',
            '  anti_overfit_tuning_objective_min_delta: 0.01',
            '',
        ]),
        encoding='utf-8',
    )
    return cfg


def _recent_days() -> list[str]:
    today = datetime.now(tz=UTC).date()
    return [str(today - timedelta(days=2)), str(today - timedelta(days=1)), str(today)]


def _write_signals_db(path: Path, days: list[str]) -> list[int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts_values: list[int] = []
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            'CREATE TABLE signals_v2 (ts INTEGER, asset TEXT, interval_sec INTEGER, action TEXT, proba_up REAL, conf REAL, score REAL, ev REAL, payout REAL, reason TEXT, executed_today INTEGER)'
        )
        for day_idx, day in enumerate(days):
            base = datetime.fromisoformat(f'{day}T12:00:00+00:00')
            for i in range(24):
                ts = int((base + timedelta(minutes=5 * i)).timestamp())
                ts_values.append(ts)
                con.execute(
                    'INSERT INTO signals_v2 (ts, asset, interval_sec, action, proba_up, conf, score, ev, payout, reason, executed_today) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (ts, ASSET, INTERVAL, 'CALL' if i % 3 != 0 else 'PUT', 0.62 if i % 2 == 0 else 0.38, 0.57, 0.41, 0.02, 0.80, 'topk_emit', (i % 3) + 1),
                )
        con.commit()
    finally:
        con.close()
    return ts_values


def _write_dataset(path: Path, ts_values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for idx, ts in enumerate(ts_values):
        rows.append({'ts': ts, 'y_open_close': 1.0 if idx % 2 == 0 else 0.0})
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_daily_summaries(repo_root: Path, days: list[str]) -> None:
    runs_dir = repo_root / 'runs'
    runs_dir.mkdir(parents=True, exist_ok=True)
    for idx, day in enumerate(days):
        payload = {
            'kind': 'daily_summary',
            'day': day,
            'asset': ASSET,
            'interval_sec': INTERVAL,
            'timezone': 'UTC',
            'trades_eval_total': 12,
            'wins_eval_total': 7 + idx,
            'win_rate_eval_total': (7 + idx) / 12.0,
            'by_hour': {
                '12': {'trades': 6, 'wins': 3 + idx, 'losses': 3 - idx if idx < 3 else 0, 'win_rate': (3 + idx) / 6.0, 'ev_mean': 0.02},
                '13': {'trades': 6, 'wins': 4 + idx, 'losses': 2 - idx if idx < 2 else 0, 'win_rate': (4 + idx) / 6.0, 'ev_mean': 0.03},
            },
            'winrate_by_slot': {
                '1': {'slot': 1, 'trades': 6, 'wins': 3 + idx, 'win_rate': (3 + idx) / 6.0, 'ev_avg': 0.02, 'score_avg': 0.41},
                '2': {'slot': 2, 'trades': 6, 'wins': 4 + idx, 'win_rate': (4 + idx) / 6.0, 'ev_avg': 0.03, 'score_avg': 0.42},
            },
        }
        path = daily_summary_path(day=day, asset=ASSET, interval_sec=INTERVAL, out_dir=runs_dir)
        path.write_text(json.dumps(payload, indent=2), encoding='utf-8')



def test_synthesize_multiwindow_summary_from_signal_rows_uses_real_windows() -> None:
    base = int(datetime.now(tz=UTC).timestamp())
    rows = []
    for idx in range(18):
        rows.append({
            'ts': base + idx * INTERVAL,
            'correct': idx % 2 == 0,
            'inferred_direction': idx % 5 == 0,
        })
    payload = synthesize_multiwindow_summary_from_signal_rows(
        rows,
        timezone_name='UTC',
        min_windows=3,
        min_trades_window=1,
    )
    assert payload is not None
    assert payload['source'] == 'signals_eval_fallback'
    assert isinstance(payload.get('per_window'), list) and len(payload['per_window']) >= 3



def test_fit_intelligence_pack_materializes_real_data_summary_before_training_rows(tmp_path: Path, monkeypatch) -> None:
    cfg_path = _write_config(tmp_path)
    days = _recent_days()
    signals_db = tmp_path / 'runs' / 'signals' / SCOPE_TAG / 'live_signals.sqlite3'
    dataset_path = tmp_path / 'data' / 'datasets' / SCOPE_TAG / 'dataset.csv'
    ts_values = _write_signals_db(signals_db, days)
    _write_dataset(dataset_path, ts_values)
    _write_daily_summaries(tmp_path, days)

    monkeypatch.chdir(tmp_path)
    pack, out = fit_intelligence_pack(
        repo_root=tmp_path,
        config_path=cfg_path,
        asset=ASSET,
        interval_sec=INTERVAL,
        lookback_days=4,
        signals_db_path=signals_db,
        dataset_path=dataset_path,
        multiwindow_summary_path=None,
    )

    assert out == pack_path(repo_root=tmp_path, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
    source_meta = pack['metadata']['anti_overfit_source']
    assert source_meta['kind'] == 'daily_hourly_summary_fallback'
    assert source_meta['data_materialized_path']
    materialized = anti_overfit_summary_path(repo_root=tmp_path, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
    data_materialized = anti_overfit_data_summary_path(repo_root=tmp_path, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
    assert materialized.exists()
    assert data_materialized.exists()
    data_payload = json.loads(data_materialized.read_text(encoding='utf-8'))
    assert data_payload['source'] == 'daily_hourly_summary_fallback'
    assert len(data_payload['per_window']) >= 3
    assert anti_overfit_tuning_path(repo_root=tmp_path, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence').exists()



def test_metrics_fallback_to_tuning_review_when_live_tuning_artifact_restored(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    runtime_profile = 'live_controlled_practice'
    paths = _artifact_paths(
        repo_root=tmp_path,
        cfg_path=cfg_path,
        runtime_profile=runtime_profile,
        scope_tag=SCOPE_TAG,
        artifact_dir='runs/intelligence',
    )

    paths['pack'].write_text(json.dumps({'kind': 'intelligence_pack', 'metadata': {'training_rows': 42}}), encoding='utf-8')
    paths['latest_eval'].write_text(json.dumps({'kind': 'intelligence_eval', 'anti_overfit': {'available': True, 'accepted': False}}), encoding='utf-8')
    paths['retrain_plan'].write_text(json.dumps({'kind': 'retrain_plan', 'state': 'cooldown', 'priority': 'high'}), encoding='utf-8')
    paths['retrain_status'].write_text(json.dumps({'kind': 'retrain_status', 'state': 'rejected', 'priority': 'high'}), encoding='utf-8')
    paths['retrain_review'].write_text(json.dumps({'kind': 'retrain_review', 'verdict': 'rejected'}), encoding='utf-8')
    paths['portfolio_cycle'].write_text(json.dumps({'candidates': []}), encoding='utf-8')
    paths['portfolio_allocation'].write_text(json.dumps({'selected': [], 'suppressed': []}), encoding='utf-8')
    tuning_review_path = anti_overfit_tuning_review_path(repo_root=tmp_path, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
    tuning_review_path.write_text(
        json.dumps(
            {
                'kind': 'anti_overfit_tuning_review',
                'verdict': 'rejected',
                'tuning': {
                    'selected_variant': 'recent_balanced_relief',
                    'baseline_variant': 'baseline',
                    'improved': True,
                    'selection_reason': 'objective_improved',
                    'selected': {'objective': 0.73},
                    'baseline': {'objective': 0.41},
                },
            },
            indent=2,
        ),
        encoding='utf-8',
    )

    metrics = _metrics_from_paths(paths, scope_tag=SCOPE_TAG)
    assert metrics['anti_overfit_tuning_present'] is True
    assert metrics['anti_overfit_tuning_source'] == 'review'
    assert metrics['anti_overfit_tuning_selected_variant'] == 'recent_balanced_relief'
    assert metrics['anti_overfit_tuning_improved'] is True
