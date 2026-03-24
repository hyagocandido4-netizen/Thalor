from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.ops.intelligence_surface import (
    build_intelligence_surface_payload,
    build_portfolio_intelligence_payload,
)
from natbin.portfolio.latest import write_portfolio_latest_payload
from natbin.runtime.execution_models import OrderIntent
from natbin.state.execution_repo import ExecutionRepository


NOW = datetime.now(tz=UTC).isoformat(timespec='seconds')


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def _write_repo(repo: Path) -> Path:
    cfg = repo / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'multi_asset:',
                '  enabled: true',
                '  max_parallel_assets: 2',
                'execution:',
                '  enabled: false',
                '  mode: disabled',
                '  provider: fake',
                'intelligence:',
                '  enabled: true',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '    cluster_key: fx',
                '  - asset: GBPUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '    cluster_key: fx',
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def test_intelligence_surface_writes_scope_artifact_and_rollup(tmp_path: Path) -> None:
    cfg = _write_repo(tmp_path)
    scope_tag = 'EURUSD-OTC_300s'
    intel_dir = tmp_path / 'runs' / 'intelligence' / scope_tag
    feedback = {
        'allocator_blocked': False,
        'portfolio_score': 0.81,
        'block_reason': None,
    }
    _write_json(
        intel_dir / 'pack.json',
        {
            'kind': 'intelligence_pack',
            'generated_at_utc': NOW,
            'metadata': {'training_rows': 512},
        },
    )
    _write_json(
        intel_dir / 'latest_eval.json',
        {
            'kind': 'intelligence_eval',
            'evaluated_at_utc': NOW,
            'allow_trade': True,
            'intelligence_score': 0.77,
            'portfolio_score': 0.81,
            'portfolio_feedback': feedback,
            'retrain_orchestration': {'state': 'queued', 'priority': 'high'},
            'coverage': {'bias': 0.0},
        },
    )
    _write_json(
        intel_dir / 'retrain_plan.json',
        {
            'kind': 'retrain_plan',
            'at_utc': NOW,
            'state': 'queued',
            'priority': 'high',
            'recommended_action': 'retrain',
        },
    )
    _write_json(
        intel_dir / 'retrain_status.json',
        {
            'kind': 'retrain_status',
            'updated_at_utc': NOW,
            'state': 'queued',
            'priority': 'high',
        },
    )
    write_portfolio_latest_payload(
        tmp_path,
        name='portfolio_cycle_latest.json',
        config_path=cfg,
        profile='default',
        payload={
            'cycle_id': 'cycle_001',
            'finished_at_utc': NOW,
            'candidates': [
                {
                    'scope_tag': scope_tag,
                    'asset': 'EURUSD-OTC',
                    'interval_sec': 300,
                    'action': 'CALL',
                    'reason': 'candidate_ready',
                    'intelligence_score': 0.77,
                    'portfolio_score': 0.81,
                    'retrain_state': 'queued',
                    'retrain_priority': 'high',
                }
            ],
        },
    )
    write_portfolio_latest_payload(
        tmp_path,
        name='portfolio_allocation_latest.json',
        config_path=cfg,
        profile='default',
        payload={
            'allocation_id': 'alloc_001',
            'at_utc': NOW,
            'selected': [
                {
                    'scope_tag': scope_tag,
                    'asset': 'EURUSD-OTC',
                    'interval_sec': 300,
                    'reason': 'selected_topk',
                    'rank': 1,
                    'cluster_key': 'fx',
                    'portfolio_score': 0.81,
                    'intelligence_score': 0.77,
                    'retrain_state': 'queued',
                    'retrain_priority': 'high',
                    'portfolio_feedback': feedback,
                }
            ],
            'suppressed': [],
        },
    )

    repo = ExecutionRepository(tmp_path / 'runs' / 'runtime_execution.sqlite3')
    repo.save_intent(
        OrderIntent(
            intent_id='intent_001',
            scope_tag=scope_tag,
            broker_name='fake',
            account_mode='PRACTICE',
            day='2026-03-21',
            asset='EURUSD-OTC',
            interval_sec=300,
            signal_ts=1773956100,
            decision_action='CALL',
            decision_conf=0.73,
            decision_score=0.61,
            stake_amount=2.0,
            stake_currency='BRL',
            expiry_ts=1773956400,
            entry_deadline_utc=NOW,
            client_order_key='thalor_test',
            intent_state='planned',
            broker_status='unknown',
            created_at_utc=NOW,
            updated_at_utc=NOW,
            allocation_batch_id='alloc_001',
            cluster_key='fx',
            portfolio_score=0.81,
            intelligence_score=0.77,
            retrain_state='queued',
            retrain_priority='high',
            allocation_reason='selected_topk',
            allocation_rank=1,
            portfolio_feedback_json=json.dumps(feedback, ensure_ascii=False),
        )
    )

    payload = build_intelligence_surface_payload(repo_root=tmp_path, config_path=cfg, write_artifact=True)
    assert payload['kind'] == 'intelligence_surface'
    assert payload['severity'] == 'warn'
    assert payload['summary']['portfolio_score'] == 0.81
    assert payload['summary']['intelligence_score'] == 0.77
    assert payload['summary']['retrain_state'] == 'queued'
    assert payload['allocation']['allocation_id'] == 'alloc_001'
    assert payload['execution']['latest_intent']['allocation_rank'] == 1
    assert payload['execution']['missing_fields'] == []

    artifact = tmp_path / 'runs' / 'control' / scope_tag / 'intelligence.json'
    assert artifact.exists()

    portfolio = build_portfolio_intelligence_payload(repo_root=tmp_path, config_path=cfg)
    assert portfolio['kind'] == 'portfolio_intelligence_surface'
    assert portfolio['severity'] == 'warn'
    assert len(portfolio['items']) == 2
    assert portfolio['summary']['pack_available'] == 1
    assert portfolio['summary']['selected_scopes'] == 1
