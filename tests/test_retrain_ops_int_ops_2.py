from __future__ import annotations

import json
from pathlib import Path

from natbin.intelligence.paths import intelligence_ops_state_path, latest_eval_path, pack_path, retrain_plan_path, retrain_review_path, retrain_status_path
from natbin.ops.retrain_ops import build_retrain_run_payload, build_retrain_status_payload
from natbin.portfolio.latest import write_portfolio_latest_payload

ASSET = 'EURUSD-OTC'
INTERVAL = 300
SCOPE_TAG = 'EURUSD-OTC_300s'
PROFILE = 'live_controlled_practice'


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                f'  profile: {PROFILE}',
                'broker:',
                '  provider: iqoption',
                '  balance_mode: PRACTICE',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: PRACTICE',
                'intelligence:',
                '  enabled: true',
                '  artifact_dir: runs/intelligence',
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


def _write_before_artifacts(repo_root: Path, cfg_path: Path) -> None:
    pack = {
        'kind': 'intelligence_pack',
        'metadata': {'training_rows': 120, 'training_strategy': 'explicit_trades'},
        'learned_gate': {'reliability_score': 0.82},
        'anti_overfit': {'available': True, 'accepted': True, 'robustness_score': 0.62, 'penalty': 0.0},
    }
    latest_eval = {
        'kind': 'intelligence_eval',
        'allow_trade': False,
        'intelligence_score': 0.12,
        'portfolio_score': 0.08,
        'learned_reliability': 0.82,
        'stack': {'available': True, 'decision': 'abstain'},
        'anti_overfit': {'available': True, 'accepted': True, 'robustness_score': 0.62, 'penalty': 0.0},
        'drift': {'level': 'ok'},
        'regime': {'level': 'ok'},
        'retrain_orchestration': {'state': 'queued', 'priority': 'high'},
    }
    pack_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding='utf-8')
    latest_eval_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps(latest_eval, indent=2, ensure_ascii=False), encoding='utf-8')
    retrain_plan_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps({'kind': 'retrain_plan', 'state': 'queued', 'priority': 'high'}, indent=2, ensure_ascii=False), encoding='utf-8')
    retrain_status_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps({'kind': 'retrain_status', 'state': 'queued', 'priority': 'high'}, indent=2, ensure_ascii=False), encoding='utf-8')
    write_portfolio_latest_payload(
        repo_root,
        name='portfolio_cycle_latest.json',
        payload={'cycle_id': 'before-cycle', 'candidates': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'action': 'HOLD', 'reason': 'regime_block'}]},
        config_path=cfg_path,
        profile=PROFILE,
        write_legacy=False,
    )
    write_portfolio_latest_payload(
        repo_root,
        name='portfolio_allocation_latest.json',
        payload={'allocation_id': 'before-allocation', 'selected': [], 'suppressed': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'reason': 'regime_block'}]},
        config_path=cfg_path,
        profile=PROFILE,
        write_legacy=False,
    )


def _write_after_artifacts(repo_root: Path, cfg_path: Path, *, promote: bool) -> None:
    if promote:
        pack = {
            'kind': 'intelligence_pack',
            'metadata': {'training_rows': 210, 'training_strategy': 'recovered_with_inferred_hold_rows'},
            'learned_gate': {'reliability_score': 0.93},
            'anti_overfit': {'available': True, 'accepted': True, 'robustness_score': 0.74, 'penalty': 0.0},
        }
        latest_eval = {
            'kind': 'intelligence_eval',
            'allow_trade': True,
            'intelligence_score': 0.34,
            'portfolio_score': 0.28,
            'learned_reliability': 0.93,
            'stack': {'available': True, 'decision': 'promote'},
            'anti_overfit': {'available': True, 'accepted': True, 'robustness_score': 0.74, 'penalty': 0.0},
            'drift': {'level': 'ok'},
            'regime': {'level': 'ok'},
            'retrain_orchestration': {'state': 'idle', 'priority': 'low'},
        }
        cycle = {'cycle_id': 'after-cycle', 'candidates': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'action': 'CALL', 'reason': 'selected'}]}
        alloc = {'allocation_id': 'after-allocation', 'selected': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'reason': 'selected'}], 'suppressed': []}
        plan = {'kind': 'retrain_plan', 'state': 'idle', 'priority': 'low'}
    else:
        pack = {
            'kind': 'intelligence_pack',
            'metadata': {'training_rows': 95, 'training_strategy': 'explicit_trades'},
            'learned_gate': {'reliability_score': 0.70},
            'anti_overfit': {'available': True, 'accepted': False, 'robustness_score': 0.28, 'penalty': 0.1},
        }
        latest_eval = {
            'kind': 'intelligence_eval',
            'allow_trade': False,
            'intelligence_score': -0.10,
            'portfolio_score': -0.18,
            'learned_reliability': 0.70,
            'stack': {'available': True, 'decision': 'suppress'},
            'anti_overfit': {'available': True, 'accepted': False, 'robustness_score': 0.28, 'penalty': 0.1},
            'drift': {'level': 'ok'},
            'regime': {'level': 'ok'},
            'retrain_orchestration': {'state': 'queued', 'priority': 'high'},
        }
        cycle = {'cycle_id': 'after-cycle', 'candidates': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'action': 'HOLD', 'reason': 'anti_overfit_block'}]}
        alloc = {'allocation_id': 'after-allocation', 'selected': [], 'suppressed': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'reason': 'anti_overfit_block'}]}
        plan = {'kind': 'retrain_plan', 'state': 'queued', 'priority': 'high'}
    pack_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding='utf-8')
    latest_eval_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps(latest_eval, indent=2, ensure_ascii=False), encoding='utf-8')
    retrain_plan_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding='utf-8')
    write_portfolio_latest_payload(repo_root, name='portfolio_cycle_latest.json', payload=cycle, config_path=cfg_path, profile=PROFILE, write_legacy=False)
    write_portfolio_latest_payload(repo_root, name='portfolio_allocation_latest.json', payload=alloc, config_path=cfg_path, profile=PROFILE, write_legacy=False)


def test_retrain_run_materializes_effective_ops_state_and_status_snapshot(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    _write_before_artifacts(tmp_path, cfg)

    def fake_refresh(**kwargs):
        if kwargs.get('rebuild_pack'):
            _write_after_artifacts(tmp_path, cfg, promote=False)
            return {'ok': True, 'items': [{'scope_tag': SCOPE_TAG, 'ok': True, 'pack_training_rows': 95}], 'materialized_portfolio': {'ok': True}}
        return {'ok': True, 'message': 'post_review_resync_noop', 'materialized_portfolio': {'ok': True}}

    monkeypatch.setattr('natbin.ops.retrain_ops.refresh_config_intelligence', fake_refresh)

    payload = build_retrain_run_payload(repo_root=tmp_path, config_path=cfg, asset=ASSET, interval_sec=INTERVAL)
    item = payload['items'][0]
    ops_state_file = intelligence_ops_state_path(repo_root=tmp_path, scope_tag=SCOPE_TAG)
    assert ops_state_file.exists()
    ops_state = json.loads(ops_state_file.read_text(encoding='utf-8'))
    assert item['ops_state']['kind'] == 'intelligence_ops_state'
    assert ops_state['consistency']['expected_rejected_cooldown'] is True
    assert ops_state['anti_overfit']['tuning']['source'] == 'review'

    snapshot = build_retrain_status_payload(repo_root=tmp_path, config_path=cfg, asset=ASSET, interval_sec=INTERVAL)
    snap_item = snapshot['items'][0]
    assert snap_item['ops_state']['consistency']['ok'] is True
    assert snap_item['review']['verdict'] == 'rejected'
    assert retrain_review_path(repo_root=tmp_path, scope_tag=SCOPE_TAG).exists()
