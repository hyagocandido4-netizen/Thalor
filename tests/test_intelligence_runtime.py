
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from natbin.intelligence.drift import build_drift_baseline
from natbin.intelligence.learned_gate import fit_learned_gate
from natbin.intelligence.paths import latest_eval_path, pack_path, retrain_plan_path, retrain_status_path
from natbin.intelligence.runtime import enrich_candidate
from natbin.portfolio.models import CandidateDecision, PortfolioScope
from natbin.portfolio.paths import ScopeRuntimePaths


def _make_signals_db(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            'CREATE TABLE signals_v2 (ts INTEGER, asset TEXT, interval_sec INTEGER, action TEXT, proba_up REAL, conf REAL, score REAL, ev REAL, payout REAL, reason TEXT, executed_today INTEGER)'
        )
        for row in rows:
            con.execute(
                'INSERT INTO signals_v2 (ts, asset, interval_sec, action, proba_up, conf, score, ev, payout, reason, executed_today) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (
                    row.get('ts'),
                    row.get('asset'),
                    row.get('interval_sec'),
                    row.get('action'),
                    row.get('proba_up'),
                    row.get('conf'),
                    row.get('score'),
                    row.get('ev'),
                    row.get('payout'),
                    row.get('reason'),
                    row.get('executed_today'),
                ),
            )
        con.commit()
    finally:
        con.close()


def _make_cfg(*, drift_fail_closed: bool = False, scope_policies=None):
    return SimpleNamespace(
        quota=SimpleNamespace(target_trades_per_day=3.0),
        intelligence=SimpleNamespace(
            enabled=True,
            artifact_dir='runs/intelligence',
            slot_aware_enable=True,
            learned_gating_enable=True,
            learned_gating_weight=0.60,
            learned_stacking_enable=True,
            learned_promote_above=0.62,
            learned_suppress_below=0.42,
            learned_abstain_band=0.03,
            learned_fail_closed=False,
            learned_calibration_enable=True,
            learned_min_reliability=0.50,
            learned_neutralize_low_reliability=True,
            stack_max_bonus=0.05,
            stack_max_penalty=0.05,
            scope_policies=list(scope_policies or []),
            drift_monitor_enable=True,
            drift_recent_limit=50,
            drift_warn_psi=0.10,
            drift_block_psi=0.20,
            drift_fail_closed=drift_fail_closed,
            retrain_warn_streak=2,
            retrain_block_streak=1,
            retrain_cooldown_hours=12,
            regime_warn_shift=0.5,
            regime_block_shift=1.0,
            coverage_regulator_enable=True,
            coverage_target_trades_per_day=None,
            coverage_tolerance=0.25,
            coverage_bias_weight=0.05,
            coverage_curve_power=1.20,
            coverage_max_bonus=0.05,
            coverage_max_penalty=0.05,
            anti_overfit_enable=True,
            anti_overfit_fail_closed=False,
            anti_overfit_min_windows=3,
            anti_overfit_gap_penalty_weight=0.10,
        ),
    )


def _write_pack(repo_root: Path, scope_tag: str) -> None:
    model = fit_learned_gate(
        [
            {
                'base_ev': 0.10,
                'base_score': 0.82,
                'base_conf': 0.80,
                'proba_side': 0.85,
                'payout': 0.80,
                'hour_sin': 0.0,
                'hour_cos': 1.0,
                'dow_sin': 0.0,
                'dow_cos': 1.0,
                'slot_multiplier': 1.08,
                'executed_today_norm': 0.2,
                'correct': 1,
            }
            for _ in range(60)
        ]
        + [
            {
                'base_ev': -0.06,
                'base_score': 0.30,
                'base_conf': 0.52,
                'proba_side': 0.35,
                'payout': 0.80,
                'hour_sin': 0.0,
                'hour_cos': 1.0,
                'dow_sin': 0.0,
                'dow_cos': 1.0,
                'slot_multiplier': 0.90,
                'executed_today_norm': 0.8,
                'correct': 0,
            }
            for _ in range(60)
        ],
        min_rows=50,
    )
    pack = {
        'kind': 'intelligence_pack',
        'schema_version': 'phase1-intelligence-pack-v2',
        'slot_profile': {
            'hours': {
                '10': {'hour': '10', 'multiplier': 1.10, 'quality': 0.05, 'eligible': True, 'recommendation': {'state': 'promote', 'score_delta': 0.02, 'threshold_delta': -0.01, 'alpha_delta': 0.01}},
                '11': {'hour': '11', 'multiplier': 0.92, 'quality': -0.04, 'eligible': True, 'recommendation': {'state': 'suppress', 'score_delta': -0.02, 'threshold_delta': 0.01, 'alpha_delta': -0.01}},
            }
        },
        'coverage_profile': {
            'cumulative_trade_share': {'10': 0.40, '11': 0.70, '23': 1.0},
            'target_curve_share': {'10': 0.30, '11': 0.60, '23': 1.0}
        },
        'learned_gate': model,
        'drift_baseline': build_drift_baseline([{'score': 0.85, 'conf': 0.90, 'ev': 0.18} for _ in range(80)]),
        'anti_overfit': {'accepted': True, 'penalty': 0.0},
    }
    p = pack_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir='runs/intelligence')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pack, indent=2), encoding='utf-8')


def test_enrich_candidate_writes_eval_and_score(tmp_path: Path):
    scope = PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s')
    runtime_paths = ScopeRuntimePaths(
        signals_db_path=tmp_path / 'runs' / 'signals' / scope.scope_tag / 'live_signals.sqlite3',
        state_db_path=tmp_path / 'runs' / 'state' / scope.scope_tag / 'live_topk_state.sqlite3',
    )
    _make_signals_db(
        runtime_paths.signals_db_path,
        [
            {'ts': 1773136800 + i * 300, 'asset': scope.asset, 'interval_sec': scope.interval_sec, 'action': 'CALL', 'proba_up': 0.7, 'conf': 0.8, 'score': 0.78, 'ev': 0.16, 'payout': 0.8, 'reason': 'topk_emit', 'executed_today': 1}
            for i in range(20)
        ],
    )
    _write_pack(tmp_path, scope.scope_tag)
    cand = CandidateDecision(
        scope_tag=scope.scope_tag,
        asset=scope.asset,
        interval_sec=scope.interval_sec,
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
    out = enrich_candidate(repo_root=tmp_path, scope=scope, candidate=cand, runtime_paths=runtime_paths, cfg=_make_cfg())
    assert out.intelligence_score is not None
    assert out.learned_gate_prob is not None
    assert out.action == 'CALL'
    assert out.stack_decision in {'promote', 'neutral', 'abstain'}
    assert out.regime_level in {'ok', 'warn', 'block'}
    eval_p = latest_eval_path(repo_root=tmp_path, scope_tag=scope.scope_tag, artifact_dir='runs/intelligence')
    assert eval_p.exists()
    payload = json.loads(eval_p.read_text(encoding='utf-8'))
    assert payload['intelligence_score'] == out.intelligence_score
    assert payload['stack']['decision'] == out.stack_decision


def test_enrich_candidate_can_fail_closed_on_drift(tmp_path: Path):
    scope = PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s')
    runtime_paths = ScopeRuntimePaths(
        signals_db_path=tmp_path / 'runs' / 'signals' / scope.scope_tag / 'live_signals.sqlite3',
        state_db_path=tmp_path / 'runs' / 'state' / scope.scope_tag / 'live_topk_state.sqlite3',
    )
    _make_signals_db(
        runtime_paths.signals_db_path,
        [
            {'ts': 1773136800 + i * 300, 'asset': scope.asset, 'interval_sec': scope.interval_sec, 'action': 'CALL', 'proba_up': 0.3, 'conf': 0.2, 'score': 0.12, 'ev': -0.10, 'payout': 0.8, 'reason': 'topk_emit', 'executed_today': 1}
            for i in range(20)
        ],
    )
    _write_pack(tmp_path, scope.scope_tag)
    cand = CandidateDecision(
        scope_tag=scope.scope_tag,
        asset=scope.asset,
        interval_sec=scope.interval_sec,
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
    out = enrich_candidate(repo_root=tmp_path, scope=scope, candidate=cand, runtime_paths=runtime_paths, cfg=_make_cfg(drift_fail_closed=True))
    assert out.action == 'HOLD'
    assert out.reason == 'intelligence_drift_block'
    assert out.regime_level in {'warn', 'block'}


def test_enrich_candidate_applies_scope_policy(tmp_path: Path):
    scope = PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s')
    runtime_paths = ScopeRuntimePaths(
        signals_db_path=tmp_path / 'runs' / 'signals' / scope.scope_tag / 'live_signals.sqlite3',
        state_db_path=tmp_path / 'runs' / 'state' / scope.scope_tag / 'live_topk_state.sqlite3',
    )
    _make_signals_db(
        runtime_paths.signals_db_path,
        [
            {'ts': 1773136800 + i * 300, 'asset': scope.asset, 'interval_sec': scope.interval_sec, 'action': 'CALL', 'proba_up': 0.7, 'conf': 0.8, 'score': 0.78, 'ev': 0.16, 'payout': 0.8, 'reason': 'topk_emit', 'executed_today': 1}
            for i in range(20)
        ],
    )
    _write_pack(tmp_path, scope.scope_tag)
    cand = CandidateDecision(
        scope_tag=scope.scope_tag,
        asset=scope.asset,
        interval_sec=scope.interval_sec,
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
    policies = [SimpleNamespace(name='scope_exact', scope_tag=scope.scope_tag, learned_weight=0.75, promote_above=0.58)]
    out = enrich_candidate(repo_root=tmp_path, scope=scope, candidate=cand, runtime_paths=runtime_paths, cfg=_make_cfg(scope_policies=policies))
    assert out.intelligence['policy']['name'] == 'scope_exact'
    assert out.intelligence['policy']['learned_weight'] == 0.75



def test_enrich_candidate_writes_portfolio_feedback_and_retrain_artifacts(tmp_path: Path):
    scope = PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s')
    runtime_paths = ScopeRuntimePaths(
        signals_db_path=tmp_path / 'runs' / 'signals' / scope.scope_tag / 'live_signals.sqlite3',
        state_db_path=tmp_path / 'runs' / 'state' / scope.scope_tag / 'live_topk_state.sqlite3',
    )
    _make_signals_db(
        runtime_paths.signals_db_path,
        [
            {'ts': 1773136800 + i * 300, 'asset': scope.asset, 'interval_sec': scope.interval_sec, 'action': 'CALL', 'proba_up': 0.25, 'conf': 0.22, 'score': 0.10, 'ev': -0.12, 'payout': 0.8, 'reason': 'topk_emit', 'executed_today': 4}
            for i in range(25)
        ],
    )
    _write_pack(tmp_path, scope.scope_tag)
    cand = CandidateDecision(
        scope_tag=scope.scope_tag,
        asset=scope.asset,
        interval_sec=scope.interval_sec,
        day='2026-03-10',
        ts=1773136800,
        action='CALL',
        score=0.82,
        conf=0.81,
        ev=0.17,
        reason='topk_emit',
        blockers=None,
        decision_path='runs/decisions/decision_latest_EURUSD-OTC_300s.json',
        raw={'ts': 1773136800, 'proba_up': 0.78, 'payout': 0.8, 'executed_today': 4},
    )
    out = enrich_candidate(repo_root=tmp_path, scope=scope, candidate=cand, runtime_paths=runtime_paths, cfg=_make_cfg())
    assert out.portfolio_score is not None
    assert out.portfolio_feedback['portfolio_score'] == out.portfolio_score
    plan = json.loads(retrain_plan_path(repo_root=tmp_path, scope_tag=scope.scope_tag, artifact_dir='runs/intelligence').read_text(encoding='utf-8'))
    status = json.loads(retrain_status_path(repo_root=tmp_path, scope_tag=scope.scope_tag, artifact_dir='runs/intelligence').read_text(encoding='utf-8'))
    assert plan['state'] in {'queued', 'watch', 'cooldown'}
    assert status['kind'] == 'retrain_status'
    assert out.retrain_state == plan['state']
