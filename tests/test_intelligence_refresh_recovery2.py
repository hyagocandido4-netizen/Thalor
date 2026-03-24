from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from natbin.control.commands import intelligence_payload, portfolio_status_payload
from natbin.intelligence.refresh import refresh_config_intelligence
from natbin.portfolio.paths import portfolio_runs_dir, resolve_scope_runtime_paths
from natbin.runtime.scope import decision_latest_path
from natbin.state.summary_paths import daily_summary_path


ASSET = 'EURUSD-OTC'
INTERVAL = 300
SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_config(repo_root: Path, *, name: str = 'live_controlled_practice.yaml', profile: str = 'live_controlled_practice') -> Path:
    cfg = repo_root / 'config' / name
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                f'  profile: {profile}',
                'multi_asset:',
                '  enabled: false',
                'quota:',
                '  target_trades_per_day: 1',
                'decision:',
                '  tune_dir: runs/tune_mw_topk_20260223_222559',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: PRACTICE',
                '  stake:',
                '    amount: 2.0',
                '    currency: BRL',
                '  limits:',
                '    max_pending_unknown: 1',
                '    max_open_positions: 1',
                'intelligence:',
                '  enabled: true',
                '  artifact_dir: runs/intelligence',
                '  learned_gating_enable: true',
                '  learned_gating_min_rows: 50',
                '  anti_overfit_enable: true',
                '  anti_overfit_min_windows: 3',
                'assets:',
                f'  - asset: {ASSET}',
                f'    interval_sec: {INTERVAL}',
                '    timezone: UTC',
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
        for idx, ts in enumerate(ts_values[:8]):
            action = 'CALL' if idx % 2 == 0 else 'PUT'
            if idx in {1, 4, 7}:
                action = 'PUT' if action == 'CALL' else 'CALL'
            proba = 0.82 if action == 'CALL' else 0.18
            con.execute(
                'INSERT INTO signals_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (ts, ASSET, INTERVAL, action, proba, 0.74, 0.61, 0.12 if action == 'CALL' else -0.03, 0.8, 'topk_emit', idx % 3),
            )
        for idx, ts in enumerate(ts_values[8:140], start=8):
            aligned = 0.81 if idx % 2 == 0 else 0.19
            proba = (1.0 - aligned) if idx % 9 == 0 else aligned
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


def _write_decision_latest(repo_root: Path, ts: int) -> None:
    path = decision_latest_path(asset=ASSET, interval_sec=INTERVAL, out_dir=repo_root / 'runs')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                'kind': 'decision',
                'observed_at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
                'asset': ASSET,
                'interval_sec': INTERVAL,
                'day': datetime.now(tz=UTC).strftime('%Y-%m-%d'),
                'ts': int(ts),
                'dt_local': '2026-03-22 11:00:00',
                'action': 'HOLD',
                'reason': 'regime_block',
                'blockers': 'cp_reject;below_ev_threshold;not_in_topk_today',
                'executed_today': 0,
                'budget_left': 1,
                'gate_mode': 'cp_meta_iso',
                'regime_ok': 0,
                'threshold': 0.02,
                'thresh_on': 'ev',
                'k': 1,
                'rank_in_day': -1,
                'payout': 0.8,
                'ev': -1.0,
                'proba_up': 0.49,
                'conf': 0.51,
                'score': 0.0,
                'meta_model': 'hgb',
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )


def _write_legacy_mismatch(repo_root: Path) -> None:
    base = portfolio_runs_dir(repo_root)
    base.mkdir(parents=True, exist_ok=True)
    stale = {
        'cycle_id': 'stale_cycle',
        'allocation_id': 'stale_alloc',
        'finished_at_utc': '2026-03-11T06:20:34+00:00',
        'at_utc': '2026-03-11T06:20:34+00:00',
        'config_path': 'config/multi_asset.yaml',
        'runtime_profile': 'default',
        'candidates': [],
        'selected': [],
        'suppressed': [],
    }
    (base / 'portfolio_cycle_latest.json').write_text(json.dumps(stale, indent=2), encoding='utf-8')
    (base / 'portfolio_allocation_latest.json').write_text(json.dumps(stale, indent=2), encoding='utf-8')


def test_refresh_config_intelligence_materializes_scoped_portfolio_and_updates_eval(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    base_ts = 1773000000
    ts_values = [base_ts + (i * INTERVAL) for i in range(140)]
    dataset_path = tmp_path / 'data' / 'dataset_phase2.csv'
    _write_dataset(dataset_path, ts_values)
    _write_daily_summaries(tmp_path)
    _write_decision_latest(tmp_path, ts_values[-1])
    _write_legacy_mismatch(tmp_path)

    global_signals = resolve_scope_runtime_paths(tmp_path, scope_tag=SCOPE_TAG, partition_enable=False).signals_db_path
    _write_signals_db(global_signals, ts_values)

    payload = refresh_config_intelligence(
        repo_root=tmp_path,
        config_path=cfg,
        asset=ASSET,
        interval_sec=INTERVAL,
        rebuild_pack=True,
        materialize_portfolio=True,
    )

    assert payload['ok'] is True
    assert payload['materialized_portfolio']['ok'] is True
    item = payload['items'][0]
    assert item['pack_training_rows'] >= 100
    assert item['pack_anti_overfit_available'] is True
    assert item['latest_eval_present'] is True
    assert item['latest_eval_anti_overfit_available'] is True

    status = portfolio_status_payload(repo_root=tmp_path, config_path=cfg)
    assert status['latest_cycle'] is not None
    assert status['latest_allocation'] is not None
    assert status['latest_sources']['cycle']['source'] == 'scoped'
    assert status['latest_sources']['allocation']['source'] == 'scoped'

    surf = intelligence_payload(repo_root=tmp_path, config_path=cfg)
    assert 'portfolio_artifact_scope' not in list(surf.get('warnings') or [])
    assert ((surf.get('sources') or {}).get('candidate') or {}).get('source') == 'scoped'
    assert ((surf.get('sources') or {}).get('allocation') or {}).get('source') == 'scoped'
    assert bool(((surf.get('summary') or {}).get('anti_overfit') or {}).get('available')) is True
