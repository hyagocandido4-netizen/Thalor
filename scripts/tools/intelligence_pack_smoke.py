#!/usr/bin/env python
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
import sys
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.intelligence.fit import fit_intelligence_pack
from natbin.intelligence.paths import latest_eval_path, pack_path
from natbin.intelligence.runtime import enrich_candidate
from natbin.portfolio.models import CandidateDecision, PortfolioScope
from natbin.portfolio.paths import ScopeRuntimePaths
from natbin.state.summary_paths import daily_summary_path
from natbin.config.loader import load_thalor_config


ASSET = 'EURUSD-OTC'
INTERVAL = 300
SCOPE_TAG = 'EURUSD-OTC_300s'


def _ok(msg: str) -> None:
    print(f'[smoke][OK] {msg}')


def _fail(msg: str) -> None:
    print(f'[smoke][FAIL] {msg}')
    raise SystemExit(2)


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join([
            'version: "2.0"',
            'multi_asset:',
            '  enabled: true',
            '  partition_data_paths: true',
            '  data_db_template: data/market_{scope_tag}.sqlite3',
            '  dataset_path_template: data/datasets/{scope_tag}/dataset.csv',
            'quota:',
            '  target_trades_per_day: 3',
            'assets:',
            f'  - asset: {ASSET}',
            f'    interval_sec: {INTERVAL}',
            '    timezone: UTC',
            '    cluster_key: fx',
            'intelligence:',
            '  enabled: true',
            '  artifact_dir: runs/intelligence',
            '  slot_aware_enable: true',
            '  slot_aware_min_trades: 4',
            '  learned_gating_enable: true',
            '  learned_gating_min_rows: 20',
            '  drift_monitor_enable: true',
            '  drift_recent_limit: 50',
            '  drift_warn_psi: 0.10',
            '  drift_block_psi: 0.20',
            '  coverage_regulator_enable: true',
            '  coverage_bias_weight: 0.05',
            '  anti_overfit_enable: true',
            'execution:',
            '  enabled: false',
            '',
        ]),
        encoding='utf-8',
    )
    return cfg


def _write_summaries(repo_root: Path) -> None:
    now = datetime.now(tz=UTC)
    for days_back, wins10, wins11 in [(0, 8, 3), (1, 9, 2)]:
        day = (now - timedelta(days=days_back)).strftime('%Y-%m-%d')
        payload = {
            'day': day,
            'asset': ASSET,
            'interval_sec': INTERVAL,
            'timezone': 'UTC',
            'by_hour': {
                '10': {'trades': 10, 'wins': wins10, 'losses': 10 - wins10, 'ev_mean': 0.08},
                '11': {'trades': 10, 'wins': wins11, 'losses': 10 - wins11, 'ev_mean': -0.04},
            },
            'trades_by_hour': {
                '10': {'total': 10, 'CALL': 5, 'PUT': 5},
                '11': {'total': 10, 'CALL': 5, 'PUT': 5},
            },
            'observations_by_hour': {'10': 50, '11': 50},
        }
        path = daily_summary_path(day=day, asset=ASSET, interval_sec=INTERVAL, out_dir=repo_root / 'runs')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def _write_signals_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            'CREATE TABLE signals_v2 (ts INTEGER, asset TEXT, interval_sec INTEGER, action TEXT, proba_up REAL, conf REAL, score REAL, ev REAL, payout REAL, reason TEXT, executed_today INTEGER)'
        )
        base_ts = 1773136800
        for i in range(60):
            con.execute(
                'INSERT INTO signals_v2 (ts, asset, interval_sec, action, proba_up, conf, score, ev, payout, reason, executed_today) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (base_ts + i * INTERVAL, ASSET, INTERVAL, 'CALL', 0.82, 0.80, 0.78, 0.14, 0.80, 'topk_emit', i % 3),
            )
        for i in range(60):
            con.execute(
                'INSERT INTO signals_v2 (ts, asset, interval_sec, action, proba_up, conf, score, ev, payout, reason, executed_today) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (base_ts + (60 + i) * INTERVAL, ASSET, INTERVAL, 'CALL', 0.28, 0.54, 0.32, -0.04, 0.80, 'topk_emit', i % 3),
            )
        con.commit()
    finally:
        con.close()


def _write_dataset(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_ts = 1773136800
    rows = []
    for i in range(60):
        rows.append({'ts': base_ts + i * INTERVAL, 'y_open_close': 1.0})
    for i in range(60):
        rows.append({'ts': base_ts + (60 + i) * INTERVAL, 'y_open_close': 0.0})
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_multiwindow_summary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'per_window': [
            {'topk_hit_weighted': 0.61, 'topk_taken': 20},
            {'topk_hit_weighted': 0.59, 'topk_taken': 18},
            {'topk_hit_weighted': 0.63, 'topk_taken': 22},
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix='thalor_m5_intelligence_'))
    cwd_before = Path.cwd()
    try:
        cfg_path = _write_config(tmp)
        _write_summaries(tmp)
        runtime_paths = ScopeRuntimePaths(
            signals_db_path=tmp / 'runs' / 'signals' / SCOPE_TAG / 'live_signals.sqlite3',
            state_db_path=tmp / 'runs' / 'state' / SCOPE_TAG / 'live_topk_state.sqlite3',
        )
        dataset_path = tmp / 'data' / 'datasets' / SCOPE_TAG / 'dataset.csv'
        multiwindow_summary = tmp / 'tune' / 'summary.json'
        _write_signals_db(runtime_paths.signals_db_path)
        _write_dataset(dataset_path)
        _write_multiwindow_summary(multiwindow_summary)

        os.chdir(tmp)
        pack, out = fit_intelligence_pack(
            repo_root=tmp,
            config_path=cfg_path,
            asset=ASSET,
            interval_sec=INTERVAL,
            lookback_days=2,
            signals_db_path=runtime_paths.signals_db_path,
            dataset_path=dataset_path,
            multiwindow_summary_path=multiwindow_summary,
        )
        expected_pack = pack_path(repo_root=tmp, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
        if out != expected_pack or not out.exists():
            _fail(f'pack not created where expected: {out}')
        if not pack.get('learned_gate'):
            _fail(f'learned gate missing from pack: {pack}')
        if int(((pack.get('metadata') or {}).get('training_rows') or 0)) < 100:
            _fail(f'training rows unexpectedly low: {pack}')
        _ok('fit_intelligence_pack builds pack with learned gate and metadata')

        scope = PortfolioScope(asset=ASSET, interval_sec=INTERVAL, timezone='UTC', scope_tag=SCOPE_TAG, cluster_key='fx')
        cand = CandidateDecision(
            scope_tag=SCOPE_TAG,
            asset=ASSET,
            interval_sec=INTERVAL,
            day='2026-03-10',
            ts=1773136800,
            action='CALL',
            score=0.82,
            conf=0.81,
            ev=0.17,
            reason='topk_emit',
            blockers=None,
            decision_path='runs/decisions/decision_latest_EURUSD-OTC_300s.json',
            raw={'ts': 1773136800, 'proba_up': 0.78, 'payout': 0.8, 'executed_today': 0},
        )
        cfg = load_thalor_config(config_path=cfg_path, repo_root=tmp)
        out_cand = enrich_candidate(repo_root=tmp, scope=scope, candidate=cand, runtime_paths=runtime_paths, cfg=cfg)
        if out_cand.intelligence_score is None:
            _fail(f'intelligence score missing after enrichment: {out_cand.as_dict()}')
        if out_cand.learned_gate_prob is None:
            _fail(f'learned gate probability missing after enrichment: {out_cand.as_dict()}')
        eval_path = latest_eval_path(repo_root=tmp, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence')
        if not eval_path.exists():
            _fail('latest_eval.json was not written')
        payload = json.loads(eval_path.read_text(encoding='utf-8'))
        if payload.get('status') != 'ok':
            _fail(f'unexpected intelligence eval payload: {payload}')
        _ok('runtime enrichment writes latest_eval and annotates candidate')

    finally:
        os.chdir(cwd_before)
        shutil.rmtree(tmp, ignore_errors=True)

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
