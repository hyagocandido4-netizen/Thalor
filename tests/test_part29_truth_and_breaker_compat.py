from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

from natbin.runtime.failsafe import CircuitBreakerSnapshot
from natbin.ops.provider_stability import build_provider_stability_payload
from natbin.ops.provider_session_governor import build_provider_session_governor_payload
from scripts.tools.canary_go_no_go import run_closure_report


class _Proc:
    def __init__(self, *, returncode: int = 0, stdout: str = '{}', stderr: str = '') -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_circuit_breaker_snapshot_from_mapping_ignores_unknown_fields() -> None:
    snap = CircuitBreakerSnapshot.from_mapping(
        {
            'asset': 'EURUSD-OTC',
            'interval_sec': '300',
            'state': 'open',
            'failures': '2',
            'primary_cause': 'broker_transport_failure',
            'half_open_trial_in_flight': 1,
            'future_field': 'ignore-me',
        }
    )
    assert snap.asset == 'EURUSD-OTC'
    assert snap.interval_sec == 300
    assert snap.primary_cause == 'broker_transport_failure'
    assert snap.half_open_trial_in_flight is True


def test_provider_stability_refreshes_stale_provider_probe_artifact(monkeypatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / 'config.yaml'
    cfg_path.write_text('version: "2.0"\n', encoding='utf-8')
    scope = SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')
    monkeypatch.setattr(
        'natbin.ops.provider_stability.load_selected_scopes',
        lambda **kwargs: (tmp_path, cfg_path, SimpleNamespace(), [scope]),
    )
    repo_artifacts = tmp_path / 'runs' / 'control' / '_repo'
    repo_artifacts.mkdir(parents=True, exist_ok=True)
    stale_at = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat(timespec='seconds')
    (repo_artifacts / 'provider_probe.json').write_text(
        json.dumps({'at_utc': stale_at, 'summary': {'scope_count': 1, 'provider_ready_scopes': 0}, 'severity': 'error'}),
        encoding='utf-8',
    )

    calls: list[dict[str, object]] = []

    def fake_build_provider_probe_payload(**kwargs):
        calls.append(kwargs)
        return {
            'kind': 'provider_probe',
            'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
            'severity': 'ok',
            'summary': {'scope_count': 1, 'provider_ready_scopes': 1},
            'shared_provider_session': {'ok': True},
            'transport_hint': {'configured': True, 'scheme': 'socks5h'},
            'checks': [{'name': 'provider_session', 'status': 'ok', 'message': 'ok'}],
            'scope_results': [],
        }

    monkeypatch.setattr('natbin.ops.provider_stability.build_provider_probe_payload', fake_build_provider_probe_payload)
    payload = build_provider_stability_payload(
        repo_root=tmp_path,
        config_path=cfg_path,
        all_scopes=True,
        active_provider_probe=False,
        refresh_probe=False,
        artifact_max_age_sec=60,
        write_artifact=False,
    )
    assert calls, 'stale provider probe should force a fresh provider probe rebuild'
    assert payload['artifacts']['provider_probe_fresh'] is True
    assert payload['artifacts']['freshness']['provider_probe']['refreshed'] is True
    assert payload['artifacts']['freshness']['provider_probe']['was_stale'] is True


def test_provider_session_governor_refreshes_stale_stability_artifact(monkeypatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / 'config.yaml'
    cfg_path.write_text('version: "2.0"\n', encoding='utf-8')
    scope = SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')
    monkeypatch.setattr(
        'natbin.ops.provider_session_governor.load_selected_scopes',
        lambda **kwargs: (tmp_path, cfg_path, SimpleNamespace(), [scope]),
    )
    repo_artifacts = tmp_path / 'runs' / 'control' / '_repo'
    repo_artifacts.mkdir(parents=True, exist_ok=True)
    stale_at = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat(timespec='seconds')
    (repo_artifacts / 'provider_stability.json').write_text(
        json.dumps({'at_utc': stale_at, 'severity': 'warn', 'stability_state': 'degraded', 'summary': {'scope_count': 1, 'provider_ready_scopes': 1}}),
        encoding='utf-8',
    )

    calls: list[dict[str, object]] = []

    def fake_build_provider_stability_payload(**kwargs):
        calls.append(kwargs)
        return {
            'kind': 'provider_stability_report',
            'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
            'severity': 'ok',
            'stability_state': 'stable',
            'summary': {
                'scope_count': 1,
                'provider_ready_scopes': 1,
                'transient_noise_categories': [],
                'hard_blockers': [],
            },
            'categories': [],
        }

    monkeypatch.setattr('natbin.ops.provider_session_governor.build_provider_stability_payload', fake_build_provider_stability_payload)
    payload = build_provider_session_governor_payload(
        repo_root=tmp_path,
        config_path=cfg_path,
        all_scopes=True,
        active_provider_probe=False,
        refresh_stability=False,
        stability_max_age_sec=60,
        write_artifact=False,
    )
    assert calls, 'stale provider_stability should force a refresh before emitting governor'
    assert payload['artifacts']['provider_stability']['refreshed'] is True
    assert payload['summary']['stability_state'] == 'stable'


def test_canary_go_no_go_closure_report_forces_active_provider_probe(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    tools = tmp_path / 'scripts' / 'tools'
    tools.mkdir(parents=True, exist_ok=True)
    (tools / 'portfolio_canary_closure_report.py').write_text('print("ok")\n', encoding='utf-8')

    monkeypatch.setattr('scripts.tools.canary_go_no_go._python_executable', lambda repo_root: 'python')

    def fake_run(cmd, **kwargs):
        captured['cmd'] = list(cmd)
        return _Proc(returncode=0, stdout='{}', stderr='')

    monkeypatch.setattr('scripts.tools.canary_go_no_go.subprocess.run', fake_run)
    run_closure_report(repo_root=tmp_path, config='config/practice_portfolio_canary.yaml', all_scopes=True)
    assert '--active-provider-probe' in captured['cmd']
