from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.control.plan import build_context
from natbin.ops.practice_readiness import build_practice_readiness_payload
from natbin.state.control_repo import write_control_artifact


SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_dataset(path: Path, rows: int = 180) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fh:
        fh.write('ts,feature_a\n')
        base_ts = 1773300000
        for idx in range(rows):
            fh.write(f'{base_ts + idx * 300},{1.0 + idx / 1000.0}\n')


def _seed_practice_repo(repo: Path) -> Path:
    cfg = repo / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: live_controlled_practice',
                '  startup_invalidate_stale_artifacts: true',
                '  lock_refresh_enable: true',
                'broker:',
                '  provider: iqoption',
                '  balance_mode: PRACTICE',
                'security:',
                '  deployment_profile: live',
                '  live_require_external_credentials: true',
                '  secrets_file: secrets/bundle.yaml',
                '  guard:',
                '    enabled: true',
                '    live_only: true',
                '    time_filter_enable: true',
                'notifications:',
                '  enabled: true',
                '  telegram:',
                '    enabled: false',
                '    send_enabled: false',
                'multi_asset:',
                '  enabled: false',
                '  max_parallel_assets: 1',
                '  portfolio_topk_total: 1',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: PRACTICE',
                '  stake:',
                '    amount: 2.0',
                '    currency: BRL',
                '  limits:',
                '    max_pending_unknown: 1',
                '    max_open_positions: 1',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    bundle = repo / 'secrets' / 'bundle.yaml'
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
    ctx = build_context(repo_root=repo, config_path=cfg, dump_snapshot=False)
    _write_dataset(repo / 'data' / 'dataset_phase2.csv', rows=180)
    market_path = Path(ctx.scoped_paths['market_context'])
    market_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat(timespec='seconds')
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
                'at_utc': now,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    fresh = {'at_utc': now, 'state': 'healthy'}
    write_control_artifact(repo_root=repo, asset='EURUSD-OTC', interval_sec=300, name='loop_status', payload=fresh)
    write_control_artifact(repo_root=repo, asset='EURUSD-OTC', interval_sec=300, name='health', payload=fresh)
    intel_dir = repo / 'runs' / 'intelligence' / SCOPE_TAG
    intel_dir.mkdir(parents=True, exist_ok=True)
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

    soak_dir = repo / 'runs' / 'soak'
    soak_dir.mkdir(parents=True, exist_ok=True)
    (soak_dir / f'soak_latest_{SCOPE_TAG}.json').write_text(
        json.dumps(
            {
                'at_utc': now,
                'phase': 'runtime_soak',
                'exit_code': 0,
                'config_path': str(cfg),
                'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': SCOPE_TAG},
                'cycles_requested': 2,
                'cycles_completed': 2,
                'freshness': {'scope_tag': SCOPE_TAG, 'stale_artifacts': [], 'artifacts': []},
                'guard': {'stale_artifacts': []},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    return cfg


def test_build_practice_readiness_payload_marks_scope_ready(tmp_path: Path, monkeypatch) -> None:
    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})
    cfg = _seed_practice_repo(tmp_path)
    payload = build_practice_readiness_payload(repo_root=tmp_path, config_path=cfg)
    assert payload['kind'] == 'practice_readiness'
    assert payload['severity'] == 'ok'
    assert payload['ready_for_practice'] is True
    assert payload['doctor']['ready_for_practice'] is True
    assert payload['execution']['account_mode'] == 'PRACTICE'
    assert payload['broker']['balance_mode'] == 'PRACTICE'
    assert payload['soak']['status'] == 'ok'
    names = {item['name'] for item in payload['checks']}
    assert 'runtime_soak' in names
    assert 'controlled_scope' in names
    artifact = tmp_path / 'runs' / 'control' / SCOPE_TAG / 'practice.json'
    assert artifact.exists()
