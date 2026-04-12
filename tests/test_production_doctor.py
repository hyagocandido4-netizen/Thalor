from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.control.plan import build_context
from natbin.ops.production_doctor import build_production_doctor_payload
from natbin.state.control_repo import write_control_artifact


SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_dataset(path: Path, rows: int = 150) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fh:
        fh.write('ts,feature_a\n')
        base_ts = 1773300000
        for idx in range(rows):
            fh.write(f'{base_ts + idx * 300},{1.0 + idx / 1000.0}\n')


def _seed_runtime_surface(repo: Path, *, execution_live: bool = False) -> Path:
    cfg = repo / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: prod',
                'execution:',
                f'  enabled: {str(execution_live).lower()}',
                f'  mode: {"live" if execution_live else "disabled"}',
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
    ctx = build_context(repo_root=repo, config_path=cfg, dump_snapshot=False)
    _write_dataset(repo / 'data' / 'dataset_phase2.csv', rows=180)
    market_path = Path(ctx.scoped_paths['market_context'])
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
    write_control_artifact(repo_root=repo, asset='EURUSD-OTC', interval_sec=300, name='loop_status', payload=fresh)
    write_control_artifact(repo_root=repo, asset='EURUSD-OTC', interval_sec=300, name='health', payload=fresh)
    intel_dir = repo / 'runs' / 'intelligence' / SCOPE_TAG
    intel_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat(timespec='seconds')
    for name, payload in {
        'pack.json': {
            'kind': 'intelligence_pack',
            'generated_at_utc': now,
            'metadata': {'training_rows': 180},
        },
        'latest_eval.json': {
            'kind': 'intelligence_eval',
            'evaluated_at_utc': now,
            'allow_trade': True,
            'intelligence_score': 0.68,
            'portfolio_score': 0.72,
            'portfolio_feedback': {'allocator_blocked': False, 'portfolio_score': 0.72},
            'retrain_orchestration': {'state': 'idle', 'priority': 'low'},
        },
        'retrain_plan.json': {
            'kind': 'retrain_plan',
            'at_utc': now,
            'state': 'idle',
            'priority': 'low',
        },
        'retrain_status.json': {
            'kind': 'retrain_status',
            'updated_at_utc': now,
            'state': 'idle',
            'priority': 'low',
        },
    }.items():
        (intel_dir / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    return cfg


def test_production_doctor_ok_for_local_ready_repo(tmp_path: Path) -> None:
    cfg = _seed_runtime_surface(tmp_path, execution_live=False)
    payload = build_production_doctor_payload(repo_root=tmp_path, config_path=cfg)
    assert payload['kind'] == 'production_doctor'
    assert payload['severity'] == 'ok'
    assert payload['ready_for_cycle'] is True
    assert payload['ready_for_live'] is False
    assert payload['ready_for_practice'] is False
    assert payload['ready_for_real'] is False
    names = {item['name']: item for item in payload['checks']}
    assert names['dataset_ready']['status'] == 'ok'
    assert names['market_context']['status'] == 'ok'
    assert names['effective_config_artifacts']['status'] == 'ok'
    assert names['intelligence_surface']['status'] == 'ok'
    assert payload['intelligence']['severity'] == 'ok'
    artifact = tmp_path / 'runs' / 'control' / SCOPE_TAG / 'doctor.json'
    assert artifact.exists()


def test_production_doctor_flags_live_broker_missing_credentials(tmp_path: Path, monkeypatch) -> None:
    cfg = _seed_runtime_surface(tmp_path, execution_live=True)

    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})
    payload = build_production_doctor_payload(repo_root=tmp_path, config_path=cfg, probe_broker=False)
    assert payload['severity'] == 'error'
    assert payload['ready_for_practice'] is False
    assert payload['ready_for_real'] is False
    assert 'broker_preflight' in payload['blockers']
    broker = next(item for item in payload['checks'] if item['name'] == 'broker_preflight')
    assert broker['reason'] == 'iqoption_missing_credentials'


def test_production_doctor_accepts_local_durable_alert_contract_for_practice_live(tmp_path: Path, monkeypatch) -> None:
    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})

    cfg = tmp_path / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: live_controlled_practice',
                'security:',
                '  deployment_profile: live',
                '  live_require_external_credentials: true',
                '  secrets_file: secrets/bundle.yaml',
                'notifications:',
                '  enabled: true',
                '  telegram:',
                '    enabled: false',
                '    send_enabled: false',
                'broker:',
                '  provider: iqoption',
                '  balance_mode: PRACTICE',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: PRACTICE',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    bundle = tmp_path / 'secrets' / 'bundle.yaml'
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: trader-secret',
                '  balance_mode: PRACTICE',
                '',
            ]
        ),
        encoding='utf-8',
    )
    ctx = build_context(repo_root=tmp_path, config_path=cfg, dump_snapshot=False)
    _write_dataset(tmp_path / 'data' / 'dataset_phase2.csv', rows=180)
    now = datetime.now(UTC).isoformat(timespec='seconds')
    Path(ctx.scoped_paths['market_context']).write_text(
        json.dumps(
            {
                'asset': 'EURUSD-OTC',
                'interval_sec': 300,
                'market_open': True,
                'open_source': 'db_fresh',
                'payout': 0.85,
                'payout_source': 'turbo',
                'last_candle_ts': 1773340200,
                'at_utc': now,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    fresh = {'at_utc': now, 'state': 'healthy'}
    write_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='loop_status', payload=fresh)
    write_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='health', payload=fresh)
    scope_tag = 'EURUSD-OTC_300s'
    intel_dir = tmp_path / 'runs' / 'intelligence' / scope_tag
    intel_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in {
        'pack.json': {'kind': 'intelligence_pack', 'generated_at_utc': now, 'metadata': {'training_rows': 180}},
        'latest_eval.json': {
            'kind': 'intelligence_eval',
            'evaluated_at_utc': now,
            'allow_trade': True,
            'intelligence_score': 0.68,
            'portfolio_score': 0.72,
            'portfolio_feedback': {'allocator_blocked': False, 'portfolio_score': 0.72},
            'retrain_orchestration': {'state': 'idle', 'priority': 'low'},
        },
        'retrain_plan.json': {'kind': 'retrain_plan', 'at_utc': now, 'state': 'idle', 'priority': 'low'},
        'retrain_status.json': {'kind': 'retrain_status', 'updated_at_utc': now, 'state': 'idle', 'priority': 'low'},
    }.items():
        (intel_dir / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')

    payload = build_production_doctor_payload(repo_root=tmp_path, config_path=cfg, probe_broker=False)
    checks = {item['name']: item for item in payload['checks']}
    assert checks['alerting_ready']['status'] == 'ok'
    assert checks['alerting_ready']['alerting_contract'] == 'local_durable'
    assert payload['ready_for_practice'] is True


def test_production_doctor_requires_external_alert_channel_for_real_account(tmp_path: Path, monkeypatch) -> None:
    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})

    cfg = tmp_path / 'config' / 'live_controlled_real.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: live_controlled_real',
                'security:',
                '  deployment_profile: live',
                '  live_require_external_credentials: true',
                '  secrets_file: secrets/bundle.yaml',
                'notifications:',
                '  enabled: true',
                '  telegram:',
                '    enabled: false',
                '    send_enabled: false',
                'broker:',
                '  provider: iqoption',
                '  balance_mode: REAL',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: REAL',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    bundle = tmp_path / 'secrets' / 'bundle.yaml'
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: trader-secret',
                '  balance_mode: REAL',
                '',
            ]
        ),
        encoding='utf-8',
    )
    ctx = build_context(repo_root=tmp_path, config_path=cfg, dump_snapshot=False)
    _write_dataset(tmp_path / 'data' / 'dataset_phase2.csv', rows=180)
    now = datetime.now(UTC).isoformat(timespec='seconds')
    Path(ctx.scoped_paths['market_context']).write_text(
        json.dumps(
            {
                'asset': 'EURUSD-OTC',
                'interval_sec': 300,
                'market_open': True,
                'open_source': 'db_fresh',
                'payout': 0.85,
                'payout_source': 'turbo',
                'last_candle_ts': 1773340200,
                'at_utc': now,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    fresh = {'at_utc': now, 'state': 'healthy'}
    write_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='loop_status', payload=fresh)
    write_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='health', payload=fresh)
    scope_tag = 'EURUSD-OTC_300s'
    intel_dir = tmp_path / 'runs' / 'intelligence' / scope_tag
    intel_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in {
        'pack.json': {'kind': 'intelligence_pack', 'generated_at_utc': now, 'metadata': {'training_rows': 180}},
        'latest_eval.json': {
            'kind': 'intelligence_eval',
            'evaluated_at_utc': now,
            'allow_trade': True,
            'intelligence_score': 0.68,
            'portfolio_score': 0.72,
            'portfolio_feedback': {'allocator_blocked': False, 'portfolio_score': 0.72},
            'retrain_orchestration': {'state': 'idle', 'priority': 'low'},
        },
        'retrain_plan.json': {'kind': 'retrain_plan', 'at_utc': now, 'state': 'idle', 'priority': 'low'},
        'retrain_status.json': {'kind': 'retrain_status', 'updated_at_utc': now, 'state': 'idle', 'priority': 'low'},
    }.items():
        (intel_dir / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')

    payload = build_production_doctor_payload(repo_root=tmp_path, config_path=cfg, probe_broker=False)
    checks = {item['name']: item for item in payload['checks']}
    assert checks['alerting_ready']['status'] == 'error'
    assert payload['ready_for_real'] is False
