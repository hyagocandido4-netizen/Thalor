from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from natbin.ops import signal_artifact_audit as mod


def _scope(asset: str = 'EURUSD-OTC', interval_sec: int = 300):
    return SimpleNamespace(asset=asset, interval_sec=interval_sec, scope_tag=f'{asset}_{interval_sec}s')


def test_signal_artifact_audit_uses_scoped_candidate_when_decision_latest_missing(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    cfg_path = repo / 'config.yaml'
    cfg_path.write_text('x: 1', encoding='utf-8')
    scope = _scope()
    cfg = SimpleNamespace(
        runtime=SimpleNamespace(profile='practice_portfolio_canary'),
        intelligence=SimpleNamespace(artifact_dir='runs/intelligence'),
    )

    monkeypatch.setattr(mod, 'load_selected_scopes', lambda **kwargs: (repo, cfg_path, cfg, [scope]))
    monkeypatch.setattr(mod, '_scope_payload', lambda repo_root, current_scope: (repo / 'runs' / 'decisions' / f'decision_latest_{current_scope.scope_tag}.json', None))
    monkeypatch.setattr(
        mod,
        '_load_candidate_entry',
        lambda **kwargs: (
            {
                'finished_at_utc': '2026-04-11T06:20:00+00:00',
                'source': {'source': 'scoped'},
                'item': {
                    'scope_tag': scope.scope_tag,
                    'asset': scope.asset,
                    'interval_sec': 300,
                    'action': 'HOLD',
                    'reason': 'regime_block',
                    'conf': 0.55,
                    'score': 0.0,
                    'ev': -0.2,
                },
            },
            {'source': 'scoped'},
        ),
    )
    monkeypatch.setattr(mod, '_load_allocation_entry', lambda **kwargs: (None, {'source': 'missing'}))

    eval_path = repo / 'runs' / 'intelligence' / scope.scope_tag / 'latest_eval.json'
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    eval_path.write_text(
        json.dumps(
            {
                'kind': 'intelligence_eval',
                'evaluated_at_utc': '2026-04-11T06:20:01+00:00',
                'allow_trade': False,
                'status': 'hold',
            }
        ),
        encoding='utf-8',
    )

    payload = mod.build_signal_artifact_audit_payload(repo_root=repo, config_path=cfg_path, all_scopes=True, write_artifact=False)

    assert payload['summary']['missing_artifact_scopes'] == 0
    assert payload['summary']['watch_scopes'] == 1
    item = payload['scope_results'][0]
    assert item['exists'] is True
    assert item['missing'] is False
    assert 'candidate:scoped' in item['artifact_sources']
    assert item['candidate_reason'] == 'regime_block'
    assert item['dominant_reason'] == 'regime_block'
