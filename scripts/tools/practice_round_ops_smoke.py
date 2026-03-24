from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.brokers.iqoption import IQOptionAdapter
from natbin.control.plan import build_context
from natbin.ops.live_validation import ValidationResult
from natbin.ops import practice_round as practice_round_mod
from natbin.state.control_repo import write_control_artifact


SCOPE_TAG = 'EURUSD-OTC_300s'


def _seed_repo(repo: Path) -> Path:
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
    bundle.write_text('broker:\n  email: trader@example.com\n  password: trader-secret\n  balance_mode: PRACTICE\n', encoding='utf-8')
    data_dir = repo / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    dataset = data_dir / 'dataset_phase2.csv'
    with dataset.open('w', encoding='utf-8') as fh:
        fh.write('ts,feature_a\n')
        base_ts = 1773300000
        for idx in range(180):
            fh.write(f'{base_ts + idx * 300},{1.0 + idx / 1000.0}\n')
    ctx = build_context(repo_root=repo, config_path=cfg, dump_snapshot=False)
    now = datetime.now(UTC).isoformat(timespec='seconds')
    market_path = Path(ctx.scoped_paths['market_context'])
    market_path.parent.mkdir(parents=True, exist_ok=True)
    market_path.write_text(json.dumps({'asset': 'EURUSD-OTC', 'interval_sec': 300, 'market_open': True, 'payout': 0.85, 'at_utc': now}, indent=2), encoding='utf-8')
    fresh = {'at_utc': now, 'state': 'healthy'}
    write_control_artifact(repo_root=repo, asset='EURUSD-OTC', interval_sec=300, name='loop_status', payload=fresh)
    write_control_artifact(repo_root=repo, asset='EURUSD-OTC', interval_sec=300, name='health', payload=fresh)
    intel_dir = repo / 'runs' / 'intelligence' / SCOPE_TAG
    intel_dir.mkdir(parents=True, exist_ok=True)
    (intel_dir / 'pack.json').write_text(json.dumps({'kind': 'intelligence_pack', 'generated_at_utc': now, 'metadata': {'training_rows': 120}}, indent=2), encoding='utf-8')
    (intel_dir / 'latest_eval.json').write_text(json.dumps({'kind': 'intelligence_eval', 'evaluated_at_utc': now, 'allow_trade': True, 'intelligence_score': 0.7, 'portfolio_score': 0.73, 'portfolio_feedback': {'allocator_blocked': False, 'portfolio_score': 0.73}, 'retrain_orchestration': {'state': 'idle', 'priority': 'low'}}, indent=2), encoding='utf-8')
    (intel_dir / 'retrain_plan.json').write_text(json.dumps({'kind': 'retrain_plan', 'at_utc': now, 'state': 'idle', 'priority': 'low'}, indent=2), encoding='utf-8')
    (intel_dir / 'retrain_status.json').write_text(json.dumps({'kind': 'retrain_status', 'updated_at_utc': now, 'state': 'idle', 'priority': 'low'}, indent=2), encoding='utf-8')
    return cfg


def _fake_result(spec_name: str, required: bool, note: str | None, potentially_submits: bool, payload: dict[str, object] | None = None) -> ValidationResult:
    now = datetime.now(UTC).isoformat(timespec='seconds')
    body = json.dumps(payload or {'ok': True}, ensure_ascii=False)
    return ValidationResult(
        name=spec_name,
        returncode=0,
        duration_sec=0.01,
        started_at_utc=now,
        finished_at_utc=now,
        cmd=['python', spec_name],
        required=required,
        note=note,
        potentially_submits=potentially_submits,
        stdout=body,
        stderr='',
        payload=payload or {'ok': True},
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_practice_round_') as td:
        repo = Path(td)
        cfg = _seed_repo(repo)

        IQOptionAdapter._dependency_status = lambda self: {'available': True, 'reason': None}

        def fake_soak(**kwargs):
            now = datetime.now(UTC).isoformat(timespec='seconds')
            soak_dir = repo / 'runs' / 'soak'
            soak_dir.mkdir(parents=True, exist_ok=True)
            payload = {
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
            (soak_dir / f'soak_latest_{SCOPE_TAG}.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
            return payload

        def fake_run_validation_step(_python_exe, spec, _repo_root, _env):
            if spec.name == 'observe_once_practice_live':
                return _fake_result(spec.name, spec.required, spec.note, spec.potentially_submits, {
                    'enabled': True,
                    'intent_created': True,
                    'latest_intent': {'intent_id': 'intent-1', 'intent_state': 'accepted_open'},
                    'submit_attempt': {'transport_status': 'ack', 'external_order_id': 'ord-1'},
                    'execution_summary': {'consuming_today': 1, 'pending_unknown': 0, 'open_positions': 1},
                })
            if spec.name == 'orders_after_practice':
                return _fake_result(spec.name, spec.required, spec.note, spec.potentially_submits, {
                    'enabled': True,
                    'summary': {'consuming_today': 1, 'pending_unknown': 0, 'open_positions': 1},
                    'recent_intents': [{'intent_id': 'intent-1', 'intent_state': 'accepted_open'}],
                })
            if spec.name == 'reconcile_after_practice':
                return _fake_result(spec.name, spec.required, spec.note, spec.potentially_submits, {'enabled': True, 'summary': {'ok': True}})
            if spec.name == 'incidents_after_practice':
                return _fake_result(spec.name, spec.required, spec.note, spec.potentially_submits, {'ok': True, 'severity': 'ok'})
            return _fake_result(spec.name, spec.required, spec.note, spec.potentially_submits)

        practice_round_mod.build_runtime_soak_summary = fake_soak  # type: ignore[assignment]
        practice_round_mod.run_validation_step = fake_run_validation_step  # type: ignore[assignment]
        practice_round_mod.incident_report_payload = lambda **kwargs: {'ok': True, 'severity': 'ok', 'artifacts': {}, 'recommended_actions': []}  # type: ignore[assignment]

        payload = practice_round_mod.build_practice_round_payload(repo_root=repo, config_path=cfg, soak_cycles=2)
        assert payload['round_ok'] is True, payload
        assert payload['soak']['action'] == 'ran', payload
        assert Path(payload['artifacts']['report_path']).exists(), payload
        print('practice_round_ops_smoke: OK')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
