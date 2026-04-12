from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from natbin.incidents.reporting import incident_status_payload
from natbin.ops.production_doctor import build_production_doctor_payload
from natbin.runtime.breaker_analysis import classify_cycle_outcomes
from natbin.runtime.breaker_diagnostics import build_breaker_artifact_payload
from natbin.runtime.failsafe import CircuitBreakerPolicy, CircuitBreakerSnapshot, RuntimeFailsafe
from natbin.runtime.precheck import run_precheck
from natbin.runtime.hardening import RuntimeHardeningReport
from natbin.state.control_repo import RuntimeControlRepository, control_artifact_paths, write_control_artifact


def _write_cfg(repo: Path) -> Path:
    cfg = repo / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: test',
                'execution:',
                '  enabled: false',
                '  mode: disabled',
                '  provider: iqoption',
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


def _write_dataset(path: Path, rows: int = 120) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fh:
        fh.write('ts,feature_a\n')
        base_ts = 1773300000
        for idx in range(rows):
            fh.write(f'{base_ts + idx * 300},{1.0 + idx / 1000.0}\n')


def _write_breaker_artifact(repo: Path, *, cfg: Path, error: str) -> dict:
    breaker = CircuitBreakerSnapshot(
        asset='EURUSD-OTC',
        interval_sec=300,
        state='open',
        failures=3,
        reason='broker_bootstrap_collect_recent_failed',
        primary_cause='broker_bootstrap_collect_recent_failed',
        failure_domain='broker_bootstrap',
        failure_step='collect_recent',
        last_transport_error=error,
        last_transport_failure_utc=datetime.now(UTC),
    )
    market_context = {
        'asset': 'EURUSD-OTC',
        'interval_sec': 300,
        'degraded': True,
        'dependency_available': False,
        'failure_kind': 'broker_failure',
        'dependency_reason': error,
        'open_source': 'fallback_cache',
        'at_utc': datetime.now(UTC).isoformat(timespec='seconds'),
    }
    payload = build_breaker_artifact_payload(
        repo_root=repo,
        asset='EURUSD-OTC',
        interval_sec=300,
        breaker=breaker,
        market_context=market_context,
        failsafe_snapshot={
            'blocked_reason': 'circuit_open',
            'half_open_trial_available': False,
            'half_open_trials_remaining': 0,
            'half_open_trial_in_flight': False,
        },
        connectivity={
            'transport': {
                'enabled': True,
                'ready': True,
                'endpoint_count': 1,
                'available_endpoint_count': 0,
                'endpoints': [
                    {
                        'name': 'proxy-primary',
                        'last_error': error,
                    }
                ],
            }
        },
    )
    write_control_artifact(repo_root=repo, asset='EURUSD-OTC', interval_sec=300, name='breaker', payload=payload)
    return payload


def test_precheck_does_not_consume_half_open_trial_during_observation(tmp_path: Path) -> None:
    repo = tmp_path
    control_repo = RuntimeControlRepository(repo / 'runs' / 'runtime_control.sqlite3')
    breaker = CircuitBreakerSnapshot(
        asset='EURUSD-OTC',
        interval_sec=300,
        state='half_open',
        failures=3,
        reason='broker_bootstrap_collect_recent_failed',
        primary_cause='broker_bootstrap_collect_recent_failed',
        failure_domain='broker_bootstrap',
    )
    control_repo.save_breaker(breaker)
    failsafe = RuntimeFailsafe(policy=CircuitBreakerPolicy(half_open_trials=1))

    blocked = run_precheck(
        failsafe,
        asset='EURUSD-OTC',
        interval_sec=300,
        control_repo=control_repo,
        market_context={'stale': True, 'market_open': True},
        enforce_market_context=True,
    )
    snap1 = control_repo.load_breaker('EURUSD-OTC', 300)
    assert blocked.blocked is True
    assert blocked.reason == 'market_context_stale'
    assert snap1.half_open_trials_used == 0
    assert snap1.half_open_trial_in_flight is False

    allowed = run_precheck(
        failsafe,
        asset='EURUSD-OTC',
        interval_sec=300,
        control_repo=control_repo,
        market_context={'stale': False, 'market_open': True},
        enforce_market_context=True,
    )
    snap2 = control_repo.load_breaker('EURUSD-OTC', 300)
    assert allowed.blocked is False
    assert snap2.half_open_trials_used == 0
    assert snap2.half_open_trial_in_flight is False


def test_classify_cycle_outcomes_marks_broker_bootstrap_transport_failures() -> None:
    failure = classify_cycle_outcomes(
        [
            {
                'name': 'collect_recent',
                'ok': False,
                'kind': 'nonzero_exit',
                'stdout_tail': '',
                'stderr_tail': 'JSONDecodeError: proxy upstream bad gateway during broker connect',
            }
        ]
    )
    assert failure.failure_domain == 'broker_bootstrap'
    assert failure.primary_cause == 'broker_bootstrap_collect_recent_failed'
    assert 'proxy' in str(failure.transport_error or '').lower()


def test_incident_status_surfaces_breaker_primary_cause_and_transport_error(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_cfg(tmp_path)
    error = 'proxy upstream bad gateway'
    payload = _write_breaker_artifact(tmp_path, cfg=cfg, error=error)
    assert payload['primary_cause']['code'] == 'broker_transport_failure'

    monkeypatch.setattr(
        'natbin.incidents.reporting.build_release_readiness_payload',
        lambda **kwargs: {'severity': 'ok', 'ready_for_live': False, 'execution_live': False},
    )
    monkeypatch.setattr(
        'natbin.incidents.reporting.alerts_status_payload',
        lambda **kwargs: {'telegram': {'enabled': False, 'send_enabled': False, 'credentials_present': False, 'recent_counts': {}, 'recent': []}},
    )
    monkeypatch.setattr('natbin.incidents.reporting.gate_status', lambda **kwargs: {'kill_switch': {'active': False}, 'drain_mode': {'active': False}})
    monkeypatch.setattr(
        'natbin.incidents.reporting.audit_security_posture',
        lambda **kwargs: {'blocked': False, 'severity': 'ok', 'credential_source': 'external_secret_file'},
    )
    monkeypatch.setattr(
        'natbin.incidents.reporting.build_intelligence_surface_payload',
        lambda **kwargs: {'enabled': False, 'severity': 'ok', 'warnings': [], 'summary': {}, 'allocation': {}, 'execution': {'missing_fields': []}},
    )
    monkeypatch.setattr(
        'natbin.incidents.reporting.inspect_runtime_freshness',
        lambda **kwargs: RuntimeHardeningReport(
            scope_tag='EURUSD-OTC_300s',
            checked_at_utc=datetime.now(tz=UTC).isoformat(timespec='seconds'),
            stale_after_sec=900,
            lock={},
            artifacts=[],
            stale_artifacts=[],
            actions=[],
            mode='inspect',
        ),
    )
    monkeypatch.setattr('natbin.incidents.reporting._health_summary', lambda *args, **kwargs: {'state': 'blocked', 'message': 'circuit_open'})
    monkeypatch.setattr('natbin.incidents.reporting._loop_summary', lambda *args, **kwargs: {'phase': 'failed', 'message': 'cycle_failed'})
    monkeypatch.setattr('natbin.incidents.reporting.load_recent_scope_incidents', lambda **kwargs: [])
    monkeypatch.setattr('natbin.incidents.reporting._summarize_incidents', lambda recent: {'total': 0, 'by_type': {}, 'by_severity': {}, 'latest': None})

    status = incident_status_payload(repo_root=tmp_path, config_path=cfg, write_artifact=False)
    assert status['breaker']['primary_cause']['code'] == 'broker_transport_failure'
    issues = {item['code']: item for item in status['open_issues']}
    assert issues['breaker_primary_cause']['primary_cause_code'] == 'broker_transport_failure'
    assert error in str(issues['breaker_primary_cause']['last_transport_error'])
    assert issues['health_not_ok']['primary_cause_code'] == 'broker_transport_failure'


def test_production_doctor_includes_breaker_diagnostics(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_cfg(tmp_path)
    _write_dataset(tmp_path / 'data' / 'dataset_phase2.csv', rows=180)
    market_path = tmp_path / 'runs' / 'runtime' / 'EURUSD-OTC_300s' / 'market_context.json'
    market_path.parent.mkdir(parents=True, exist_ok=True)
    market_path.write_text(
        json.dumps(
            {
                'asset': 'EURUSD-OTC',
                'interval_sec': 300,
                'market_open': True,
                'open_source': 'db_fresh',
                'payout': 0.85,
                'payout_source': 'turbo',
                'last_candle_ts': 1773340200,
                'at_utc': datetime.now(UTC).isoformat(timespec='seconds'),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    fresh = {'at_utc': datetime.now(UTC).isoformat(timespec='seconds'), 'state': 'healthy'}
    write_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='loop_status', payload=fresh)
    write_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='health', payload=fresh)
    _write_breaker_artifact(tmp_path, cfg=cfg, error='proxy upstream bad gateway')

    monkeypatch.setattr(
        'natbin.ops.production_doctor.audit_security_posture',
        lambda **kwargs: {'blocked': False, 'severity': 'ok', 'checks': [], 'credential_source': 'external_secret_file'},
    )
    monkeypatch.setattr(
        'natbin.ops.production_doctor.build_retention_payload',
        lambda **kwargs: {'candidates_total': 0, 'categories': {}},
    )
    monkeypatch.setattr(
        'natbin.ops.intelligence_surface.build_intelligence_surface_payload',
        lambda **kwargs: {'enabled': False, 'severity': 'ok', 'warnings': [], 'summary': {}, 'allocation': {}, 'execution': {'missing_fields': []}},
    )

    payload = build_production_doctor_payload(repo_root=tmp_path, config_path=cfg, write_artifact=False)
    checks = {item['name']: item for item in payload['checks']}
    assert checks['circuit_breaker_diagnostics']['primary_cause_code'] == 'broker_transport_failure'
    assert 'proxy' in str(checks['circuit_breaker_diagnostics']['last_transport_error']).lower()
    assert checks['circuit_breaker_diagnostics']['status'] in {'warn', 'error'}


def test_control_artifact_paths_include_breaker(tmp_path: Path) -> None:
    paths = control_artifact_paths(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300)
    assert str(paths['breaker']).endswith('breaker.json')
