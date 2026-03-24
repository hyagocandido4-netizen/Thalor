from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.control import app as control_app
from natbin.control.plan import build_context
from natbin.ops.practice_bootstrap import build_practice_bootstrap_payload
from natbin.state.control_repo import write_control_artifact


SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_dataset(path: Path, rows: int = 180) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fh:
        fh.write('ts,feature_a\n')
        base_ts = 1773300000
        for idx in range(rows):
            fh.write(f'{base_ts + idx * 300},{1.0 + idx / 1000.0}\n')


def _write_market_context(path: Path, *, now: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
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
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )


def _write_intelligence(repo: Path, *, now: str) -> None:
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


def _write_soak(repo: Path, *, now: str, cfg: Path) -> dict[str, object]:
    soak_dir = repo / 'runs' / 'soak'
    soak_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        'at_utc': now,
        'phase': 'runtime_soak',
        'exit_code': 0,
        'config_path': str(cfg),
        'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': SCOPE_TAG},
        'cycles_requested': 2,
        'cycles_completed': 2,
        'freshness': {'scope_tag': SCOPE_TAG, 'stale_artifacts': [], 'artifacts': []},
        'guard': {'stale_artifacts': []},
    }
    (soak_dir / f'soak_latest_{SCOPE_TAG}.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    return payload


def _seed_clean_repo(repo: Path, *, account_mode: str = 'PRACTICE', balance_mode: str = 'PRACTICE') -> tuple[Path, object]:
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
                f'  balance_mode: {balance_mode}',
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
                f'  account_mode: {account_mode}',
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
                f'  balance_mode: {balance_mode}',
                '',
            ]
        ),
        encoding='utf-8',
    )
    ctx = build_context(repo_root=repo, config_path=cfg, dump_snapshot=False)
    return cfg, ctx


def test_practice_bootstrap_builds_clean_ready_flow(tmp_path: Path, monkeypatch) -> None:
    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})
    cfg, ctx = _seed_clean_repo(tmp_path)

    def fake_asset_prepare(**kwargs):
        now = datetime.now(UTC).isoformat(timespec='seconds')
        _write_dataset(tmp_path / 'data' / 'dataset_phase2.csv', rows=180)
        _write_market_context(Path(ctx.scoped_paths['market_context']), now=now)
        return {
            'phase': 'asset_prepare',
            'ok': True,
            'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300},
            'steps': [
                {'name': 'collect_recent', 'returncode': 0},
                {'name': 'make_dataset', 'returncode': 0},
                {'name': 'refresh_market_context', 'returncode': 0},
            ],
        }

    def fake_refresh(**kwargs):
        now = datetime.now(UTC).isoformat(timespec='seconds')
        _write_intelligence(tmp_path, now=now)
        return {'ok': True, 'items': [], 'materialized_portfolio': {'ok': True}}

    def fake_soak(**kwargs):
        now = datetime.now(UTC).isoformat(timespec='seconds')
        fresh = {'at_utc': now, 'state': 'healthy'}
        write_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='loop_status', payload=fresh)
        write_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='health', payload=fresh)
        _write_soak(tmp_path, now=now, cfg=cfg)
        return {
            'at_utc': now,
            'phase': 'runtime_soak',
            'exit_code': 0,
            'config_path': str(cfg),
            'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': SCOPE_TAG},
            'cycles_requested': 2,
            'cycles_completed': 2,
            'freshness': {'scope_tag': SCOPE_TAG, 'stale_artifacts': [], 'artifacts': []},
            'guard': {'stale_artifacts': []},
        }

    monkeypatch.setattr('natbin.ops.practice_bootstrap._run_asset_prepare', fake_asset_prepare)
    monkeypatch.setattr('natbin.ops.practice_bootstrap.refresh_config_intelligence', lambda **kwargs: fake_refresh(**kwargs))
    monkeypatch.setattr('natbin.ops.practice_bootstrap.build_runtime_soak_summary', lambda **kwargs: fake_soak(**kwargs))

    payload = build_practice_bootstrap_payload(repo_root=tmp_path, config_path=cfg, soak_cycles=2)
    assert payload['kind'] == 'practice_bootstrap'
    assert payload['severity'] == 'ok'
    assert payload['round_eligible'] is True
    assert payload['ready_for_practice_green'] is True
    assert payload['asset_prepare']['action'] == 'ran'
    assert payload['soak']['action'] == 'ran'
    assert payload['post_practice']['payload']['ready_for_practice'] is True
    assert payload['post_practice']['payload']['doctor']['ready_for_practice'] is True
    assert Path(payload['artifacts']['control_path']).exists()
    assert Path(payload['artifacts']['report_path']).exists()
    assert Path(payload['artifacts']['latest_report_path']).exists()


def test_practice_bootstrap_blocks_on_critical_profile_issue(tmp_path: Path, monkeypatch) -> None:
    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})
    cfg, _ctx = _seed_clean_repo(tmp_path, account_mode='REAL', balance_mode='REAL')

    def fail_if_called(**kwargs):  # pragma: no cover - defensive
        raise AssertionError('bootstrap should stop before trying to prepare or soak')

    monkeypatch.setattr('natbin.ops.practice_bootstrap._run_asset_prepare', fail_if_called)
    monkeypatch.setattr('natbin.ops.practice_bootstrap.build_runtime_soak_summary', fail_if_called)

    payload = build_practice_bootstrap_payload(repo_root=tmp_path, config_path=cfg, soak_cycles=2)
    assert payload['severity'] == 'error'
    assert payload['round_eligible'] is False
    assert payload['blocked_reason'] == 'critical_preflight_blockers'
    critical_names = {item['name'] for item in payload['pre_practice']['critical_issues']}
    assert 'execution_account_mode' in critical_names
    assert 'broker_balance_mode' in critical_names
    assert payload['asset_prepare']['action'] == 'skipped'
    assert payload['soak']['action'] == 'skipped'


def test_control_app_practice_bootstrap_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        control_app,
        'practice_bootstrap_payload',
        lambda **kwargs: {
            'ok': True,
            'severity': 'ok',
            'round_eligible': True,
            'ready_for_practice_green': True,
        },
    )
    assert control_app.main(['practice-bootstrap', '--json']) == 0

    monkeypatch.setattr(
        control_app,
        'practice_bootstrap_payload',
        lambda **kwargs: {
            'ok': False,
            'severity': 'error',
            'round_eligible': False,
            'ready_for_practice_green': False,
        },
    )
    assert control_app.main(['practice-bootstrap', '--json']) == 2
