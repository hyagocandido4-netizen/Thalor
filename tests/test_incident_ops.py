from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.incidents.reporting import (
    incident_alert_payload,
    incident_drill_payload,
    incident_report_payload,
    incident_status_payload,
)


def _touch(path: Path, body: str = 'x\n') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')


def _write_repo(repo: Path, *, send_enabled: bool = False) -> Path:
    _touch(repo / 'README.md', '# repo\n')
    _touch(repo / '.env.example', 'THALOR_SECRETS_FILE="secrets/bundle.yaml"\n')
    _touch(repo / 'requirements.txt', 'pydantic>=2\n')
    _touch(repo / 'pyproject.toml', '[build-system]\nrequires=["setuptools"]\n')
    _touch(repo / 'setup.cfg', '[metadata]\nname=natbin\n')
    _touch(repo / 'src' / 'natbin' / 'runtime_app.py', '# placeholder\n')
    _touch(repo / 'src' / 'natbin' / 'incidents' / 'reporting.py', '# placeholder\n')
    _touch(repo / 'scripts' / 'tools' / 'release_bundle.py', '# placeholder\n')
    _touch(repo / 'scripts' / 'tools' / 'incident_ops_smoke.py', '# placeholder\n')
    _touch(repo / 'docs/history/package_legacy/README_PACKAGE_M7_1_APPEND.md', '# m71\n')
    _touch(repo / 'Dockerfile', 'FROM python:3.12-slim\n')
    _touch(repo / 'docker-compose.yml', 'services: {}\n')
    _touch(repo / 'docker-compose.prod.yml', 'services: {}\n')
    _touch(repo / '.gitignore', '.env\nsecrets/\nruns/\ndata/\n')
    for name in [
        'OPERATIONS.md',
        'DOCKER.md',
        'ALERTING_M7.md',
        'PRODUCTION_CHECKLIST_M7.md',
        'DIAGRAMS_M7.md',
        'INCIDENT_RUNBOOKS_M71.md',
        'LIVE_OPS_HARDENING_M71.md',
    ]:
        _touch(repo / 'docs' / name, f'# {name}\n')
    bundle = repo / 'secrets' / 'bundle.yaml'
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: trader-secret',
                '  balance_mode: PRACTICE',
                'telegram:',
                '  bot_token: 123456:ABCDEF',
                '  chat_id: "999888777"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    cfg = repo / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  startup_invalidate_stale_artifacts: true',
                '  lock_refresh_enable: true',
                'security:',
                '  deployment_profile: live',
                '  secrets_file: secrets/bundle.yaml',
                '  live_require_external_credentials: true',
                'notifications:',
                '  enabled: true',
                '  telegram:',
                f'    send_enabled: {str(send_enabled).lower()}',
                '    enabled: true',
                'multi_asset:',
                '  enabled: true',
                '  max_parallel_assets: 2',
                'execution:',
                '  enabled: true',
                '  mode: live',
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


def test_incident_status_payload_reports_recent_warning(tmp_path: Path) -> None:
    cfg_path = _write_repo(tmp_path, send_enabled=False)
    day = datetime.now(tz=UTC).strftime('%Y%m%d')
    incident_file = tmp_path / 'runs' / 'incidents' / f'incidents_{day}_EURUSD-OTC_300s.jsonl'
    incident_file.parent.mkdir(parents=True, exist_ok=True)
    incident_file.write_text(
        json.dumps(
            {
                'kind': 'incident',
                'incident_type': 'market_context_stale',
                'severity': 'warning',
                'recorded_at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
                'asset': 'EURUSD-OTC',
                'interval_sec': 300,
                'day': datetime.now(tz=UTC).strftime('%Y-%m-%d'),
                'ts': int(datetime.now(tz=UTC).timestamp()),
            }
        )
        + '\n',
        encoding='utf-8',
    )
    payload = incident_status_payload(repo_root=tmp_path, config_path=cfg_path, limit=10, window_hours=24)
    assert payload['kind'] == 'incident_status'
    assert payload['severity'] in {'warn', 'error'}
    assert payload['intelligence']['severity'] in {'ok', 'warn'}
    codes = {item['code'] for item in payload['open_issues']}
    assert 'recent_warning_incidents' in codes
    artifact = tmp_path / 'runs' / 'control' / 'EURUSD-OTC_300s' / 'incidents.json'
    assert artifact.exists()


def test_incident_report_alert_and_drill(tmp_path: Path) -> None:
    cfg_path = _write_repo(tmp_path, send_enabled=False)
    report = incident_report_payload(repo_root=tmp_path, config_path=cfg_path, limit=10, window_hours=24)
    assert report['kind'] == 'incident_report'
    paths = report['artifacts']
    assert Path(paths['report_path']).exists()
    alert = incident_alert_payload(repo_root=tmp_path, config_path=cfg_path, limit=10, window_hours=24)
    assert alert['kind'] == 'incident_alert'
    assert ((alert['alert'] or {}).get('delivery') or {}).get('status') == 'queued'
    drill = incident_drill_payload(repo_root=tmp_path, config_path=cfg_path, scenario='db_lock')
    assert drill['kind'] == 'incident_drill'
    assert drill['scenario'] == 'db_lock'
    assert drill['commands']


def test_incident_status_payload_uses_non_conflicting_health_and_loop_keys(tmp_path: Path, monkeypatch) -> None:
    cfg_path = _write_repo(tmp_path, send_enabled=False)

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
        lambda **kwargs: {'enabled': True, 'severity': 'ok', 'warnings': [], 'summary': {}, 'allocation': {}, 'execution': {'missing_fields': []}},
    )

    from natbin.runtime.hardening import RuntimeHardeningReport

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
    monkeypatch.setattr('natbin.incidents.reporting._loop_summary', lambda *args, **kwargs: {'phase': 'failed', 'message': 'portfolio_cycle_failure'})
    monkeypatch.setattr('natbin.incidents.reporting.load_recent_scope_incidents', lambda **kwargs: [])
    monkeypatch.setattr('natbin.incidents.reporting._summarize_incidents', lambda recent: {'total': 0, 'by_type': {}, 'by_severity': {}, 'latest': None})

    payload = incident_status_payload(repo_root=tmp_path, config_path=cfg_path, write_artifact=False)
    issues = {item['code']: item for item in payload['open_issues']}
    assert issues['health_not_ok']['health_message'] == 'circuit_open'
    assert issues['loop_failure_recent']['loop_message'] == 'portfolio_cycle_failure'
