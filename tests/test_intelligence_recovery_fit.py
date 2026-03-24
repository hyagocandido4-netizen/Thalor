from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from natbin.intelligence.fit import fit_intelligence_pack
from natbin.portfolio.paths import resolve_scope_runtime_paths
from natbin.state.summary_paths import daily_summary_path


ASSET = 'EURUSD-OTC'
INTERVAL = 300
SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: live_controlled_practice',
                'multi_asset:',
                '  enabled: false',
                'quota:',
                '  target_trades_per_day: 1',
                'decision:',
                '  tune_dir: runs/tune_mw_topk_20260223_222559',
                'assets:',
                f'  - asset: {ASSET}',
                f'    interval_sec: {INTERVAL}',
                '    timezone: UTC',
                'intelligence:',
                '  enabled: true',
                '  artifact_dir: runs/intelligence',
                '  learned_gating_enable: true',
                '  learned_gating_min_rows: 50',
                '  anti_overfit_enable: true',
                '  anti_overfit_min_windows: 3',
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def _write_dataset(path: Path, ts_values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for idx, ts in enumerate(ts_values):
        y = 1.0 if idx % 2 == 0 else 0.0
        rows.append({'ts': ts, 'y_open_close': y})
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_signals_db(path: Path, ts_values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            'CREATE TABLE signals_v2 (ts INTEGER, asset TEXT, interval_sec INTEGER, action TEXT, proba_up REAL, conf REAL, score REAL, ev REAL, payout REAL, reason TEXT, executed_today INTEGER)'
        )
        # few explicit trades
        for idx, ts in enumerate(ts_values[:6]):
            action = 'CALL' if idx % 2 == 0 else 'PUT'
            if idx in {1, 4}:
                action = 'PUT' if action == 'CALL' else 'CALL'
            proba = 0.82 if action == 'CALL' else 0.18
            con.execute(
                'INSERT INTO signals_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (ts, ASSET, INTERVAL, action, proba, 0.74, 0.61, 0.12 if action == 'CALL' else -0.03, 0.8, 'topk_emit', idx % 3),
            )
        # many HOLD rows that need inference recovery
        for idx, ts in enumerate(ts_values[6:120], start=6):
            aligned = 0.81 if idx % 2 == 0 else 0.19
            proba = (1.0 - aligned) if idx % 7 == 0 else aligned
            con.execute(
                'INSERT INTO signals_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (ts, ASSET, INTERVAL, 'HOLD', proba, 0.55, 0.0, 0.0, 0.8, 'regime_block', idx % 3),
            )
        con.commit()
    finally:
        con.close()


def _write_daily_summaries(repo_root: Path) -> None:
    runs_dir = repo_root / 'runs'
    runs_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=UTC)
    payloads = [
        (0, 2, 0.50),
        (1, 3, 0.67),
        (2, 1, 1.00),
        (3, 1, 0.00),
    ]
    for days_back, trades_eval_total, win_rate in payloads:
        day = (now - timedelta(days=days_back)).strftime('%Y-%m-%d')
        payload = {
            'day': day,
            'asset': ASSET,
            'interval_sec': INTERVAL,
            'timezone': 'UTC',
            'trades_eval_total': trades_eval_total,
            'wins_eval_total': int(round(trades_eval_total * win_rate)),
            'win_rate_eval_total': win_rate,
            'trades_total': trades_eval_total,
            'by_hour': {'10': {'trades': trades_eval_total, 'wins': int(round(trades_eval_total * win_rate)), 'losses': max(0, trades_eval_total - int(round(trades_eval_total * win_rate))), 'ev_mean': 0.03}},
            'trades_by_hour': {'10': {'total': trades_eval_total, 'CALL': trades_eval_total, 'PUT': 0}},
            'observations_by_hour': {'10': max(1, trades_eval_total * 4)},
        }
        daily_summary_path(day=day, asset=ASSET, interval_sec=INTERVAL, out_dir=runs_dir).write_text(
            json.dumps(payload, indent=2),
            encoding='utf-8',
        )


def test_fit_intelligence_pack_recovers_training_rows_and_anti_overfit(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    base_ts = 1773000000
    ts_values = [base_ts + (i * INTERVAL) for i in range(120)]
    dataset_path = tmp_path / 'data' / 'dataset_phase2.csv'
    _write_dataset(dataset_path, ts_values)
    _write_daily_summaries(tmp_path)

    global_signals = resolve_scope_runtime_paths(tmp_path, scope_tag=SCOPE_TAG, partition_enable=False).signals_db_path
    _write_signals_db(global_signals, ts_values)

    pack, out = fit_intelligence_pack(
        repo_root=tmp_path,
        config_path=cfg,
        asset=ASSET,
        interval_sec=INTERVAL,
        lookback_days=5,
    )

    assert out.exists()
    meta = dict(pack.get('metadata') or {})
    assert meta.get('training_rows', 0) >= 100
    assert meta.get('training_strategy') == 'recovered_with_inferred_hold_rows'
    assert meta.get('inferred_hold_rows', 0) > 0
    assert any('runs/live_signals.sqlite3' in str(item.get('signals_db_path') or '') for item in list(meta.get('training_sources') or []))
    assert pack.get('learned_gate') is not None
    assert pack['anti_overfit']['available'] is True
    assert meta.get('anti_overfit_source', {}).get('kind') == 'daily_hourly_summary_fallback'
