from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from natbin.intelligence.fit import fit_intelligence_pack
from natbin.intelligence.paths import latest_eval_path
from natbin.intelligence.runtime import enrich_candidate
from natbin.portfolio.models import CandidateDecision, PortfolioScope
from natbin.portfolio.paths import ScopeRuntimePaths
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
            'multi_asset:',
            '  enabled: true',
            '  partition_data_paths: true',
            'quota:',
            '  target_trades_per_day: 3',
            'assets:',
            f'  - asset: {ASSET}',
            f'    interval_sec: {INTERVAL}',
            '    timezone: UTC',
            'intelligence:',
            '  enabled: true',
            '  artifact_dir: runs/intelligence',
            '  learned_gating_enable: true',
            '  learned_gating_min_rows: 20',
            '  learned_calibration_enable: true',
            '  learned_min_reliability: 0.55',
            '  stack_max_bonus: 0.04',
            '  stack_max_penalty: 0.06',
            '  slot_aware_enable: true',
            '  coverage_regulator_enable: true',
            '  drift_monitor_enable: true',
            '  anti_overfit_enable: true',
            '  learned_stacking_enable: true',
            '  scope_policies:',
            '    - name: eurusd_scope',
            '      scope_tag: EURUSD-OTC_300s',
            '      learned_weight: 0.72',
            '      promote_above: 0.60',
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_h11_smoke_') as td:
        root = Path(td)
        cfg_path = _write_config(root)
        _write_summaries(root)
        signals_db = root / 'runs' / 'signals' / SCOPE_TAG / 'live_signals.sqlite3'
        dataset_path = root / 'data' / 'datasets' / SCOPE_TAG / 'dataset.csv'
        multiwindow_summary = root / 'tune' / 'summary.json'
        _write_signals_db(signals_db)
        _write_dataset(dataset_path)
        _write_multiwindow_summary(multiwindow_summary)

        pack, _ = fit_intelligence_pack(
            repo_root=root,
            config_path=cfg_path,
            asset=ASSET,
            interval_sec=INTERVAL,
            lookback_days=2,
            signals_db_path=signals_db,
            dataset_path=dataset_path,
            multiwindow_summary_path=multiwindow_summary,
        )
        assert pack['schema_version'] == 'phase1-intelligence-pack-v3'
        assert pack['scope_policy']['name'] == 'eurusd_scope'
        assert pack['learned_gate']['probability_source'] in {'calibrated_isotonic', 'raw_logistic'}
        assert 0.0 <= float(pack['learned_gate']['reliability_score']) <= 1.0
        print('[h11][OK] pack includes calibrated learned gate and scope policy')

        scope = PortfolioScope(asset=ASSET, interval_sec=INTERVAL, timezone='UTC', scope_tag=SCOPE_TAG)
        runtime_paths = ScopeRuntimePaths(signals_db_path=signals_db, state_db_path=root / 'runs' / 'state' / SCOPE_TAG / 'live_topk_state.sqlite3')
        cfg = type('Cfg', (), {
            'quota': type('Quota', (), {'target_trades_per_day': 3.0})(),
            'intelligence': type('IntCfg', (), {
                'enabled': True,
                'artifact_dir': 'runs/intelligence',
                'slot_aware_enable': True,
                'learned_gating_enable': True,
                'learned_gating_weight': 0.60,
                'learned_stacking_enable': True,
                'learned_promote_above': 0.62,
                'learned_suppress_below': 0.42,
                'learned_abstain_band': 0.03,
                'learned_fail_closed': False,
                'learned_calibration_enable': True,
                'learned_min_reliability': 0.55,
                'learned_neutralize_low_reliability': True,
                'stack_max_bonus': 0.04,
                'stack_max_penalty': 0.06,
                'scope_policies': [type('Policy', (), {'name': 'eurusd_scope', 'scope_tag': SCOPE_TAG, 'learned_weight': 0.72, 'promote_above': 0.60})()],
                'drift_monitor_enable': True,
                'drift_recent_limit': 50,
                'drift_warn_psi': 0.10,
                'drift_block_psi': 0.20,
                'drift_fail_closed': False,
                'retrain_warn_streak': 2,
                'retrain_block_streak': 1,
                'retrain_cooldown_hours': 12,
                'regime_warn_shift': 0.5,
                'regime_block_shift': 1.0,
                'coverage_regulator_enable': True,
                'coverage_target_trades_per_day': None,
                'coverage_tolerance': 0.25,
                'coverage_bias_weight': 0.05,
                'coverage_curve_power': 1.20,
                'coverage_max_bonus': 0.05,
                'coverage_max_penalty': 0.05,
                'anti_overfit_enable': True,
                'anti_overfit_fail_closed': False,
            })(),
        })()
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
        out = enrich_candidate(repo_root=root, scope=scope, candidate=cand, runtime_paths=runtime_paths, cfg=cfg)
        assert out.intelligence['policy']['name'] == 'eurusd_scope'
        assert out.intelligence['learned_probability_source'] in {'calibrated_isotonic', 'raw_logistic'}
        assert latest_eval_path(repo_root=root, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence').exists()
        print('[h11][OK] runtime enrichment uses scope policy and calibrated gate')

    print('[h11] ALL OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
