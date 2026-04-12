from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
TOOLS = ROOT / 'scripts' / 'tools'
for candidate in (SRC, TOOLS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import capture_portfolio_canary_bundle as canary_bundle  # type: ignore
from natbin.ops import evidence_window_scan as evidence_scan  # type: ignore
from natbin.ops import provider_stability as provider_stability  # type: ignore
from natbin.ops import production_gate as production_gate  # type: ignore
import natbin.control.commands as control_commands  # type: ignore


def test_provider_stability_ignores_benign_recorded_events(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / 'config.yaml'
    cfg.write_text('version: "2.0"\n', encoding='utf-8')
    scope = SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')

    monkeypatch.setattr(
        provider_stability,
        'load_selected_scopes',
        lambda **kwargs: (tmp_path, cfg, SimpleNamespace(), [scope]),
    )
    monkeypatch.setattr(
        provider_stability,
        'build_provider_probe_payload',
        lambda **kwargs: {
            'kind': 'provider_probe',
            'severity': 'ok',
            'summary': {'scope_count': 1, 'provider_ready_scopes': 1},
            'scope_results': [],
        },
    )
    monkeypatch.setattr(
        provider_stability,
        'read_provider_issue_events',
        lambda repo, limit=200: [
            {'reason': 'watch'},
            {'normalized_reason': 'rescan_next_candle'},
            {'reason': 'missing_underlying_list'},
        ],
    )

    payload = provider_stability.build_provider_stability_payload(
        repo_root=tmp_path,
        config_path=cfg,
        all_scopes=True,
        refresh_probe=True,
        write_artifact=False,
    )
    categories = {str(item.get('category')): int(item.get('count') or 0) for item in list(payload.get('categories') or [])}
    assert categories.get('unknown', 0) == 0
    assert categories.get('upstream_digital_metadata', 0) >= 1


def test_evidence_window_scan_uses_cached_provider_artifact_when_governed(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    cfg = repo / 'config.yaml'
    cfg.write_text('version: "2.0"\n', encoding='utf-8')
    scope = SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')
    governor_path = repo / 'runs' / 'control' / '_repo' / 'provider_session_governor.json'
    governor_path.parent.mkdir(parents=True, exist_ok=True)
    governor_path.write_text(json.dumps({
        'summary': {'stability_state': 'degraded'},
        'governor': {'prefer_cached_provider_artifacts': True},
    }), encoding='utf-8')
    provider_artifact = repo / 'runs' / 'control' / '_repo' / 'provider_probe.json'
    provider_artifact.write_text(json.dumps({
        'kind': 'provider_probe',
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'severity': 'ok',
        'ok': True,
        'summary': {'scope_count': 1, 'provider_ready_scopes': 1},
        'shared_provider_session': {'attempted': True, 'ok': True},
        'transport_hint': {'configured': True},
        'scope_results': [{
            'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': 'EURUSD-OTC_300s'},
            'severity': 'ok',
            'ok': True,
            'checks': [],
            'actions': [],
            'shared_provider_session': {'attempted': True, 'ok': True},
            'local_market_context': {'fresh': True, 'market_open': True},
            'remote_candles': {'attempted': True, 'ok': True},
            'remote_market_context': {'attempted': True, 'ok': True},
        }],
    }), encoding='utf-8')

    monkeypatch.setattr(
        evidence_scan,
        'load_selected_scopes',
        lambda **kwargs: (repo, cfg, SimpleNamespace(multi_asset=SimpleNamespace(enabled=True, max_parallel_assets=1, portfolio_topk_total=1, portfolio_hard_max_positions=1), execution=SimpleNamespace(account_mode='PRACTICE', stake=SimpleNamespace(amount=2.0), limits=SimpleNamespace(max_pending_unknown=1, max_open_positions=1)), broker=SimpleNamespace(balance_mode='PRACTICE')), [scope]),
    )
    monkeypatch.setattr(
        control_commands,
        'portfolio_status_payload',
        lambda **kwargs: {'asset_board': [{'scope_tag': 'EURUSD-OTC_300s', 'budget_left': 1, 'pending_unknown': 0, 'open_positions': 0, 'latest_action': 'HOLD'}], 'intelligence': {'items': []}},
    )
    monkeypatch.setattr(
        evidence_scan,
        'build_production_doctor_payload',
        lambda **kwargs: {'severity': 'ok', 'ready_for_cycle': True, 'ready_for_practice': True, 'blockers': [], 'warnings': [], 'actions': []},
    )
    calls: list[dict[str, object]] = []

    def fake_probe(**kwargs):
        calls.append(dict(kwargs))
        return {'kind': 'provider_probe', 'severity': 'ok', 'summary': {'scope_count': 1, 'provider_ready_scopes': 1}, 'scope_results': []}

    monkeypatch.setattr(evidence_scan, 'build_provider_probe_payload', fake_probe)

    payload = evidence_scan.build_evidence_window_scan_payload(
        repo_root=repo,
        config_path=cfg,
        all_scopes=True,
        active_provider_probe=True,
        write_artifact=False,
    )
    assert payload['provider_probe']['strategy'] == 'artifact_cached_due_governor'
    assert payload['provider_probe']['active_requested'] is True
    assert payload['provider_probe']['active_effective'] is False
    assert calls == []


def test_bundle_summary_falls_back_to_governor_when_scan_missing(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    for name in ['portfolio_canary_signal_scan', 'asset_candidate_best', 'production_gate_all_scopes', 'portfolio_status', 'portfolio_canary_warmup', 'provider_session_governor']:
        (bundle / name).mkdir(parents=True)

    (bundle / 'portfolio_canary_signal_scan' / 'last_json.json').write_text(json.dumps({
        'kind': 'portfolio_canary_signal_proof',
        'ok': True,
        'severity': 'warn',
        'best_watch_scope': {'scope': {'asset': 'USDJPY-OTC', 'interval_sec': 300, 'scope_tag': 'USDJPY-OTC_300s'}, 'window_state': 'watch'},
        'summary': {'recommended_action': 'wait_regime_rescan', 'watch_scopes': 3, 'dominant_nontrade_reason': 'regime_block'},
    }), encoding='utf-8')
    (bundle / 'asset_candidate_best' / 'last_json.json').write_text(json.dumps({'phase': 'asset_candidate', 'ok': True, 'candidate': {'action': 'HOLD'}}), encoding='utf-8')
    (bundle / 'production_gate_all_scopes' / 'last_json.json').write_text(json.dumps({'ready_for_all_scopes': True, 'summary': {'provider_ready_count': 6}}), encoding='utf-8')
    (bundle / 'portfolio_status' / 'last_json.json').write_text(json.dumps({'multi_asset': {'asset_count': 6}}), encoding='utf-8')
    (bundle / 'portfolio_canary_warmup' / 'last_json.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
    (bundle / 'provider_session_governor' / 'last_json.json').write_text(json.dumps({'summary': {'provider_ready_scopes': 6, 'governor_mode': 'serial_guarded', 'sleep_between_scopes_ms': 1500}}), encoding='utf-8')

    results = [
        {'name': 'portfolio_canary_signal_scan', 'parsed_summary': {'last_json_kind': 'portfolio_canary_signal_proof'}},
        {'name': 'asset_candidate_best', 'parsed_summary': {'last_json_kind': 'asset_candidate'}},
        {'name': 'production_gate_all_scopes', 'parsed_summary': {'last_json_kind': 'production_gate'}},
        {'name': 'portfolio_status', 'parsed_summary': {'last_json_kind': 'portfolio_status'}},
        {'name': 'portfolio_canary_warmup', 'parsed_summary': {'last_json_kind': 'portfolio_canary_warmup'}},
        {'name': 'provider_session_governor', 'parsed_summary': {'last_json_kind': 'provider_session_governor'}},
    ]
    summary = canary_bundle._bundle_summary(bundle, results)
    assert summary['portfolio_canary']['provider_ready_scopes'] == 6
    assert summary['portfolio_canary']['scan_severity'] == 'warn'
    assert summary['portfolio_canary']['recommended_scope']['scope']['scope_tag'] == 'USDJPY-OTC_300s'


def test_capture_bundle_defaults_to_governed_scan(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    cfg_dir = repo / 'config'
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / 'practice_portfolio_canary.yaml'
    cfg.write_text('version: "2.0"\n', encoding='utf-8')
    calls: list[tuple[str, list[str]]] = []

    def fake_run_one(*, name, argv, cwd, env, timeout_sec, output_dir):
        step_dir = output_dir / name
        step_dir.mkdir(parents=True, exist_ok=True)
        payload = {}
        if name == 'provider_session_governor':
            payload = {'kind': 'provider_session_governor', 'summary': {'provider_ready_scopes': 6, 'governor_mode': 'serial_guarded', 'sleep_between_scopes_ms': 1500}}
        elif name == 'portfolio_canary_signal_scan':
            payload = {'kind': 'portfolio_canary_signal_proof', 'ok': True, 'severity': 'warn', 'best_watch_scope': {'scope': {'asset': 'USDJPY-OTC', 'interval_sec': 300, 'scope_tag': 'USDJPY-OTC_300s'}}, 'summary': {'watch_scopes': 3, 'recommended_action': 'wait_regime_rescan'}}
        elif name == 'asset_candidate_best':
            payload = {'phase': 'asset_candidate', 'ok': True, 'candidate': {'action': 'HOLD'}}
        elif name == 'production_gate_all_scopes':
            payload = {'kind': 'production_gate', 'ready_for_all_scopes': True, 'summary': {'provider_ready_count': 6}}
        elif name == 'portfolio_status':
            payload = {'multi_asset': {'asset_count': 6}}
        elif name == 'portfolio_canary_warmup':
            payload = {'kind': 'portfolio_canary_warmup', 'ok': True}
        elif name == 'status':
            payload = {'config': {'config_path': str(cfg)}}
        if payload:
            (step_dir / 'last_json.json').write_text(json.dumps(payload), encoding='utf-8')
        calls.append((name, list(argv)))
        return {'name': name, 'returncode': 0, 'timed_out': False, 'parsed_summary': {'last_json_kind': payload.get('kind') if isinstance(payload, dict) else None}}

    monkeypatch.setattr(canary_bundle, '_repo_root_from_script', lambda: repo)
    monkeypatch.setattr(canary_bundle, '_find_python', lambda repo_root: 'python')
    monkeypatch.setattr(canary_bundle, '_run_one', fake_run_one)
    monkeypatch.setattr(canary_bundle, '_zip_dir', lambda source_dir, zip_path: zip_path.write_bytes(b'zip'))
    monkeypatch.setattr(canary_bundle.sys, 'argv', ['capture_portfolio_canary_bundle.py'])

    rc = canary_bundle.main()
    assert rc == 0
    names = [name for name, _ in calls]
    assert names.index('provider_session_governor') < names.index('evidence_window_scan')
    evidence_argv = next(argv for name, argv in calls if name == 'evidence_window_scan')
    assert '--active-provider-probe' not in evidence_argv
