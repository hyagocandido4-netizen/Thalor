from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from natbin.intelligence.fit import fit_intelligence_pack
from natbin.intelligence.paths import (
    anti_overfit_data_summary_path,
    anti_overfit_tuning_review_path,
)
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
        for day in days:
            base = datetime.fromisoformat(f'{day}T12:00:00+00:00')
            for i in range(18):
                ts = int((base + timedelta(minutes=5 * i)).timestamp())
                ts_values.append(ts)
                con.execute(
                    'INSERT INTO signals_v2 (ts, asset, interval_sec, action, proba_up, conf, score, ev, payout, reason, executed_today) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (ts, ASSET, INTERVAL, 'CALL' if i % 2 == 0 else 'PUT', 0.63 if i % 2 == 0 else 0.37, 0.58, 0.42, 0.02, 0.80, 'topk_emit', (i % 3) + 1),
                )
        con.commit()
    finally:
        con.close()
    return ts_values


def _write_dataset(path: Path, ts_values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {'ts': ts, 'y_open_close': 1.0 if idx % 2 == 0 else 0.0}
        for idx, ts in enumerate(ts_values)
    ]).to_csv(path, index=False)


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
                '12': {'trades': 6, 'wins': 3 + idx, 'losses': max(0, 3 - idx), 'win_rate': (3 + idx) / 6.0, 'ev_mean': 0.02},
                '13': {'trades': 6, 'wins': 4 + idx, 'losses': max(0, 2 - idx), 'win_rate': (4 + idx) / 6.0, 'ev_mean': 0.03},
            },
        }
        path = daily_summary_path(day=day, asset=ASSET, interval_sec=INTERVAL, out_dir=runs_dir)
        path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_p22_data_') as tmp:
        repo_root = Path(tmp)
        cfg_path = _write_config(repo_root)
        days = _recent_days()
        signals_db = repo_root / 'runs' / 'signals' / SCOPE_TAG / 'live_signals.sqlite3'
        dataset_path = repo_root / 'data' / 'datasets' / SCOPE_TAG / 'dataset.csv'
        ts_values = _write_signals_db(signals_db, days)
        _write_dataset(dataset_path, ts_values)
        _write_daily_summaries(repo_root, days)

        pack, _ = fit_intelligence_pack(
            repo_root=repo_root,
            config_path=cfg_path,
            asset=ASSET,
            interval_sec=INTERVAL,
            lookback_days=4,
            signals_db_path=signals_db,
            dataset_path=dataset_path,
        )
        source = (pack.get('metadata') or {}).get('anti_overfit_source') or {}
        assert source.get('kind') == 'daily_hourly_summary_fallback', source
        data_summary = anti_overfit_data_summary_path(repo_root=repo_root, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
        assert data_summary.exists(), 'anti_overfit_data_summary.json missing'

        paths = _artifact_paths(
            repo_root=repo_root,
            cfg_path=cfg_path,
            runtime_profile='live_controlled_practice',
            scope_tag=SCOPE_TAG,
            artifact_dir='runs/intelligence',
        )
        paths['pack'].write_text(json.dumps(pack), encoding='utf-8')
        paths['latest_eval'].write_text(json.dumps({'kind': 'intelligence_eval', 'anti_overfit': pack.get('anti_overfit')}), encoding='utf-8')
        paths['retrain_plan'].write_text(json.dumps({'kind': 'retrain_plan', 'state': 'cooldown', 'priority': 'high'}), encoding='utf-8')
        paths['retrain_status'].write_text(json.dumps({'kind': 'retrain_status', 'state': 'rejected', 'priority': 'high'}), encoding='utf-8')
        paths['retrain_review'].write_text(json.dumps({'kind': 'retrain_review', 'verdict': 'rejected'}), encoding='utf-8')
        paths['portfolio_cycle'].write_text(json.dumps({'candidates': []}), encoding='utf-8')
        paths['portfolio_allocation'].write_text(json.dumps({'selected': [], 'suppressed': []}), encoding='utf-8')
        tuning_live = paths['anti_overfit_tuning']
        if tuning_live.exists():
            tuning_live.unlink()
        tuning_review = anti_overfit_tuning_review_path(repo_root=repo_root, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
        tuning_review.write_text(json.dumps({
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
        }, indent=2), encoding='utf-8')
        metrics = _metrics_from_paths(paths, scope_tag=SCOPE_TAG)
        assert metrics['anti_overfit_tuning_present'] is True, metrics
        assert metrics['anti_overfit_tuning_source'] == 'review', metrics
        assert metrics['anti_overfit_tuning_selected_variant'] == 'recent_balanced_relief', metrics
    print('p22_anti_overfit_data_1_smoke: OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
