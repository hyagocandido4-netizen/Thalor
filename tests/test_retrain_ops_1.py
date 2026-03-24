from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.intelligence.paths import latest_eval_path, pack_path, retrain_plan_path, retrain_review_path, retrain_status_path
from natbin.intelligence.retrain import orchestrate_retrain
from natbin.ops.retrain_ops import build_retrain_run_payload, build_retrain_status_payload
from natbin.portfolio.latest import scoped_portfolio_allocation_latest_path, scoped_portfolio_cycle_latest_path, write_portfolio_latest_payload

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


def test_retrain_run_promotes_and_writes_review(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    _write_before_artifacts(tmp_path, cfg)

    def fake_refresh(**kwargs):
        _write_after_artifacts(tmp_path, cfg, promote=True)
        return {'ok': True, 'items': [{'scope_tag': SCOPE_TAG, 'ok': True, 'pack_training_rows': 210}], 'materialized_portfolio': {'ok': True}}

    monkeypatch.setattr('natbin.ops.retrain_ops.refresh_config_intelligence', fake_refresh)

    payload = build_retrain_run_payload(repo_root=tmp_path, config_path=cfg, asset=ASSET, interval_sec=INTERVAL)
    assert payload['ok'] is True
    item = payload['items'][0]
    assert item['verdict'] == 'promoted'
    assert item['restored_previous_artifacts'] is False
    status = json.loads(retrain_status_path(repo_root=tmp_path, scope_tag=SCOPE_TAG).read_text(encoding='utf-8'))
    review = json.loads(retrain_review_path(repo_root=tmp_path, scope_tag=SCOPE_TAG).read_text(encoding='utf-8'))
    assert status['state'] == 'promoted'
    assert review['verdict'] == 'promoted'
    assert review['comparison']['score'] >= 0.5


def test_retrain_run_rejects_and_restores_previous_artifacts(tmp_path: Path, monkeypatch) -> None:
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
    assert item['verdict'] == 'rejected'
    assert item['restored_previous_artifacts'] is True
    pack_payload = json.loads(pack_path(repo_root=tmp_path, scope_tag=SCOPE_TAG).read_text(encoding='utf-8'))
    latest_eval = json.loads(latest_eval_path(repo_root=tmp_path, scope_tag=SCOPE_TAG).read_text(encoding='utf-8'))
    assert pack_payload['metadata']['training_rows'] == 120
    assert latest_eval['portfolio_score'] == 0.08
    status = json.loads(retrain_status_path(repo_root=tmp_path, scope_tag=SCOPE_TAG).read_text(encoding='utf-8'))
    review = json.loads(retrain_review_path(repo_root=tmp_path, scope_tag=SCOPE_TAG).read_text(encoding='utf-8'))
    cycle = json.loads(scoped_portfolio_cycle_latest_path(tmp_path, config_path=cfg, profile=PROFILE).read_text(encoding='utf-8'))
    alloc = json.loads(scoped_portfolio_allocation_latest_path(tmp_path, config_path=cfg, profile=PROFILE).read_text(encoding='utf-8'))
    assert status['state'] == 'rejected'
    assert status['plan_state'] == 'cooldown'
    assert bool(status['cooldown_active']) is True
    assert review['verdict'] == 'rejected'
    assert review['final']['retrain_state'] == 'rejected'
    assert cycle['candidates'][0]['retrain_state'] == 'rejected'
    assert cycle['candidates'][0]['retrain_plan_state'] == 'cooldown'
    assert alloc['suppressed'][0]['retrain_state'] == 'rejected'
    assert alloc['suppressed'][0]['retrain_plan_state'] == 'cooldown'


def test_retrain_run_rejected_preserves_terminal_status_for_resync(tmp_path: Path, monkeypatch) -> None:
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
    assert item['verdict'] == 'rejected'
    assert item['post_review_resync']['ok'] is True
    assert item['post_review_resync']['retrain_state'] == 'rejected'
    assert item['post_review_resync']['retrain_plan_state'] == 'cooldown'



def test_retrain_status_payload_reads_review_and_metrics(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    _write_before_artifacts(tmp_path, cfg)

    def fake_refresh(**kwargs):
        _write_after_artifacts(tmp_path, cfg, promote=True)
        return {'ok': True, 'items': [{'scope_tag': SCOPE_TAG, 'ok': True}], 'materialized_portfolio': {'ok': True}}

    monkeypatch.setattr('natbin.ops.retrain_ops.refresh_config_intelligence', fake_refresh)
    build_retrain_run_payload(repo_root=tmp_path, config_path=cfg, asset=ASSET, interval_sec=INTERVAL)

    payload = build_retrain_status_payload(repo_root=tmp_path, config_path=cfg, asset=ASSET, interval_sec=INTERVAL)
    item = payload['items'][0]
    assert item['status']['state'] == 'promoted'
    assert item['review']['verdict'] == 'promoted'
    assert item['metrics']['pack_training_rows'] == 210


def test_orchestrate_retrain_preserves_recent_terminal_status(tmp_path: Path) -> None:
    status_path = retrain_status_path(repo_root=tmp_path, scope_tag=SCOPE_TAG)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps({'kind': 'retrain_status', 'state': 'promoted', 'priority': 'low', 'updated_at_utc': '2026-03-22T12:00:00+00:00', 'status_reason': 'improved', 'run_id': 'abc'}, indent=2), encoding='utf-8')
    payload = orchestrate_retrain(
        repo_root=tmp_path,
        scope_tag=SCOPE_TAG,
        artifact_dir='runs/intelligence',
        trigger_payload={'priority': 'high', 'reason': 'drift_block_streak'},
        drift_state={'level': 'block', 'retrain_recommended': True},
        regime={'level': 'block'},
        coverage={'pressure': 'balanced'},
        learned_reliability=0.7,
        anti_overfit={'available': True, 'accepted': True},
        policy={'portfolio_weight': 1.0, 'allocator_block_regime': True, 'allocator_retrain_penalty': 0.05},
        cooldown_hours=24,
        watch_reliability_below=0.55,
        now_utc=datetime(2026, 3, 22, 14, 0, tzinfo=UTC),
    )
    status = json.loads(status_path.read_text(encoding='utf-8'))
    assert payload['state'] == 'queued'
    assert status['state'] == 'promoted'
    assert status['plan_state'] == 'queued'


def _write_expired_cooldown(repo_root: Path) -> None:
    retrain_plan_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(
        json.dumps(
            {
                'kind': 'retrain_plan',
                'state': 'cooldown',
                'priority': 'high',
                'queue_recommended': True,
                'watch_recommended': True,
                'reasons': ['anti_overfit_reject'],
                'cooldown_active': True,
                'cooldown_until_utc': '2026-03-22T23:16:09+00:00',
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    retrain_status_path(repo_root=repo_root, scope_tag=SCOPE_TAG).write_text(
        json.dumps(
            {
                'kind': 'retrain_status',
                'schema_version': 'phase1-retrain-status-v3',
                'state': 'cooldown',
                'priority': 'high',
                'plan_state': 'cooldown',
                'plan_priority': 'high',
                'queue_recommended': True,
                'watch_recommended': True,
                'plan_reasons': ['anti_overfit_reject'],
                'cooldown_active': True,
                'cooldown_until_utc': '2026-03-22T23:16:09+00:00',
                'updated_at_utc': '2026-03-22T21:54:33+00:00',
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )


def test_retrain_status_payload_recomputes_expired_cooldown(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    _write_before_artifacts(tmp_path, cfg)
    _write_expired_cooldown(tmp_path)

    calls: list[dict[str, object]] = []

    def fake_refresh(**kwargs):
        calls.append(dict(kwargs))
        return {'ok': True, 'message': 'noop'}

    monkeypatch.setattr('natbin.ops.retrain_ops.refresh_config_intelligence', fake_refresh)
    monkeypatch.setattr('natbin.ops.retrain_ops._now_dt', lambda: datetime(2026, 3, 22, 23, 41, tzinfo=UTC))
    monkeypatch.setattr('natbin.ops.retrain_ops._now_iso', lambda: '2026-03-22T23:41:00+00:00')

    payload = build_retrain_status_payload(repo_root=tmp_path, config_path=cfg, asset=ASSET, interval_sec=INTERVAL)
    item = payload['items'][0]
    assert item['plan']['state'] == 'queued'
    assert item['status']['state'] == 'queued'
    assert item['status']['status_reason'] == 'cooldown_expired'
    assert item['cooldown_refresh']['refreshed'] is True
    assert calls and calls[0]['rebuild_pack'] is False


def test_retrain_run_executes_after_expired_cooldown(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    _write_before_artifacts(tmp_path, cfg)
    _write_expired_cooldown(tmp_path)

    calls: list[dict[str, object]] = []

    def fake_refresh(**kwargs):
        calls.append(dict(kwargs))
        if kwargs.get('rebuild_pack'):
            _write_after_artifacts(tmp_path, cfg, promote=True)
            return {'ok': True, 'items': [{'scope_tag': SCOPE_TAG, 'ok': True, 'pack_training_rows': 210}], 'materialized_portfolio': {'ok': True}}
        return {'ok': True, 'message': 'cooldown_refresh_noop'}

    monkeypatch.setattr('natbin.ops.retrain_ops.refresh_config_intelligence', fake_refresh)
    monkeypatch.setattr('natbin.ops.retrain_ops._now_dt', lambda: datetime(2026, 3, 22, 23, 41, tzinfo=UTC))
    monkeypatch.setattr('natbin.ops.retrain_ops._now_iso', lambda: '2026-03-22T23:41:00+00:00')

    payload = build_retrain_run_payload(repo_root=tmp_path, config_path=cfg, asset=ASSET, interval_sec=INTERVAL)
    item = payload['items'][0]
    assert item['executed'] is True
    assert item['verdict'] == 'promoted'
    assert item['comparison'] is not None
    assert item['after'] is not None
    review = json.loads(retrain_review_path(repo_root=tmp_path, scope_tag=SCOPE_TAG).read_text(encoding='utf-8'))
    assert review['executed'] is True
    assert review['cooldown_refresh']['normalized'] is True
    assert len(calls) >= 3
    assert calls[0]['rebuild_pack'] is False
    assert calls[1]['rebuild_pack'] is True
    assert calls[2]['rebuild_pack'] is False
