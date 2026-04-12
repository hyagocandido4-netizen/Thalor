from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from natbin.ops.config_provenance import build_config_provenance_payload
from natbin.ops.practice_preflight import build_practice_preflight_payload
from natbin.ops.runtime_artifact_audit import build_runtime_artifact_audit_payload
from natbin.usecases.refresh_daily_summary import refresh_daily_summaries


def _write_cfg(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: live_controlled_practice',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: PRACTICE',
                'broker:',
                '  provider: iqoption',
                '  balance_mode: PRACTICE',
                'data:',
                '  db_path: data/market_otc.sqlite3',
                '  dataset_path: data/dataset_phase2.csv',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                'notifications:',
                '  telegram:',
                '    enabled: true',
                '    send_enabled: true',
                'security:',
                '  deployment_profile: local',
            ]
        ) + '\n',
        encoding='utf-8',
    )
    return cfg


def test_config_provenance_prefers_yaml_winner_when_bundle_override_is_ignored(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_cfg(tmp_path)
    bundle = tmp_path / 'config' / 'broker_secrets.yaml'
    bundle.write_text(
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: ultra-secret',
                '  balance_mode: PRACTICE',
            ]
        ) + '\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('THALOR_SECRETS_FILE', 'config/broker_secrets.yaml')
    payload = build_config_provenance_payload(repo_root=tmp_path, config_path=cfg)
    field = next(item for item in payload['field_provenance'] if item['field'] == 'broker.balance_mode')
    check = next(item for item in payload['checks'] if item['name'] == 'secret_bundle_balance_mode_override')
    assert field['winner'] == 'yaml'
    assert field['bundle_override_present'] is True
    assert field['bundle_override_effective'] is False
    assert check['status'] == 'ok'
    assert 'Remova broker.balance_mode do secret bundle' not in payload['actions']


def test_refresh_daily_summary_bootstraps_signals_schema_when_db_is_empty(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path)
    dataset = tmp_path / 'data' / 'dataset_phase2.csv'
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text('ts,y_open_close\n1,1\n', encoding='utf-8')
    db_path = tmp_path / 'runs' / 'live_signals.sqlite3'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()

    result = refresh_daily_summaries(
        cfg={
            'data': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'timezone': 'UTC'},
            'phase2': {'dataset_path': str(dataset)},
        },
        wanted=['2026-04-01'],
        db_path=str(db_path),
        out_dir=str(tmp_path / 'runs'),
        force_today_stub=False,
    )
    assert result['days_written'] == 0

    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signals_v2'").fetchone()
        assert row is not None
    finally:
        con.close()


def test_runtime_artifact_audit_ignores_informative_stale_or_missing_optional_artifacts(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path)
    scope_tag = 'EURUSD-OTC_300s'
    now = datetime.now(UTC)
    old = now - timedelta(days=2)

    runs = tmp_path / 'runs'
    (runs / 'config').mkdir(parents=True, exist_ok=True)
    (runs / 'control' / scope_tag).mkdir(parents=True, exist_ok=True)

    (runs / 'config' / f'effective_config_latest_{scope_tag}.json').write_text(json.dumps({'at_utc': now.isoformat(timespec='seconds')}), encoding='utf-8')
    core_names = ['effective_config', 'loop_status', 'health', 'doctor', 'intelligence']
    for name in core_names:
        target = runs / 'control' / scope_tag / f'{name}.json'
        payload = {'at_utc': now.isoformat(timespec='seconds'), 'kind': name, 'config_path': str(cfg)}
        target.write_text(json.dumps(payload), encoding='utf-8')

    mc = runs / f'market_context_{scope_tag}.json'
    mc.write_text(json.dumps({'at_utc': now.isoformat(timespec='seconds'), 'market_open': True}), encoding='utf-8')

    (runs / 'control' / scope_tag / 'release.json').write_text(
        json.dumps({'at_utc': old.isoformat(timespec='seconds'), 'config_path': str(tmp_path / 'config' / 'live_controlled_real.yaml')}),
        encoding='utf-8',
    )
    (runs / 'control' / scope_tag / 'incidents.json').write_text(
        json.dumps({'at_utc': old.isoformat(timespec='seconds'), 'total': 0, 'incidents': {'total': 0}}),
        encoding='utf-8',
    )

    payload = build_runtime_artifact_audit_payload(repo_root=tmp_path, config_path=cfg)
    assert payload['severity'] == 'ok'
    artifacts = {item['name']: item for item in payload['scope_results'][0]['artifacts']}
    assert artifacts['release']['status'] == 'ok'
    assert artifacts['incidents']['status'] == 'ok'
    assert artifacts['retrain']['status'] == 'ok'
    assert artifacts['practice_round']['status'] == 'ok'


def test_practice_preflight_enables_provider_probe_by_default(monkeypatch, tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path)

    import natbin.ops.practice_preflight as module

    captured: dict[str, object] = {}

    def fake_diag_suite_payload(**kwargs):
        captured['active_provider_probe'] = kwargs.get('active_provider_probe')
        return {
            'kind': 'diag_suite',
            'ok': True,
            'severity': 'ok',
            'ready_for_practice': True,
            'checks': [],
            'actions': [],
            'results': {
                'practice': {
                    'ready_for_practice': True,
                    'checks': [
                        {'name': 'drain_mode', 'status': 'ok'},
                        {'name': 'runtime_soak', 'status': 'ok'},
                        {'name': 'production_doctor', 'status': 'ok'},
                    ],
                }
            },
        }

    def fake_transport(**kwargs):
        return {'kind': 'transport_smoke', 'ok': True, 'severity': 'ok', 'actions': [], 'scope_results': [{'actions': []}]}

    def fake_modules(**kwargs):
        return {'kind': 'module_smoke', 'ok': True, 'severity': 'ok', 'actions': []}

    monkeypatch.setattr(module, 'build_diag_suite_payload', fake_diag_suite_payload)
    monkeypatch.setattr(module, 'build_transport_smoke_payload', fake_transport)
    monkeypatch.setattr(module, 'build_module_smoke_payload', fake_modules)
    monkeypatch.setattr(module, 'maybe_heal_market_context', lambda **kwargs: {'name': 'market_context', 'status': 'skip', 'enabled': True, 'attempted': False, 'message': 'test_skip'})
    monkeypatch.setattr(module, 'maybe_heal_control_freshness', lambda **kwargs: {'name': 'control_freshness', 'status': 'skip', 'enabled': True, 'attempted': False, 'message': 'test_skip'})

    payload = build_practice_preflight_payload(repo_root=tmp_path, config_path=cfg, dry_run=False)
    assert payload['ok'] is True
    assert captured['active_provider_probe'] is True
