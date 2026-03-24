from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from natbin.intelligence.paths import retrain_review_path, retrain_status_path
from natbin.ops.retrain_ops import build_retrain_run_payload, build_retrain_status_payload
from natbin.portfolio.latest import write_portfolio_latest_payload
from natbin.intelligence.paths import pack_path, latest_eval_path, retrain_plan_path

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


def _seed_before(repo_root: Path, cfg_path: Path) -> None:
    pack_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps({'kind': 'intelligence_pack', 'metadata': {'training_rows': 120}, 'learned_gate': {'reliability_score': 0.82}, 'anti_overfit': {'available': True, 'accepted': True, 'robustness_score': 0.62, 'penalty': 0.0}}, indent=2), encoding='utf-8')
    latest_eval_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps({'kind': 'intelligence_eval', 'allow_trade': False, 'intelligence_score': 0.12, 'portfolio_score': 0.08, 'learned_reliability': 0.82, 'stack': {'available': True, 'decision': 'abstain'}, 'anti_overfit': {'available': True, 'accepted': True, 'robustness_score': 0.62, 'penalty': 0.0}, 'drift': {'level': 'ok'}, 'regime': {'level': 'ok'}, 'retrain_orchestration': {'state': 'queued', 'priority': 'high'}}, indent=2), encoding='utf-8')
    retrain_plan_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps({'kind': 'retrain_plan', 'state': 'queued', 'priority': 'high'}, indent=2), encoding='utf-8')
    retrain_status_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps({'kind': 'retrain_status', 'state': 'queued', 'priority': 'high'}, indent=2), encoding='utf-8')
    write_portfolio_latest_payload(repo_root, name='portfolio_cycle_latest.json', payload={'cycle_id': 'before', 'candidates': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'action': 'HOLD', 'reason': 'regime_block'}]}, config_path=cfg_path, profile=PROFILE, write_legacy=False)
    write_portfolio_latest_payload(repo_root, name='portfolio_allocation_latest.json', payload={'allocation_id': 'before', 'selected': [], 'suppressed': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'reason': 'regime_block'}]}, config_path=cfg_path, profile=PROFILE, write_legacy=False)


def _write_after(repo_root: Path, cfg_path: Path) -> None:
    pack_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps({'kind': 'intelligence_pack', 'metadata': {'training_rows': 200}, 'learned_gate': {'reliability_score': 0.93}, 'anti_overfit': {'available': True, 'accepted': True, 'robustness_score': 0.74, 'penalty': 0.0}}, indent=2), encoding='utf-8')
    latest_eval_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps({'kind': 'intelligence_eval', 'allow_trade': True, 'intelligence_score': 0.34, 'portfolio_score': 0.28, 'learned_reliability': 0.93, 'stack': {'available': True, 'decision': 'promote'}, 'anti_overfit': {'available': True, 'accepted': True, 'robustness_score': 0.74, 'penalty': 0.0}, 'drift': {'level': 'ok'}, 'regime': {'level': 'ok'}, 'retrain_orchestration': {'state': 'idle', 'priority': 'low'}}, indent=2), encoding='utf-8')
    retrain_plan_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(json.dumps({'kind': 'retrain_plan', 'state': 'idle', 'priority': 'low'}, indent=2), encoding='utf-8')
    write_portfolio_latest_payload(repo_root, name='portfolio_cycle_latest.json', payload={'cycle_id': 'after', 'candidates': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'action': 'CALL', 'reason': 'selected'}]}, config_path=cfg_path, profile=PROFILE, write_legacy=False)
    write_portfolio_latest_payload(repo_root, name='portfolio_allocation_latest.json', payload={'allocation_id': 'after', 'selected': [{'scope_tag': SCOPE_TAG, 'asset': ASSET, 'interval_sec': INTERVAL, 'reason': 'selected'}], 'suppressed': []}, config_path=cfg_path, profile=PROFILE, write_legacy=False)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_retrain_ops_1_') as td:
        root = Path(td)
        cfg = _write_config(root)
        _seed_before(root, cfg)

        def fake_refresh(**kwargs):
            _write_after(root, cfg)
            return {'ok': True, 'items': [{'scope_tag': SCOPE_TAG, 'ok': True, 'pack_training_rows': 200}], 'materialized_portfolio': {'ok': True}}

        with patch('natbin.ops.retrain_ops.refresh_config_intelligence', side_effect=fake_refresh):
            run_payload = build_retrain_run_payload(repo_root=root, config_path=cfg, asset=ASSET, interval_sec=INTERVAL)
            status_payload = build_retrain_status_payload(repo_root=root, config_path=cfg, asset=ASSET, interval_sec=INTERVAL)

        assert run_payload['ok'] is True, run_payload
        item = run_payload['items'][0]
        assert item['verdict'] == 'promoted', item
        assert retrain_review_path(repo_root=root, scope_tag=SCOPE_TAG).exists()
        assert retrain_status_path(repo_root=root, scope_tag=SCOPE_TAG).exists()
        assert status_payload['items'][0]['status']['state'] == 'promoted', status_payload

    print('retrain_ops_1_smoke: OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
