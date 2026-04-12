from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from natbin.control import app as control_app
from natbin.ops.dependency_audit import build_dependency_audit_payload
from natbin.ops.guardrail_audit import build_guardrail_audit_payload
from natbin.ops.runtime_artifact_audit import build_runtime_artifact_audit_payload
from natbin.ops.state_db_audit import build_state_db_audit_payload
from natbin.runtime.scope import market_context_path
from natbin.state.control_repo import RuntimeControlRepository, read_control_artifact, read_repo_control_artifact, write_control_artifact
from natbin.state.db import open_db, upsert_candles
from natbin.state.execution_migrations import ensure_execution_db
from natbin.state.migrations import ensure_executed_state_db, ensure_signals_v2


def _write_config(
    repo_root: Path,
    *,
    multi_asset: bool = False,
    execution_mode: str = 'paper',
    account_mode: str = 'PRACTICE',
    broker_balance_mode: str = 'PRACTICE',
) -> Path:
    lines = [
        'version: "2.0"',
        'runtime:',
        '  profile: diagnostic_piece3',
        'execution:',
        '  enabled: true',
        f'  mode: {execution_mode}',
        '  provider: iqoption',
        f'  account_mode: {account_mode}',
        'broker:',
        '  provider: iqoption',
        f'  balance_mode: {broker_balance_mode}',
        'data:',
        '  db_path: data/market_otc.sqlite3',
        '  dataset_path: data/dataset_phase2.csv',
        'multi_asset:',
        f'  enabled: {str(multi_asset).lower()}',
        '  max_parallel_assets: 6',
        '  partition_data_paths: true',
        '  data_db_template: data/market_{scope_tag}.sqlite3',
        '  dataset_path_template: data/datasets/{scope_tag}/dataset.csv',
        'assets:',
        '  - asset: EURUSD-OTC',
        '    interval_sec: 300',
        '    timezone: UTC',
    ]
    if multi_asset:
        lines.extend([
            '  - asset: GBPUSD-OTC',
            '    interval_sec: 300',
            '    timezone: UTC',
        ])
    cfg = repo_root / 'config' / 'piece3.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return cfg


def _scope_tag(asset: str, interval_sec: int) -> str:
    return f'{asset}_{interval_sec}s'


def _seed_scope_runtime(repo_root: Path, *, asset: str, interval_sec: int, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    scope_tag = _scope_tag(asset, interval_sec)
    runs = repo_root / 'runs'
    (runs / 'config').mkdir(parents=True, exist_ok=True)
    (runs / 'control' / scope_tag).mkdir(parents=True, exist_ok=True)

    (runs / 'config' / f'effective_config_latest_{scope_tag}.json').write_text(
        json.dumps({'at_utc': now.isoformat(timespec='seconds'), 'scope_tag': scope_tag}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    write_control_artifact(
        repo_root=repo_root,
        asset=asset,
        interval_sec=interval_sec,
        name='effective_config',
        payload={'at_utc': now.isoformat(timespec='seconds'), 'scope_tag': scope_tag},
    )
    write_control_artifact(
        repo_root=repo_root,
        asset=asset,
        interval_sec=interval_sec,
        name='loop_status',
        payload={'at_utc': now.isoformat(timespec='seconds'), 'scope_tag': scope_tag, 'ok': True},
    )
    write_control_artifact(
        repo_root=repo_root,
        asset=asset,
        interval_sec=interval_sec,
        name='health',
        payload={'at_utc': now.isoformat(timespec='seconds'), 'scope_tag': scope_tag, 'ready': True},
    )
    write_control_artifact(
        repo_root=repo_root,
        asset=asset,
        interval_sec=interval_sec,
        name='doctor',
        payload={'at_utc': now.isoformat(timespec='seconds'), 'scope_tag': scope_tag, 'ok': True, 'severity': 'ok'},
    )
    write_control_artifact(
        repo_root=repo_root,
        asset=asset,
        interval_sec=interval_sec,
        name='intelligence',
        payload={'at_utc': now.isoformat(timespec='seconds'), 'scope_tag': scope_tag, 'enabled': True, 'severity': 'ok'},
    )
    write_control_artifact(
        repo_root=repo_root,
        asset=asset,
        interval_sec=interval_sec,
        name='release',
        payload={'at_utc': now.isoformat(timespec='seconds'), 'scope_tag': scope_tag, 'ok': True, 'severity': 'ok'},
    )
    mc_path = Path(market_context_path(asset=asset, interval_sec=interval_sec, out_dir=repo_root / 'runs'))
    mc_path.parent.mkdir(parents=True, exist_ok=True)
    mc_path.write_text(
        json.dumps(
            {
                'asset': asset,
                'interval_sec': interval_sec,
                'market_open': True,
                'open_source': 'db_fresh',
                'payout': 0.85,
                'payout_source': 'turbo',
                'last_candle_ts': int(now.timestamp()),
                'dependency_available': True,
                'dependency_reason': None,
                'at_utc': now.isoformat(timespec='seconds'),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )


def _seed_market_db(repo_root: Path, *, asset: str, interval_sec: int) -> Path:
    db_paths = [
        repo_root / 'data' / f'market_{_scope_tag(asset, interval_sec)}.sqlite3',
        repo_root / 'data' / 'market_otc.sqlite3',
    ]
    now_ts = int(datetime.now(UTC).timestamp())
    candles = [
        {
            'from': now_ts - (5 - idx) * interval_sec,
            'open': 1.1,
            'high': 1.2,
            'low': 1.0,
            'close': 1.15,
            'volume': 100.0 + idx,
        }
        for idx in range(5)
    ]
    for db_path in db_paths:
        con = open_db(str(db_path))
        try:
            upsert_candles(con, asset, interval_sec, candles)
        finally:
            con.close()
    ds_path = repo_root / 'data' / 'datasets' / _scope_tag(asset, interval_sec) / 'dataset.csv'
    ds_path.parent.mkdir(parents=True, exist_ok=True)
    ds_path.write_text('ts,feature\n1,0.1\n2,0.2\n', encoding='utf-8')
    return db_paths[0]


def _seed_runtime_dbs(repo_root: Path, *, scope_tag: str) -> None:
    control_repo = RuntimeControlRepository(repo_root / 'runs' / 'runtime_control.sqlite3')
    control_repo.load_breaker('EURUSD-OTC', 300)

    con = sqlite3.connect(str(repo_root / 'runs' / 'runtime_execution.sqlite3'))
    try:
        ensure_execution_db(con)
    finally:
        con.close()

    for signals_db in [
        repo_root / 'runs' / 'live_signals.sqlite3',
        repo_root / 'runs' / 'signals' / scope_tag / 'live_signals.sqlite3',
    ]:
        signals_db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(signals_db))
        try:
            ensure_signals_v2(con)
        finally:
            con.close()

    for state_db in [
        repo_root / 'runs' / 'live_topk_state.sqlite3',
        repo_root / 'runs' / 'state' / scope_tag / 'live_topk_state.sqlite3',
    ]:
        state_db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(state_db))
        try:
            ensure_executed_state_db(con)
        finally:
            con.close()


def test_runtime_artifact_audit_all_scopes_ok(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, multi_asset=True)
    _seed_scope_runtime(tmp_path, asset='EURUSD-OTC', interval_sec=300)
    _seed_scope_runtime(tmp_path, asset='GBPUSD-OTC', interval_sec=300)

    payload = build_runtime_artifact_audit_payload(repo_root=tmp_path, config_path=cfg, all_scopes=True)

    assert payload['ok'] is True
    assert payload['summary']['scope_count'] == 2
    assert read_repo_control_artifact(repo_root=tmp_path, name='runtime_artifact_audit') is not None
    assert read_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='runtime_artifact_audit') is not None


def test_guardrail_audit_flags_drain_and_mode_alignment(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, execution_mode='live', account_mode='REAL', broker_balance_mode='PRACTICE')
    _seed_scope_runtime(tmp_path, asset='EURUSD-OTC', interval_sec=300)
    _seed_market_db(tmp_path, asset='EURUSD-OTC', interval_sec=300)
    _seed_runtime_dbs(tmp_path, scope_tag=_scope_tag('EURUSD-OTC', 300))
    (tmp_path / 'runs' / 'DRAIN_MODE').write_text('1\n', encoding='utf-8')

    payload = build_guardrail_audit_payload(repo_root=tmp_path, config_path=cfg)

    assert payload['ok'] is False
    scope = payload['scope_results'][0]
    checks = {item['name']: item for item in scope['checks']}
    assert checks['drain_mode']['status'] == 'warn'
    assert checks['mode_alignment']['status'] == 'error'
    assert read_repo_control_artifact(repo_root=tmp_path, name='guardrail_audit') is not None


def test_dependency_audit_detects_missing_pysocks_for_socks_transport(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    (tmp_path / '.env').write_text('THALOR_SECRETS_FILE=config/broker_secrets.yaml\n', encoding='utf-8')
    (tmp_path / 'config' / 'broker_secrets.yaml').write_text('broker:\n  email: user@example.com\n  password: x\n', encoding='utf-8')
    secret_dir = tmp_path / 'secrets'
    secret_dir.mkdir(parents=True, exist_ok=True)
    (secret_dir / 'transport_endpoint').write_text('socks5h://user:pass@gate.example.net:7000?name=primary\n', encoding='utf-8')
    (tmp_path / 'requirements.txt').write_text('websocket-client==0.56.0\n', encoding='utf-8')
    (tmp_path / 'requirements-dev.txt').write_text('-r requirements.txt\n', encoding='utf-8')
    (tmp_path / 'requirements-ci.txt').write_text('pytest>=8\n', encoding='utf-8')
    (tmp_path / 'Dockerfile').write_text('FROM python:3.12\nRUN pip install -r requirements.txt\n', encoding='utf-8')

    from natbin.ops import dependency_audit as module

    def _fake_import(name: str):
        if name == 'socks':
            return False, 'ModuleNotFoundError: No module named socks'
        return True, None

    monkeypatch.setattr(module, 'safe_import', _fake_import)
    payload = module.build_dependency_audit_payload(repo_root=tmp_path, config_path=cfg)

    assert payload['ok'] is False
    checks = {item['name']: item for item in payload['scope_results'][0]['checks']}
    assert checks['transport_pysocks_runtime']['status'] == 'error'
    assert checks['requirements_txt']['status'] == 'error'
    assert read_repo_control_artifact(repo_root=tmp_path, name='dependency_audit') is not None


def test_state_db_audit_reports_seeded_runtime_and_market_dbs(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _seed_scope_runtime(tmp_path, asset='EURUSD-OTC', interval_sec=300)
    _seed_market_db(tmp_path, asset='EURUSD-OTC', interval_sec=300)
    _seed_runtime_dbs(tmp_path, scope_tag=_scope_tag('EURUSD-OTC', 300))

    payload = build_state_db_audit_payload(repo_root=tmp_path, config_path=cfg)

    assert payload['ok'] is True
    scope = payload['scope_results'][0]
    assert scope['databases']['market_data']['candles_scope'] == 5
    assert scope['databases']['runtime_control']['quick_check'] == 'ok'
    assert read_repo_control_artifact(repo_root=tmp_path, name='state_db_audit') is not None


@pytest.mark.parametrize(
    ('command', 'attr_name'),
    [
        ('runtime-artifact-audit', 'runtime_artifact_audit_payload'),
        ('guardrail-audit', 'guardrail_audit_payload'),
        ('dependency-audit', 'dependency_audit_payload'),
        ('state-db-audit', 'state_db_audit_payload'),
    ],
)
def test_control_app_piece3_commands_return_zero(monkeypatch, command: str, attr_name: str) -> None:
    monkeypatch.setattr(control_app, attr_name, lambda **kwargs: {'ok': True, 'severity': 'ok'})
    rc = control_app.main([command, '--json'])
    assert rc == 0
