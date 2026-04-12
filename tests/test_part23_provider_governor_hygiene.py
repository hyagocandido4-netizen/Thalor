from __future__ import annotations

import json
from pathlib import Path

from types import SimpleNamespace

from natbin.ops.provider_stability import build_provider_stability_payload
from natbin.runtime.provider_issue_recorder import record_provider_issue_event
from natbin.utils.provider_issue_taxonomy import aggregate_provider_issue_texts


def _write_cfg(tmp_path: Path) -> Path:
    cfg = tmp_path / 'config.yaml'
    cfg.write_text(
        '''execution:
  enabled: true
  provider: iqoption
  account_mode: PRACTICE
broker:
  provider: iqoption
  balance_mode: PRACTICE
asset: EURUSD-OTC
interval_sec: 300
''',
        encoding='utf-8',
    )
    return cfg


def test_aggregate_provider_issue_texts_ignores_benign_markers() -> None:
    payload = aggregate_provider_issue_texts(['returncode=0', 'timed_out=False', 'strategy=skip_fresh', '{"asset":"EURUSD-OTC","interval_sec":300,"market_open":true,"open_source":"db_fresh"}'])
    assert payload['total_events'] == 0
    assert list(payload['categories'] or []) == []


def test_provider_stability_uses_signal_proof_alias_and_keeps_strategy_no_trade_non_blocking(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_cfg(tmp_path)
    repo = tmp_path
    art = repo / 'runs' / 'control' / '_repo'
    art.mkdir(parents=True, exist_ok=True)
    (art / 'provider_probe.json').write_text(json.dumps({'summary': {'scope_count': 6, 'provider_ready_scopes': 6}, 'severity': 'ok', 'shared_provider_session': {'ok': True}, 'transport_hint': {'configured': True}}), encoding='utf-8')
    (art / 'portfolio_canary_warmup.json').write_text(json.dumps({'summary': {'effective_ready_scopes': 6}}), encoding='utf-8')
    (art / 'evidence_window_scan.json').write_text(json.dumps({'severity': 'warn', 'best_scope': {'recommended_action': 'hold_regime_block'}, 'scope_results': []}), encoding='utf-8')
    (art / 'portfolio_canary_signal_proof.json').write_text(json.dumps({'severity': 'warn', 'summary': {'actionable_scopes': 0, 'healthy_waiting_signal': False}, 'best_watch_scope': {'candidate': {'reason': 'regime_block'}}, 'results': []}), encoding='utf-8')
    record_provider_issue_event(repo_root=repo, operation='iqoption:digital_underlying_payload', source='iq_client.instance_patch', reason='missing_underlying_list', dedupe_window_sec=0.0)
    monkeypatch.setattr('natbin.ops.provider_stability.load_selected_scopes', lambda **kwargs: (repo, cfg, SimpleNamespace(), [SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')]))
    payload = build_provider_stability_payload(repo_root=repo, config_path=cfg, all_scopes=True, active_provider_probe=False, refresh_probe=False, write_artifact=False)
    assert payload['artifacts']['signal_scan_present'] is True
    cats = {row['category']: row for row in payload['categories']}
    assert cats['upstream_digital_metadata']['status'] == 'warn'
    assert cats['strategy_no_trade']['status'] == 'ok'
    assert payload['summary']['provider_ready_scopes'] == 6


def test_record_provider_issue_event_dedupes_within_window(tmp_path: Path) -> None:
    repo = tmp_path
    first = record_provider_issue_event(repo_root=repo, operation='iqoption:digital_underlying_payload', source='iq_client.instance_patch', reason='missing_underlying_list', dedupe_window_sec=60.0, dedupe_key='same')
    second = record_provider_issue_event(repo_root=repo, operation='iqoption:digital_underlying_payload', source='iq_client.instance_patch', reason='missing_underlying_list', dedupe_window_sec=60.0, dedupe_key='same')
    assert first is not None
    assert second is None
    rows = (repo / 'runs' / 'logs' / 'provider_issues.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(rows) == 1
