from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from natbin.intelligence.fit import fit_intelligence_pack
from natbin.intelligence.paths import latest_eval_path, retrain_plan_path
from natbin.intelligence.runtime import enrich_candidate
from natbin.portfolio.allocator import allocate
from natbin.portfolio.models import AssetQuota, CandidateDecision, PortfolioQuota, PortfolioScope
from natbin.portfolio.paths import ScopeRuntimePaths
from natbin.state.summary_paths import daily_summary_path


ASSET = 'EURUSD-OTC'
INTERVAL = 300
SCOPE_TAG = 'EURUSD-OTC_300s'


def _ok(msg: str) -> None:
    print(f'[h12][OK] {msg}')


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join([
            'version: "2.0"',
            'multi_asset:',
            '  enabled: true',
            '  partition_data_paths: true',
            '  portfolio_topk_total: 2',
            'quota:',
            '  target_trades_per_day: 3',
            'assets:',
            f'  - asset: {ASSET}',
            f'    interval_sec: {INTERVAL}',
            '    timezone: UTC',
            '    cluster_key: fx',
            '  - asset: BTCUSD-OTC',
            '    interval_sec: 300',
            '    timezone: UTC',
            '    cluster_key: crypto',
            'intelligence:',
            '  enabled: true',
            '  artifact_dir: runs/intelligence',
            '  learned_gating_enable: true',
            '  learned_gating_min_rows: 20',
            '  learned_calibration_enable: true',
            '  slot_aware_enable: true',
            '  coverage_regulator_enable: true',
            '  drift_monitor_enable: true',
            '  anti_overfit_enable: true',
            '  learned_stacking_enable: true',
            '  portfolio_weight: 1.10',
            '  allocator_warn_penalty: 0.02',
            '  allocator_block_penalty: 0.10',
            '  allocator_under_target_bonus: 0.03',
            '  allocator_over_target_penalty: 0.04',
            '  retrain_plan_cooldown_hours: 12',
            '  scope_policies:',
            '    - name: eurusd_scope',
            '      scope_tag: EURUSD-OTC_300s',
            '      portfolio_weight: 1.25',
            '      allocator_warn_penalty: 0.01',
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
                (base_ts + i * INTERVAL, ASSET, INTERVAL, 'CALL', 0.82, 0.80, 0.78, 0.14, 0.80, 'topk_emit', i % 5),
            )
        for i in range(60):
            con.execute(
                'INSERT INTO signals_v2 (ts, asset, interval_sec, action, proba_up, conf, score, ev, payout, reason, executed_today) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (base_ts + (60 + i) * INTERVAL, ASSET, INTERVAL, 'CALL', 0.18, 0.22, 0.10, -0.10, 0.80, 'topk_emit', 4),
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
    payload = {'per_window': [{'topk_hit_weighted': 0.61, 'topk_taken': 20}, {'topk_hit_weighted': 0.59, 'topk_taken': 18}, {'topk_hit_weighted': 0.63, 'topk_taken': 22}]}
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_h12_smoke_') as td:
        root = Path(td)
        cfg_path = _write_config(root)
        _write_summaries(root)
        signals_db = root / 'runs' / 'signals' / SCOPE_TAG / 'live_signals.sqlite3'
        dataset_path = root / 'data' / 'datasets' / SCOPE_TAG / 'dataset.csv'
        multiwindow_summary = root / 'tune' / 'summary.json'
        _write_signals_db(signals_db)
        _write_dataset(dataset_path)
        _write_multiwindow_summary(multiwindow_summary)

        pack, _ = fit_intelligence_pack(repo_root=root, config_path=cfg_path, asset=ASSET, interval_sec=INTERVAL, lookback_days=2, signals_db_path=signals_db, dataset_path=dataset_path, multiwindow_summary_path=multiwindow_summary)
        assert pack['scope_policy']['portfolio_weight'] == 1.25
        _ok('pack carries scope portfolio policy overrides')

        scope = PortfolioScope(asset=ASSET, interval_sec=INTERVAL, timezone='UTC', scope_tag=SCOPE_TAG, cluster_key='fx')
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
                'portfolio_weight': 1.10,
                'allocator_block_regime': True,
                'allocator_warn_penalty': 0.02,
                'allocator_block_penalty': 0.10,
                'allocator_under_target_bonus': 0.03,
                'allocator_over_target_penalty': 0.04,
                'allocator_retrain_penalty': 0.05,
                'allocator_reliability_penalty': 0.03,
                'scope_policies': [type('Policy', (), {'name': 'eurusd_scope', 'scope_tag': SCOPE_TAG, 'portfolio_weight': 1.25, 'allocator_warn_penalty': 0.01})()],
                'drift_monitor_enable': True,
                'drift_recent_limit': 50,
                'drift_warn_psi': 0.10,
                'drift_block_psi': 0.20,
                'drift_fail_closed': False,
                'retrain_warn_streak': 2,
                'retrain_block_streak': 1,
                'retrain_cooldown_hours': 12,
                'retrain_plan_cooldown_hours': 12,
                'retrain_watch_reliability_below': 0.55,
                'retrain_queue_on_regime_block': True,
                'retrain_queue_on_anti_overfit_reject': True,
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
        cand = CandidateDecision(scope_tag=SCOPE_TAG, asset=ASSET, interval_sec=INTERVAL, day='2026-03-10', ts=1773136800, action='CALL', score=0.82, conf=0.81, ev=0.17, reason='topk_emit', blockers=None, decision_path='runs/decisions/decision_latest_EURUSD-OTC_300s.json', raw={'ts': 1773136800, 'proba_up': 0.78, 'payout': 0.8, 'executed_today': 4})
        out = enrich_candidate(repo_root=root, scope=scope, candidate=cand, runtime_paths=runtime_paths, cfg=cfg)
        assert out.portfolio_score is not None
        assert retrain_plan_path(repo_root=root, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence').exists()
        assert latest_eval_path(repo_root=root, scope_tag=SCOPE_TAG, artifact_dir='runs/intelligence').exists()
        _ok('runtime enrichment writes portfolio score and retrain plan')

        scopes = [scope, PortfolioScope(asset='BTCUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='BTCUSD-OTC_300s', cluster_key='crypto')]
        asset_quotas = [
            AssetQuota(scope_tag=SCOPE_TAG, asset=ASSET, interval_sec=INTERVAL, day='2026-03-10', kind='open', reason='', executed_today=0, max_trades_per_day=3, budget_left=3, pending_unknown=0, max_pending_unknown=1, open_positions=0, max_open_positions=1, cluster_key='fx'),
            AssetQuota(scope_tag='BTCUSD-OTC_300s', asset='BTCUSD-OTC', interval_sec=300, day='2026-03-10', kind='open', reason='', executed_today=0, max_trades_per_day=3, budget_left=3, pending_unknown=0, max_pending_unknown=1, open_positions=0, max_open_positions=1, cluster_key='crypto'),
        ]
        portfolio_quota = PortfolioQuota(day='2026-03-10', kind='open', reason='', executed_today_total=0, hard_max_trades_per_day_total=10, budget_left_total=10, pending_unknown_total=0, open_positions_total=0, hard_max_positions_total=4)
        blocked = CandidateDecision(scope_tag=SCOPE_TAG, asset=ASSET, interval_sec=INTERVAL, day='2026-03-10', ts=1773136800, action='CALL', score=0.80, conf=0.80, ev=0.20, reason='blocked', blockers=None, decision_path='runs/decisions/a.json', raw={}, portfolio_score=0.90, portfolio_feedback={'allocator_blocked': True, 'block_reason': 'regime_block'}, regime_level='block')
        ok = CandidateDecision(scope_tag='BTCUSD-OTC_300s', asset='BTCUSD-OTC', interval_sec=300, day='2026-03-10', ts=1773136800, action='PUT', score=0.70, conf=0.65, ev=0.18, reason='ok', blockers=None, decision_path='runs/decisions/b.json', raw={}, portfolio_score=0.40, portfolio_feedback={'allocator_blocked': False}, regime_level='ok')
        allocation = allocate(str(root), scopes=scopes, candidates=[blocked, ok], asset_quotas=asset_quotas, portfolio_quota=portfolio_quota, config_path=str(cfg_path))
        assert [item.scope_tag for item in allocation.selected] == ['BTCUSD-OTC_300s']
        assert allocation.risk_summary['suppressed_feedback_blocks'] == 1
        _ok('allocator uses portfolio feedback block and portfolio score')

    print('[h12] ALL OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
