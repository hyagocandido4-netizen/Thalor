#!/usr/bin/env python
from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from natbin.ops.intelligence_surface import (
    build_intelligence_surface_payload,
    build_portfolio_intelligence_payload,
)
from natbin.runtime.execution_models import OrderIntent
from natbin.state.execution_repo import ExecutionRepository


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def ok(msg: str) -> None:
    print(f'[intelligence_ops_smoke][OK] {msg}')


def fail(msg: str) -> None:
    print(f'[intelligence_ops_smoke][FAIL] {msg}')
    raise SystemExit(2)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='thalor_intops_smoke_') as td:
        repo = Path(td)
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
        scope_tag = 'EURUSD-OTC_300s'
        now = _now()
        feedback = {'allocator_blocked': False, 'portfolio_score': 0.74}
        intel_dir = repo / 'runs' / 'intelligence' / scope_tag
        _write_json(intel_dir / 'pack.json', {'kind': 'intelligence_pack', 'generated_at_utc': now, 'metadata': {'training_rows': 240}})
        _write_json(
            intel_dir / 'latest_eval.json',
            {
                'kind': 'intelligence_eval',
                'evaluated_at_utc': now,
                'allow_trade': True,
                'intelligence_score': 0.70,
                'portfolio_score': 0.74,
                'portfolio_feedback': feedback,
                'retrain_orchestration': {'state': 'idle', 'priority': 'low'},
            },
        )
        _write_json(intel_dir / 'retrain_plan.json', {'kind': 'retrain_plan', 'at_utc': now, 'state': 'idle', 'priority': 'low'})
        _write_json(intel_dir / 'retrain_status.json', {'kind': 'retrain_status', 'updated_at_utc': now, 'state': 'idle', 'priority': 'low'})
        _write_json(
            repo / 'runs' / 'portfolio' / 'portfolio_allocation_latest.json',
            {
                'allocation_id': 'alloc_smoke_001',
                'at_utc': now,
                'selected': [
                    {
                        'scope_tag': scope_tag,
                        'asset': 'EURUSD-OTC',
                        'interval_sec': 300,
                        'reason': 'selected_topk',
                        'rank': 1,
                        'cluster_key': 'fx',
                        'portfolio_score': 0.74,
                        'intelligence_score': 0.70,
                        'retrain_state': 'idle',
                        'retrain_priority': 'low',
                        'portfolio_feedback': feedback,
                    }
                ],
                'suppressed': [],
            },
        )
        repo_exec = ExecutionRepository(repo / 'runs' / 'runtime_execution.sqlite3')
        repo_exec.save_intent(
            OrderIntent(
                intent_id='smoke_intent_001',
                scope_tag=scope_tag,
                broker_name='fake',
                account_mode='PRACTICE',
                day='2026-03-21',
                asset='EURUSD-OTC',
                interval_sec=300,
                signal_ts=1773956100,
                decision_action='CALL',
                decision_conf=0.70,
                decision_score=0.61,
                stake_amount=2.0,
                stake_currency='BRL',
                expiry_ts=1773956400,
                entry_deadline_utc=now,
                client_order_key='smoke_client_order_key',
                intent_state='planned',
                broker_status='unknown',
                created_at_utc=now,
                updated_at_utc=now,
                allocation_batch_id='alloc_smoke_001',
                cluster_key='fx',
                portfolio_score=0.74,
                intelligence_score=0.70,
                retrain_state='idle',
                retrain_priority='low',
                allocation_reason='selected_topk',
                allocation_rank=1,
                portfolio_feedback_json=json.dumps(feedback, ensure_ascii=False),
            )
        )

        scope_payload = build_intelligence_surface_payload(repo_root=repo, config_path=cfg, write_artifact=True)
        if scope_payload.get('kind') != 'intelligence_surface':
            fail('kind inesperado para intelligence surface')
        if not (repo / 'runs' / 'control' / scope_tag / 'intelligence.json').exists():
            fail('artifact intelligence.json não foi gravado')
        ok('runtime_app intelligence surface válida')

        portfolio_payload = build_portfolio_intelligence_payload(repo_root=repo, config_path=cfg)
        if portfolio_payload.get('kind') != 'portfolio_intelligence_surface':
            fail('kind inesperado para portfolio intelligence surface')
        if int((portfolio_payload.get('summary') or {}).get('selected_scopes') or 0) != 1:
            fail('portfolio rollup não refletiu o scope selecionado')
        ok('portfolio intelligence rollup válido')


if __name__ == '__main__':
    main()
