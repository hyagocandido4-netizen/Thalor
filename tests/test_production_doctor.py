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
    return cfg


def test_production_doctor_ok_for_local_ready_repo(tmp_path: Path) -> None:
    cfg = _seed_runtime_surface(tmp_path, execution_live=False)
    payload = build_production_doctor_payload(repo_root=tmp_path, config_path=cfg)
    assert payload['kind'] == 'production_doctor'
    assert payload['severity'] == 'ok'
    assert payload['ready_for_cycle'] is True
    names = {item['name']: item for item in payload['checks']}
    assert names['dataset_ready']['status'] == 'ok'
    assert names['market_context']['status'] == 'ok'
    assert names['effective_config_artifacts']['status'] == 'ok'
    artifact = tmp_path / 'runs' / 'control' / SCOPE_TAG / 'doctor.json'
    assert artifact.exists()


def test_production_doctor_flags_live_broker_missing_credentials(tmp_path: Path, monkeypatch) -> None:
    cfg = _seed_runtime_surface(tmp_path, execution_live=True)

    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})
    payload = build_production_doctor_payload(repo_root=tmp_path, config_path=cfg, probe_broker=False)
    assert payload['severity'] == 'error'
    assert 'broker_preflight' in payload['blockers']
    broker = next(item for item in payload['checks'] if item['name'] == 'broker_preflight')
    assert broker['reason'] == 'iqoption_missing_credentials'
