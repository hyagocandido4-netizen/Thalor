from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'scripts' / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import invoke_runtime_app as launcher  # type: ignore
import capture_provider_stability_bundle as bundle_module  # type: ignore


def test_invoke_runtime_app_routes_provider_stability_report(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    tools = repo / 'scripts' / 'tools'
    tools.mkdir(parents=True)
    # Presence of wrapper scripts is enough for routing test.
    (tools / 'provider_stability_report.py').write_text('print("ok")\n', encoding='utf-8')
    monkeypatch.setattr(launcher, 'default_repo_root', lambda script_path: repo)

    captured: dict[str, object] = {}

    def fake_run_script(repo_root, script_name, script_args, *, explicit_python=None, verbose=False):
        captured['repo_root'] = repo_root
        captured['script_name'] = script_name
        captured['script_args'] = list(script_args)
        return 0

    monkeypatch.setattr(launcher, '_run_script', fake_run_script)
    monkeypatch.setattr(sys, 'argv', ['invoke_runtime_app.py', '--config', 'config/practice_portfolio_canary.yaml', 'provider-stability-report', '--all-scopes', '--json'])
    rc = launcher.main()
    assert rc == 0
    assert captured['script_name'] == 'provider_stability_report.py'
    assert '--all-scopes' in captured['script_args']
    assert '--json' in captured['script_args']


def test_invoke_runtime_app_routes_portfolio_canary_signal_scan(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    tools = repo / 'scripts' / 'tools'
    tools.mkdir(parents=True)
    (tools / 'portfolio_canary_signal_scan.py').write_text('print("ok")\n', encoding='utf-8')
    monkeypatch.setattr(launcher, 'default_repo_root', lambda script_path: repo)

    captured: dict[str, object] = {}

    def fake_run_script(repo_root, script_name, script_args, *, explicit_python=None, verbose=False):
        captured['script_name'] = script_name
        captured['script_args'] = list(script_args)
        return 0

    monkeypatch.setattr(launcher, '_run_script', fake_run_script)
    monkeypatch.setattr(sys, 'argv', ['invoke_runtime_app.py', '--config', 'config/practice_portfolio_canary.yaml', 'portfolio-canary-signal-scan', '--all-scopes', '--json'])
    rc = launcher.main()
    assert rc == 0
    assert captured['script_name'] == 'portfolio_canary_signal_scan.py'
    assert '--all-scopes' in captured['script_args']


def test_capture_provider_stability_bundle_summary_reads_direct_wrappers(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    for name in ('provider_probe_all_scopes', 'portfolio_canary_warmup', 'evidence_window_scan', 'portfolio_canary_signal_scan', 'provider_stability_report'):
        (bundle / name).mkdir(parents=True, exist_ok=True)
    (bundle / 'provider_probe_all_scopes' / 'last_json.json').write_text(json.dumps({'summary': {'provider_ready_scopes': 6}}), encoding='utf-8')
    (bundle / 'portfolio_canary_warmup' / 'last_json.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
    (bundle / 'evidence_window_scan' / 'last_json.json').write_text(json.dumps({'best_scope': {'scope': {'scope_tag': 'EURGBP-OTC_300s'}}}), encoding='utf-8')
    (bundle / 'portfolio_canary_signal_scan' / 'last_json.json').write_text(json.dumps({'kind': 'portfolio_canary_signal_proof', 'summary': {'actionable_scopes': 0, 'healthy_waiting_signal': True}}), encoding='utf-8')
    (bundle / 'provider_stability_report' / 'last_json.json').write_text(json.dumps({'stability_state': 'degraded', 'severity': 'warn', 'summary': {'transient_noise_categories': ['session_parse'], 'recorded_issue_events': 4}}), encoding='utf-8')
    results = [
        {'name': 'provider_probe_all_scopes', 'parsed_summary': {'last_json_kind': 'provider_probe'}},
        {'name': 'portfolio_canary_warmup', 'parsed_summary': {'last_json_kind': 'portfolio_canary_warmup'}},
        {'name': 'evidence_window_scan', 'parsed_summary': {'last_json_kind': 'evidence_window_scan'}},
        {'name': 'portfolio_canary_signal_scan', 'parsed_summary': {'last_json_kind': 'portfolio_canary_signal_proof'}},
        {'name': 'provider_stability_report', 'parsed_summary': {'last_json_kind': 'provider_stability_report'}},
    ]
    summary = bundle_module._bundle_summary(bundle, results)
    assert summary['provider_session_shield']['stability_state'] == 'degraded'
    assert summary['provider_session_shield']['signal_healthy_waiting'] is True
