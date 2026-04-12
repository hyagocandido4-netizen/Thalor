from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

TOOLS = Path(__file__).resolve().parents[1] / 'scripts' / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import capture_provider_stability_bundle as stability_bundle  # type: ignore
import invoke_runtime_app as invoke_module  # type: ignore
import portfolio_canary_warmup as warmup_module  # type: ignore
from _capture_json import write_json_summary  # type: ignore

from natbin.ops.provider_session_governor import build_provider_session_governor_payload
from natbin.utils.provider_issue_taxonomy import classify_provider_issue


def test_provider_issue_taxonomy_classifies_missing_underlying_list() -> None:
    assert classify_provider_issue('missing_underlying_list')['category'] == 'upstream_digital_metadata'


def test_provider_session_governor_degraded_from_stability_artifact(monkeypatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / 'config.yaml'
    cfg_path.write_text('version: "2.0"\n', encoding='utf-8')
    scope = SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')
    monkeypatch.setattr(
        'natbin.ops.provider_session_governor.load_selected_scopes',
        lambda **kwargs: (tmp_path, cfg_path, SimpleNamespace(), [scope]),
    )
    repo_artifacts = tmp_path / 'runs' / 'control' / '_repo'
    repo_artifacts.mkdir(parents=True, exist_ok=True)
    (repo_artifacts / 'provider_stability.json').write_text(json.dumps({
        'stability_state': 'degraded',
        'severity': 'warn',
        'summary': {
            'scope_count': 6,
            'provider_ready_scopes': 6,
            'transient_noise_categories': ['websocket_lifecycle', 'upstream_digital_metadata'],
            'hard_blockers': [],
        },
    }), encoding='utf-8')

    payload = build_provider_session_governor_payload(repo_root=tmp_path, config_path=cfg_path, all_scopes=True, write_artifact=False)
    assert payload['severity'] == 'warn'
    assert payload['summary']['governor_mode'] == 'serial_guarded'
    assert payload['governor']['sleep_between_scopes_ms'] >= 1000
    assert payload['governor']['allow_parallel_execution'] is False


def test_capture_json_prefers_top_level_kind(tmp_path: Path) -> None:
    text = '{"kind":"provider_stability_report","ok":true,"severity":"warn","summary":{"provider_ready_scopes":6},"transport_hint":{"configured":true}}'
    summary = write_json_summary(base_dir=tmp_path, stdout_text=text)
    assert summary['last_json_kind'] == 'provider_stability_report'
    payload = json.loads((tmp_path / 'last_json.json').read_text(encoding='utf-8'))
    assert payload['kind'] == 'provider_stability_report'


def test_portfolio_canary_warmup_skips_fresh_scope(monkeypatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / 'config.yaml'
    cfg_path.write_text('version: "2.0"\n', encoding='utf-8')
    scope = SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')
    monkeypatch.setattr(warmup_module, 'load_selected_scopes', lambda **kwargs: (tmp_path, cfg_path, SimpleNamespace(), [scope]))
    monkeypatch.setattr(
        warmup_module,
        'build_provider_session_governor_payload',
        lambda **kwargs: {'governor': {'mode': 'serial_guarded', 'skip_fresh_market_context_scopes': True, 'sleep_between_scopes_ms': 0, 'refresh_market_context_timeout_sec': 30, 'asset_prepare_timeout_sec': 60, 'max_asset_prepare_fallback_scopes': 0}},
    )
    monkeypatch.setattr(
        warmup_module,
        '_market_context_state',
        lambda repo, scope_tag, interval_sec: {'path': str(tmp_path / 'runs' / f'market_context_{scope_tag}.json'), 'exists': True, 'fresh': True, 'age_sec': 5.0, 'at_utc': '2026-04-07T00:00:00+00:00', 'market_open': True, 'open_source': 'db_fresh', 'dependency_available': True, 'dependency_reason': None},
    )

    def _boom(*args, **kwargs):
        raise AssertionError('warmup should skip remote work for fresh scope')

    monkeypatch.setattr(warmup_module, 'refresh_market_context_safe', _boom)
    monkeypatch.setattr(warmup_module, '_run_asset_prepare', _boom)

    payload = warmup_module.build_warmup_payload(repo_root=tmp_path, config_path=cfg_path, all_scopes=True)
    assert payload['ok'] is True
    assert payload['summary']['skipped_fresh_scopes'] == 1
    assert payload['scope_results'][0]['strategy'] == 'skip_fresh'


def test_provider_stability_bundle_summary_includes_governor(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    for name in ('provider_probe_all_scopes', 'portfolio_canary_warmup', 'evidence_window_scan', 'portfolio_canary_signal_scan', 'provider_stability_report', 'provider_session_governor'):
        (bundle / name).mkdir(parents=True, exist_ok=True)
    (bundle / 'provider_probe_all_scopes' / 'last_json.json').write_text(json.dumps({'summary': {'provider_ready_scopes': 6}}), encoding='utf-8')
    (bundle / 'portfolio_canary_warmup' / 'last_json.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
    (bundle / 'evidence_window_scan' / 'last_json.json').write_text(json.dumps({'best_scope': {'scope': {'scope_tag': 'AUDUSD-OTC_300s'}}}), encoding='utf-8')
    (bundle / 'portfolio_canary_signal_scan' / 'last_json.json').write_text(json.dumps({'summary': {'actionable_scopes': 0, 'healthy_waiting_signal': True}}), encoding='utf-8')
    (bundle / 'provider_stability_report' / 'last_json.json').write_text(json.dumps({'stability_state': 'degraded', 'severity': 'warn', 'summary': {'transient_noise_categories': ['websocket_lifecycle'], 'recorded_issue_events': 4}}), encoding='utf-8')
    (bundle / 'provider_session_governor' / 'last_json.json').write_text(json.dumps({'summary': {'governor_mode': 'serial_guarded', 'sleep_between_scopes_ms': 1250}}), encoding='utf-8')
    results = [
        {'name': 'provider_probe_all_scopes', 'parsed_summary': {'last_json_kind': 'provider_probe'}},
        {'name': 'portfolio_canary_warmup', 'parsed_summary': {'last_json_kind': 'portfolio_canary_warmup'}},
        {'name': 'evidence_window_scan', 'parsed_summary': {'last_json_kind': 'evidence_window_scan'}},
        {'name': 'portfolio_canary_signal_scan', 'parsed_summary': {'last_json_kind': 'portfolio_canary_signal_scan'}},
        {'name': 'provider_stability_report', 'parsed_summary': {'last_json_kind': 'provider_stability_report'}},
        {'name': 'provider_session_governor', 'parsed_summary': {'last_json_kind': 'provider_session_governor'}},
    ]
    summary = stability_bundle._bundle_summary(bundle, results)
    assert summary['provider_session_shield']['governor_mode'] == 'serial_guarded'
    assert summary['provider_session_shield']['governor_sleep_between_scopes_ms'] == 1250


def test_invoke_runtime_app_routes_provider_session_governor(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    tools = repo / 'scripts' / 'tools'
    tools.mkdir(parents=True, exist_ok=True)
    (tools / 'provider_session_governor.py').write_text('print("ok")\n', encoding='utf-8')

    captured: dict[str, object] = {}

    def fake_default_repo_root(_file: str) -> Path:
        return repo

    def fake_run_script(repo_root: Path, script_name: str, script_args: list[str], *, explicit_python: str | None = None, verbose: bool = False) -> int:
        captured['repo_root'] = repo_root
        captured['script_name'] = script_name
        captured['script_args'] = list(script_args)
        return 0

    monkeypatch.setattr(invoke_module, 'default_repo_root', fake_default_repo_root)
    monkeypatch.setattr(invoke_module, '_run_script', fake_run_script)
    monkeypatch.setattr(sys, 'argv', ['invoke_runtime_app.py', '--config', 'config/practice_portfolio_canary.yaml', 'provider-session-governor', '--all-scopes', '--json'])

    code = invoke_module.main()
    assert code == 0
    assert captured['script_name'] == 'provider_session_governor.py'
