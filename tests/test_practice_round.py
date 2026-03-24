from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.control.plan import build_context
from natbin.ops.live_validation import ValidationResult
from natbin.ops.practice_round import build_practice_round_payload
from natbin.state.control_repo import write_control_artifact


SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_dataset(path: Path, rows: int = 180) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fh:
        fh.write('ts,feature_a\n')
        base_ts = 1773300000
        for idx in range(rows):
            fh.write(f'{base_ts + idx * 300},{1.0 + idx / 1000.0}\n')


def _seed_repo(repo: Path, *, account_mode: str = 'PRACTICE', balance_mode: str = 'PRACTICE') -> Path:
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
    return cfg


def _write_soak(repo: Path, cfg: Path, *, cycles_completed: int = 2) -> dict[str, object]:
    now = datetime.now(UTC).isoformat(timespec='seconds')
    soak_dir = repo / 'runs' / 'soak'
    soak_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        'at_utc': now,
        'phase': 'runtime_soak',
        'exit_code': 0,
        'config_path': str(cfg),
        'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': SCOPE_TAG},
        'cycles_requested': cycles_completed,
        'cycles_completed': cycles_completed,
        'freshness': {'scope_tag': SCOPE_TAG, 'stale_artifacts': [], 'artifacts': []},
        'guard': {'stale_artifacts': []},
    }
    (soak_dir / f'soak_latest_{SCOPE_TAG}.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    return payload


def _fake_validation_result(name: str, *, payload: dict[str, object] | None = None, required: bool = True, note: str | None = None, potentially_submits: bool = False) -> ValidationResult:
    body = json.dumps(payload or {'ok': True}, ensure_ascii=False)
    now = datetime.now(UTC).isoformat(timespec='seconds')
    return ValidationResult(
        name=name,
        returncode=0,
        duration_sec=0.01,
        started_at_utc=now,
        finished_at_utc=now,
        cmd=['python', name],
        required=required,
        note=note,
        potentially_submits=potentially_submits,
        stdout=body,
        stderr='',
        payload=payload or {'ok': True},
    )


def test_practice_round_runs_soak_then_validation(tmp_path: Path, monkeypatch) -> None:
    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})
    cfg = _seed_repo(tmp_path)
    monkeypatch.setattr('natbin.ops.practice_round.refresh_config_intelligence', lambda **kwargs: {'ok': True, 'items': [], 'materialized_portfolio': {'ok': True}})
    monkeypatch.setattr('natbin.ops.practice_bootstrap.refresh_config_intelligence', lambda **kwargs: {'ok': True, 'items': [], 'materialized_portfolio': {'ok': True}})

    def fake_soak(**kwargs):
        return _write_soak(tmp_path, cfg, cycles_completed=3)

    monkeypatch.setattr('natbin.ops.practice_bootstrap.build_runtime_soak_summary', fake_soak)

    def fake_run_validation_step(_python_exe, spec, _repo_root, _env):
        if spec.name == 'observe_once_practice_live':
            return _fake_validation_result(
                spec.name,
                payload={
                    'enabled': True,
                    'intent_created': True,
                    'latest_intent': {
                        'intent_id': 'intent-1',
                        'intent_state': 'accepted_open',
                        'account_mode': 'PRACTICE',
                    },
                    'submit_attempt': {
                        'attempt_no': 1,
                        'transport_status': 'ack',
                        'external_order_id': 'ord-1',
                    },
                    'execution_summary': {
                        'asset': 'EURUSD-OTC',
                        'interval_sec': 300,
                        'day': '2026-03-21',
                        'consuming_today': 1,
                        'pending_unknown': 0,
                        'open_positions': 1,
                        'recent_states': {'accepted_open': 1},
                    },
                    'blocked_reason': None,
                },
                required=spec.required,
                note=spec.note,
                potentially_submits=spec.potentially_submits,
            )
        if spec.name == 'orders_after_practice':
            return _fake_validation_result(
                spec.name,
                payload={
                    'enabled': True,
                    'scope_tag': SCOPE_TAG,
                    'summary': {'consuming_today': 1, 'pending_unknown': 0, 'open_positions': 1},
                    'recent_intents': [{'intent_id': 'intent-1', 'intent_state': 'accepted_open'}],
                },
                required=spec.required,
                note=spec.note,
                potentially_submits=spec.potentially_submits,
            )
        if spec.name == 'reconcile_after_practice':
            return _fake_validation_result(
                spec.name,
                payload={'enabled': True, 'scope_tag': SCOPE_TAG, 'summary': {'ok': True}},
                required=spec.required,
                note=spec.note,
                potentially_submits=spec.potentially_submits,
            )
        if spec.name == 'incidents_after_practice':
            return _fake_validation_result(
                spec.name,
                payload={'ok': True, 'severity': 'ok', 'open_issues': []},
                required=spec.required,
                note=spec.note,
                potentially_submits=spec.potentially_submits,
            )
        return _fake_validation_result(spec.name, required=spec.required, note=spec.note, potentially_submits=spec.potentially_submits)

    monkeypatch.setattr('natbin.ops.practice_round.run_validation_step', fake_run_validation_step)
    monkeypatch.setattr(
        'natbin.ops.practice_round.incident_report_payload',
        lambda **kwargs: {'ok': True, 'severity': 'ok', 'artifacts': {'report_path': str(tmp_path / 'runs' / 'incidents' / 'reports' / 'incident_report.json')}, 'recommended_actions': []},
    )

    payload = build_practice_round_payload(repo_root=tmp_path, config_path=cfg, soak_cycles=3)
    assert payload['kind'] == 'practice_round'
    assert payload['severity'] == 'ok'
    assert payload['round_ok'] is True
    assert payload['soak']['action'] == 'ran'
    assert payload['validation']['required_passed'] is True
    assert payload['validation']['observe']['latest_intent_state'] == 'accepted_open'
    assert Path(payload['artifacts']['report_path']).exists()
    assert Path(payload['artifacts']['validation_report_path']).exists()
    control_artifact = tmp_path / 'runs' / 'control' / SCOPE_TAG / 'practice_round.json'
    assert control_artifact.exists()


def test_practice_round_blocks_on_critical_ready1_issue(tmp_path: Path, monkeypatch) -> None:
    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})
    cfg = _seed_repo(tmp_path, account_mode='REAL', balance_mode='REAL')
    monkeypatch.setattr('natbin.ops.practice_round.refresh_config_intelligence', lambda **kwargs: {'ok': True, 'items': [], 'materialized_portfolio': {'ok': True}})
    monkeypatch.setattr('natbin.ops.practice_bootstrap.refresh_config_intelligence', lambda **kwargs: {'ok': True, 'items': [], 'materialized_portfolio': {'ok': True}})

    def fail_if_called(**kwargs):  # pragma: no cover - defensive
        raise AssertionError('soak should not run when practice config is critically invalid')

    monkeypatch.setattr('natbin.ops.practice_bootstrap.build_runtime_soak_summary', fail_if_called)

    payload = build_practice_round_payload(repo_root=tmp_path, config_path=cfg, soak_cycles=3)
    assert payload['round_ok'] is False
    assert payload['severity'] == 'error'
    assert payload['blocked_reason'] == 'critical_preflight_blockers'
    assert payload['soak']['action'] == 'skipped'
    assert payload['validation']['required_passed'] is False
    critical_names = {item['name'] for item in payload['pre_practice']['critical_issues']}
    assert 'execution_account_mode' in critical_names
    assert 'broker_balance_mode' in critical_names


def test_practice_round_allows_warn_only_intelligence_after_soak(tmp_path: Path, monkeypatch) -> None:
    from natbin.brokers.iqoption import IQOptionAdapter

    monkeypatch.setattr(IQOptionAdapter, '_dependency_status', lambda self: {'available': True, 'reason': None})
    cfg = _seed_repo(tmp_path)
    monkeypatch.setattr('natbin.ops.practice_round.refresh_config_intelligence', lambda **kwargs: {'ok': True, 'items': [], 'materialized_portfolio': {'ok': True}})
    monkeypatch.setattr('natbin.ops.practice_bootstrap.refresh_config_intelligence', lambda **kwargs: {'ok': True, 'items': [], 'materialized_portfolio': {'ok': True}})

    intel_dir = tmp_path / 'runs' / 'intelligence' / SCOPE_TAG
    now = datetime.now(UTC).isoformat(timespec='seconds')
    (intel_dir / 'latest_eval.json').write_text(
        json.dumps(
            {
                'kind': 'intelligence_eval',
                'evaluated_at_utc': now,
                'allow_trade': True,
                'intelligence_score': -1.18,
                'portfolio_score': -1.32,
                'drift': {'level': 'block'},
                'portfolio_feedback': {
                    'allocator_blocked': True,
                    'block_reason': 'regime_block',
                    'portfolio_score': -1.32,
                },
                'retrain_orchestration': {'state': 'queued', 'priority': 'high'},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    (intel_dir / 'retrain_plan.json').write_text(
        json.dumps({'kind': 'retrain_plan', 'at_utc': now, 'state': 'queued', 'priority': 'high'}, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )
    (intel_dir / 'retrain_status.json').write_text(
        json.dumps({'kind': 'retrain_status', 'updated_at_utc': now, 'state': 'queued', 'priority': 'high'}, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )

    def fake_soak(**kwargs):
        return _write_soak(tmp_path, cfg, cycles_completed=1)

    monkeypatch.setattr('natbin.ops.practice_bootstrap.build_runtime_soak_summary', fake_soak)

    def fake_run_validation_step(_python_exe, spec, _repo_root, _env):
        if spec.name == 'observe_once_practice_live':
            return _fake_validation_result(
                spec.name,
                payload={
                    'enabled': True,
                    'intent_created': False,
                    'blocked_reason': 'regime_block',
                    'execution_summary': {
                        'asset': 'EURUSD-OTC',
                        'interval_sec': 300,
                        'consuming_today': 0,
                        'pending_unknown': 0,
                        'open_positions': 0,
                    },
                },
                required=spec.required,
                note=spec.note,
                potentially_submits=spec.potentially_submits,
            )
        if spec.name == 'orders_after_practice':
            return _fake_validation_result(
                spec.name,
                payload={
                    'enabled': True,
                    'scope_tag': SCOPE_TAG,
                    'summary': {'consuming_today': 0, 'pending_unknown': 0, 'open_positions': 0},
                    'recent_intents': [],
                },
                required=spec.required,
                note=spec.note,
                potentially_submits=spec.potentially_submits,
            )
        if spec.name == 'reconcile_after_practice':
            return _fake_validation_result(
                spec.name,
                payload={'enabled': True, 'scope_tag': SCOPE_TAG, 'summary': {'ok': True}},
                required=spec.required,
                note=spec.note,
                potentially_submits=spec.potentially_submits,
            )
        if spec.name == 'incidents_after_practice':
            return _fake_validation_result(
                spec.name,
                payload={'ok': True, 'severity': 'ok', 'open_issues': []},
                required=spec.required,
                note=spec.note,
                potentially_submits=spec.potentially_submits,
            )
        return _fake_validation_result(spec.name, required=spec.required, note=spec.note, potentially_submits=spec.potentially_submits)

    monkeypatch.setattr('natbin.ops.practice_round.run_validation_step', fake_run_validation_step)
    monkeypatch.setattr(
        'natbin.ops.practice_round.incident_report_payload',
        lambda **kwargs: {'ok': True, 'severity': 'ok', 'artifacts': {'report_path': str(tmp_path / 'runs' / 'incidents' / 'reports' / 'incident_report.json')}, 'recommended_actions': []},
    )

    payload = build_practice_round_payload(repo_root=tmp_path, config_path=cfg, soak_cycles=1)
    assert payload['blocked_reason'] is None
    assert payload['severity'] == 'warn'
    assert payload['ok'] is True
    assert payload['round_ok'] is True
    assert payload['soak']['action'] == 'ran'
    assert payload['validation']['required_passed'] is True
    assert payload['validation']['observe']['intent_created'] is False
    assert payload['validation']['observe']['blocked_reason'] == 'regime_block'
    assert payload['post_practice']['ready_for_practice'] is False
    assert payload['post_practice']['round_eligible'] is True
    assert 'retrain recomendado' in (payload['recommended_next_steps'][0]).lower()
