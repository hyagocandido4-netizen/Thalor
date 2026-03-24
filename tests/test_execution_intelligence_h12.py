from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from natbin.runtime.execution import intent_from_signal_row


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        resolved_config={
            'execution': {
                'enabled': True,
                'mode': 'live',
                'provider': 'fake',
                'account_mode': 'PRACTICE',
                'client_order_prefix': 'thalor',
                'stake': {'amount': 2.0, 'currency': 'BRL'},
                'submit': {'grace_sec': 2},
            }
        },
        config=SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, timezone='UTC'),
        scope=SimpleNamespace(scope_tag='EURUSD-OTC_300s'),
    )


def test_intent_from_signal_row_carries_allocation_portfolio_score(tmp_path: Path) -> None:
    repo_root = tmp_path / 'repo'
    alloc_path = repo_root / 'runs' / 'portfolio' / 'portfolio_allocation_latest.json'
    alloc_path.parent.mkdir(parents=True, exist_ok=True)
    alloc_path.write_text(
        json.dumps(
            {
                'allocation_id': 'alloc_001',
                'selected': [
                    {
                        'scope_tag': 'EURUSD-OTC_300s',
                        'asset': 'EURUSD-OTC',
                        'interval_sec': 300,
                        'cluster_key': 'fx',
                        'portfolio_score': 0.73,
                        'intelligence_score': 0.81,
                        'retrain_state': 'queued',
                        'retrain_priority': 'high',
                        'reason': 'selected_topk',
                        'rank': 1,
                        'portfolio_feedback': {
                            'allocator_blocked': False,
                            'portfolio_score': 0.73,
                            'block_reason': None,
                        },
                    }
                ],
            },
            indent=2,
        ),
        encoding='utf-8',
    )

    intent = intent_from_signal_row(
        row={'day': '2026-03-19', 'ts': 1773956100, 'action': 'CALL', 'conf': 0.77, 'score': 0.66},
        ctx=_ctx(),
        repo_root=repo_root,
    )

    assert intent.allocation_batch_id == 'alloc_001'
    assert intent.cluster_key == 'fx'
    assert intent.portfolio_score == 0.73
    assert intent.intelligence_score == 0.81
    assert intent.retrain_state == 'queued'
    assert intent.retrain_priority == 'high'
    assert intent.allocation_reason == 'selected_topk'
    assert intent.allocation_rank == 1
    assert json.loads(str(intent.portfolio_feedback_json))['portfolio_score'] == 0.73


def test_intent_from_signal_row_prefers_row_portfolio_score_over_allocation(tmp_path: Path) -> None:
    repo_root = tmp_path / 'repo'
    alloc_path = repo_root / 'runs' / 'portfolio' / 'portfolio_allocation_latest.json'
    alloc_path.parent.mkdir(parents=True, exist_ok=True)
    alloc_path.write_text(
        json.dumps(
            {
                'allocation_id': 'alloc_002',
                'selected': [
                    {
                        'scope_tag': 'EURUSD-OTC_300s',
                        'asset': 'EURUSD-OTC',
                        'interval_sec': 300,
                        'cluster_key': 'fx',
                        'portfolio_score': 0.40,
                        'intelligence_score': 0.41,
                        'retrain_state': 'cooldown',
                        'retrain_priority': 'medium',
                        'reason': 'selected_topk',
                        'rank': 2,
                        'portfolio_feedback': {'allocator_blocked': False},
                    }
                ],
            },
            indent=2,
        ),
        encoding='utf-8',
    )

    intent = intent_from_signal_row(
        row={
            'day': '2026-03-19',
            'ts': 1773956100,
            'action': 'PUT',
            'conf': 0.77,
            'score': 0.66,
            'portfolio_score': 0.91,
            'intelligence_score': 0.99,
            'retrain_state': 'ready',
            'retrain_priority': 'low',
            'allocation_reason': 'row_override',
            'allocation_rank': 7,
            'portfolio_feedback': {'allocator_blocked': False, 'portfolio_score': 0.91},
        },
        ctx=_ctx(),
        repo_root=repo_root,
    )

    assert intent.allocation_batch_id == 'alloc_002'
    assert intent.portfolio_score == 0.91
    assert intent.intelligence_score == 0.99
    assert intent.retrain_state == 'ready'
    assert intent.retrain_priority == 'low'
    assert intent.allocation_reason == 'row_override'
    assert intent.allocation_rank == 7
    assert json.loads(str(intent.portfolio_feedback_json))['portfolio_score'] == 0.91
