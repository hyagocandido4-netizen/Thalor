from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.intelligence.ops_state import build_intelligence_ops_state, write_intelligence_ops_state
from natbin.ops.intelligence_surface import build_intelligence_surface_payload

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
                '  enabled: false',
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
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def test_intelligence_surface_treats_review_only_tuning_as_consistent_after_rejected_cooldown(tmp_path: Path) -> None:
    cfg = _write_repo(tmp_path)
    scope_tag = 'EURUSD-OTC_300s'
    intel_dir = tmp_path / 'runs' / 'intelligence' / scope_tag

    _write_json(intel_dir / 'pack.json', {'kind': 'intelligence_pack', 'generated_at_utc': NOW, 'anti_overfit': {'available': True, 'accepted': False}})
    _write_json(
        intel_dir / 'latest_eval.json',
        {
            'kind': 'intelligence_eval',
            'evaluated_at_utc': NOW,
            'allow_trade': False,
            'anti_overfit': {'available': True, 'accepted': False, 'robustness_score': 0.41, 'penalty': 0.12},
            'retrain_orchestration': {'state': 'cooldown', 'priority': 'high'},
        },
    )
    _write_json(intel_dir / 'retrain_plan.json', {'kind': 'retrain_plan', 'state': 'cooldown', 'priority': 'high', 'cooldown_active': True, 'cooldown_until_utc': '2099-03-24T00:00:00+00:00'})
    _write_json(intel_dir / 'retrain_status.json', {'kind': 'retrain_status', 'state': 'rejected', 'priority': 'high', 'plan_state': 'cooldown', 'plan_priority': 'high'})
    _write_json(intel_dir / 'retrain_review.json', {'kind': 'retrain_review', 'generated_at_utc': NOW, 'verdict': 'rejected', 'reason': 'hard_regression', 'executed': True, 'restored_previous_artifacts': True})
    _write_json(
        intel_dir / 'anti_overfit_tuning_review.json',
        {
            'kind': 'anti_overfit_tuning_review',
            'generated_at_utc': NOW,
            'verdict': 'rejected',
            'tuning': {'selected_variant': 'recent_balanced_relief', 'baseline_variant': 'baseline', 'improved': False},
        },
    )
    ops_state = build_intelligence_ops_state(
        scope_tag=scope_tag,
        asset='EURUSD-OTC',
        interval_sec=300,
        pack_payload=json.loads((intel_dir / 'pack.json').read_text(encoding='utf-8')),
        eval_payload=json.loads((intel_dir / 'latest_eval.json').read_text(encoding='utf-8')),
        retrain_plan=json.loads((intel_dir / 'retrain_plan.json').read_text(encoding='utf-8')),
        retrain_status=json.loads((intel_dir / 'retrain_status.json').read_text(encoding='utf-8')),
        retrain_review=json.loads((intel_dir / 'retrain_review.json').read_text(encoding='utf-8')),
        anti_overfit_tuning_review=json.loads((intel_dir / 'anti_overfit_tuning_review.json').read_text(encoding='utf-8')),
    )
    write_intelligence_ops_state(repo_root=tmp_path, scope_tag=scope_tag, payload=ops_state)

    payload = build_intelligence_surface_payload(repo_root=tmp_path, config_path=cfg, write_artifact=False)
    by_name = {item['name']: item for item in payload['checks']}

    assert by_name['ops_state']['status'] == 'ok'
    assert by_name['anti_overfit_tuning']['status'] == 'ok'
    assert by_name['retrain_review']['status'] == 'ok'
    assert payload['effective_state']['consistency']['expected_review_only_tuning'] is True
