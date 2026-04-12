from __future__ import annotations

import json
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1] / 'scripts' / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import capture_portfolio_canary_bundle as module  # type: ignore


def test_default_config_prefers_canary(tmp_path: Path) -> None:
    cfg = tmp_path / 'config'
    cfg.mkdir(parents=True)
    (cfg / 'practice_portfolio_canary.yaml').write_text('version: "2.0"\n', encoding='utf-8')
    (cfg / 'live_controlled_practice.yaml').write_text('version: "2.0"\n', encoding='utf-8')
    chosen = module._default_config(tmp_path)
    assert chosen == cfg / 'practice_portfolio_canary.yaml'


def test_bundle_summary_tracks_recommended_scope(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    (bundle / 'evidence_window_scan').mkdir(parents=True)
    (bundle / 'asset_candidate_best').mkdir(parents=True)
    (bundle / 'provider_probe_all_scopes').mkdir(parents=True)
    (bundle / 'production_gate_all_scopes').mkdir(parents=True)
    (bundle / 'portfolio_status').mkdir(parents=True)

    (bundle / 'evidence_window_scan' / 'last_json.json').write_text(json.dumps({
        'kind': 'evidence_window_scan',
        'ok': True,
        'severity': 'ok',
        'recommended_scope': {
            'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': 'EURUSD-OTC_300s'},
            'score': 88.0,
            'window_state': 'ready',
        },
    }), encoding='utf-8')
    (bundle / 'asset_candidate_best' / 'last_json.json').write_text(json.dumps({
        'phase': 'asset_candidate',
        'ok': True,
        'candidate': {'action': 'CALL'},
    }), encoding='utf-8')
    (bundle / 'provider_probe_all_scopes' / 'last_json.json').write_text(json.dumps({'summary': {'provider_ready_scopes': 2}}), encoding='utf-8')
    (bundle / 'production_gate_all_scopes' / 'last_json.json').write_text(json.dumps({'ready_for_all_scopes': True}), encoding='utf-8')
    (bundle / 'portfolio_status' / 'last_json.json').write_text(json.dumps({'multi_asset': {'asset_count': 6}}), encoding='utf-8')

    results = [
        {'name': 'evidence_window_scan', 'parsed_summary': {'last_json_kind': 'evidence_window_scan'}},
        {'name': 'asset_candidate_best', 'parsed_summary': {'last_json_kind': 'asset_candidate'}},
        {'name': 'provider_probe_all_scopes', 'parsed_summary': {'last_json_kind': 'provider_probe'}},
        {'name': 'production_gate_all_scopes', 'parsed_summary': {'last_json_kind': 'production_gate'}},
        {'name': 'portfolio_status', 'parsed_summary': {'last_json_kind': 'portfolio_status'}},
    ]
    summary = module._bundle_summary(bundle, results)
    assert summary['portfolio_canary']['recommended_scope']['scope']['scope_tag'] == 'EURUSD-OTC_300s'
    assert summary['portfolio_canary']['candidate_latest_action'] == 'CALL'
    assert summary['portfolio_canary']['provider_ready_scopes'] == 2
