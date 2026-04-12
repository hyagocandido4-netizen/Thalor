from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from natbin.incidents.reporting import incident_status_payload
from natbin.intelligence.fit import fit_intelligence_pack
from natbin.ops.live_validation import ValidationResult
from natbin.ops.practice_round import _observe_summary
from natbin.portfolio.paths import resolve_scope_runtime_paths
from natbin.runtime.hardening import RuntimeHardeningReport

ASSET = 'EURUSD-OTC'
INTERVAL = 300
SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'live_controlled_practice.yaml'
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
                'intelligence:',
                '  enabled: true',
                '  artifact_dir: runs/intelligence',
                '  learned_gating_enable: true',
                '  learned_gating_min_rows: 50',
                '  anti_overfit_enable: true',
                '  anti_overfit_min_windows: 3',
                'assets:',
                f'  - asset: {ASSET}',
                f'    interval_sec: {INTERVAL}',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def _write_dataset(path: Path, ts_values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{'ts': ts, 'y_open_close': 1.0 if idx % 2 == 0 else 0.0} for idx, ts in enumerate(ts_values)]
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_signals_db(path: Path, ts_values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute('CREATE TABLE signals_v2 (ts INTEGER, asset TEXT, interval_sec INTEGER, action TEXT, proba_up REAL, conf REAL, score REAL, ev REAL, payout REAL, reason TEXT, executed_today INTEGER)')
        for idx, ts in enumerate(ts_values[:8]):
            action = 'CALL' if idx % 2 == 0 else 'PUT'
            proba = 0.82 if action == 'CALL' else 0.18
            con.execute('INSERT INTO signals_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?)', (ts, ASSET, INTERVAL, action, proba, 0.74, 0.61, 0.12 if action == 'CALL' else -0.03, 0.8, 'topk_emit', idx % 3))
        for idx, ts in enumerate(ts_values[8:185], start=8):
            aligned = 0.81 if idx % 2 == 0 else 0.19
            proba = (1.0 - aligned) if idx % 11 == 0 else aligned
            con.execute('INSERT INTO signals_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?)', (ts, ASSET, INTERVAL, 'HOLD', proba, 0.55, 0.0, 0.0, 0.8, 'regime_block', idx % 3))
        con.commit()
    finally:
        con.close()


def _fake_validation_result(name: str, payload: dict[str, object]) -> ValidationResult:
    now = datetime.now(tz=UTC).isoformat(timespec='seconds')
    return ValidationResult(
        name=name,
        returncode=0,
        duration_sec=0.01,
        started_at_utc=now,
        finished_at_utc=now,
        cmd=['python', name],
        required=True,
        note=name,
        potentially_submits=False,
        stdout=json.dumps(payload),
        stderr='',
        payload=payload,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_int_harden1_') as td:
        root = Path(td)
        cfg = _write_config(root)
        ts_values = [1773000000 + (i * INTERVAL) for i in range(185)]
        _write_dataset(root / 'data' / 'dataset_phase2.csv', ts_values)
        signals_db = resolve_scope_runtime_paths(root, scope_tag=SCOPE_TAG, partition_enable=False).signals_db_path
        _write_signals_db(signals_db, ts_values)

        pack, _out = fit_intelligence_pack(repo_root=root, config_path=cfg, asset=ASSET, interval_sec=INTERVAL, lookback_days=5)
        anti = dict(pack.get('anti_overfit') or {})
        anti_src = dict((pack.get('metadata') or {}).get('anti_overfit_source') or {})
        assert anti.get('available') is True, pack
        assert anti_src.get('kind') in {'signals_eval_fallback', 'training_rows_fallback'}, anti_src
        assert Path(str(anti_src.get('materialized_path'))).exists(), anti_src

        observe = _observe_summary([
            _fake_validation_result('observe_once_practice_live', {'enabled': True, 'intent_created': False, 'blocked_reason': 'regime_block'}),
            _fake_validation_result('orders_after_practice', {'enabled': True, 'summary': {'consuming_today': 0, 'pending_unknown': 0, 'open_positions': 0}, 'recent_intents': []}),
            _fake_validation_result('reconcile_after_practice', {'enabled': True, 'scope_tag': SCOPE_TAG, 'summary': {'scope_tag': SCOPE_TAG, 'pending_before': 0, 'updated_intents': 0, 'new_orphans': 0, 'ambiguous_matches': 0, 'terminalized': 0, 'errors': []}, 'detail': {'scope_tag': SCOPE_TAG, 'pending_before': 0, 'pending_after': 0, 'errors': [], 'skipped_broker_scan': True, 'reason': 'no_pending_intents'}}),
            _fake_validation_result('incidents_after_practice', {'ok': True, 'severity': 'ok', 'open_issues': []}),
        ])
        assert observe['reconcile_ok'] is True, observe

        from unittest.mock import patch

        with patch('natbin.incidents.reporting.build_release_readiness_payload', return_value={'severity': 'warn', 'ready_for_live': False, 'execution_live': True}), \
             patch('natbin.incidents.reporting.alerts_status_payload', return_value={'telegram': {'enabled': False, 'send_enabled': False, 'credentials_present': False, 'recent_counts': {}, 'recent': []}}), \
             patch('natbin.incidents.reporting.gate_status', return_value={'kill_switch': {'active': False}, 'drain_mode': {'active': False}}), \
             patch('natbin.incidents.reporting.audit_security_posture', return_value={'blocked': False, 'severity': 'ok', 'credential_source': 'external'}), \
             patch('natbin.incidents.reporting.build_intelligence_surface_payload', return_value={'enabled': True, 'severity': 'ok', 'warnings': [], 'summary': {'retrain_state': 'idle', 'retrain_priority': 'low', 'portfolio_feedback_blocked': False}, 'allocation': {}, 'execution': {'missing_fields': []}}), \
             patch('natbin.incidents.reporting.inspect_runtime_freshness', return_value=RuntimeHardeningReport(scope_tag=SCOPE_TAG, checked_at_utc=datetime.now(tz=UTC).isoformat(timespec='seconds'), stale_after_sec=900, lock={}, artifacts=[], stale_artifacts=[], actions=[], mode='inspect')), \
             patch('natbin.incidents.reporting._health_summary', return_value={'state': 'healthy', 'message': 'cycle_ok'}), \
             patch('natbin.incidents.reporting._loop_summary', return_value={'phase': 'cycle', 'message': 'cycle_ok'}), \
             patch('natbin.incidents.reporting.load_recent_scope_incidents', return_value=[]), \
             patch('natbin.incidents.reporting._summarize_incidents', return_value={'count': 0, 'by_type': {}, 'by_severity': {}, 'latest': None}):
            practice_payload = incident_status_payload(repo_root=root, config_path=cfg, stage='practice', write_artifact=False)
            default_payload = incident_status_payload(repo_root=root, config_path=cfg, stage=None, write_artifact=False)
        assert practice_payload['severity'] == 'ok', practice_payload
        assert default_payload['severity'] == 'warn', default_payload

    print('int_harden_1_smoke: OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
