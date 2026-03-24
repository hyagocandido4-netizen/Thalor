from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
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


class _DummyFreshness(RuntimeHardeningReport):
    pass


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
        con.execute(
            'CREATE TABLE signals_v2 (ts INTEGER, asset TEXT, interval_sec INTEGER, action TEXT, proba_up REAL, conf REAL, score REAL, ev REAL, payout REAL, reason TEXT, executed_today INTEGER)'
        )
        for idx, ts in enumerate(ts_values[:8]):
            action = 'CALL' if idx % 2 == 0 else 'PUT'
            proba = 0.82 if action == 'CALL' else 0.18
            con.execute(
                'INSERT INTO signals_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (ts, ASSET, INTERVAL, action, proba, 0.74, 0.61, 0.12 if action == 'CALL' else -0.03, 0.8, 'topk_emit', idx % 3),
            )
        for idx, ts in enumerate(ts_values[8:185], start=8):
            aligned = 0.81 if idx % 2 == 0 else 0.19
            proba = (1.0 - aligned) if idx % 11 == 0 else aligned
            con.execute(
                'INSERT INTO signals_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (ts, ASSET, INTERVAL, 'HOLD', proba, 0.55, 0.0, 0.0, 0.8, 'regime_block', idx % 3),
            )
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


def test_fit_intelligence_pack_uses_training_rows_fallback_when_summaries_missing(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    base_ts = 1773000000
    ts_values = [base_ts + (i * INTERVAL) for i in range(185)]
    _write_dataset(tmp_path / 'data' / 'dataset_phase2.csv', ts_values)
    signals_db = resolve_scope_runtime_paths(tmp_path, scope_tag=SCOPE_TAG, partition_enable=False).signals_db_path
    _write_signals_db(signals_db, ts_values)

    pack, out = fit_intelligence_pack(
        repo_root=tmp_path,
        config_path=cfg,
        asset=ASSET,
        interval_sec=INTERVAL,
        lookback_days=5,
    )

    meta = dict(pack.get('metadata') or {})
    anti = dict(pack.get('anti_overfit') or {})
    anti_src = dict(meta.get('anti_overfit_source') or {})
    assert out.exists()
    assert meta.get('training_rows', 0) >= 150
    assert anti.get('available') is True
    assert anti.get('windows_count', 0) >= 3
    assert anti_src.get('kind') == 'signals_eval_fallback'
    materialized = Path(str(anti_src.get('materialized_path')))
    assert materialized.exists()
    materialized_payload = json.loads(materialized.read_text(encoding='utf-8'))
    assert materialized_payload.get('source') == 'signals_eval_fallback'
    assert isinstance(materialized_payload.get('per_window'), list) and len(materialized_payload['per_window']) >= 3


def test_observe_summary_marks_reconcile_no_pending_as_ok() -> None:
    results = [
        _fake_validation_result('observe_once_practice_live', {'enabled': True, 'intent_created': False, 'blocked_reason': 'regime_block'}),
        _fake_validation_result('orders_after_practice', {'enabled': True, 'summary': {'consuming_today': 0, 'pending_unknown': 0, 'open_positions': 0}, 'recent_intents': []}),
        _fake_validation_result(
            'reconcile_after_practice',
            {
                'enabled': True,
                'scope_tag': SCOPE_TAG,
                'summary': {
                    'scope_tag': SCOPE_TAG,
                    'pending_before': 0,
                    'updated_intents': 0,
                    'new_orphans': 0,
                    'ambiguous_matches': 0,
                    'terminalized': 0,
                    'errors': [],
                },
                'detail': {
                    'scope_tag': SCOPE_TAG,
                    'pending_before': 0,
                    'pending_after': 0,
                    'errors': [],
                    'skipped_broker_scan': True,
                    'reason': 'no_pending_intents',
                },
            },
        ),
        _fake_validation_result('incidents_after_practice', {'ok': True, 'severity': 'ok', 'open_issues': []}),
    ]

    observe = _observe_summary(results)
    assert observe['intent_created'] is False
    assert observe['blocked_reason'] == 'regime_block'
    assert observe['reconcile_ok'] is True
    assert observe['no_trade_is_not_error'] is True


def test_incident_status_payload_ignores_release_warn_in_practice_stage(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)

    monkeypatch.setattr(
        'natbin.incidents.reporting.build_release_readiness_payload',
        lambda **kwargs: {'severity': 'warn', 'ready_for_live': False, 'execution_live': True},
    )
    monkeypatch.setattr(
        'natbin.incidents.reporting.alerts_status_payload',
        lambda **kwargs: {'telegram': {'enabled': False, 'send_enabled': False, 'credentials_present': False, 'recent_counts': {}, 'recent': []}},
    )
    monkeypatch.setattr('natbin.incidents.reporting.gate_status', lambda **kwargs: {'kill_switch': {'active': False}, 'drain_mode': {'active': False}})
    monkeypatch.setattr(
        'natbin.incidents.reporting.audit_security_posture',
        lambda **kwargs: {'blocked': False, 'severity': 'ok', 'credential_source': 'external'},
    )
    monkeypatch.setattr(
        'natbin.incidents.reporting.build_intelligence_surface_payload',
        lambda **kwargs: {
            'enabled': True,
            'severity': 'ok',
            'warnings': [],
            'summary': {'retrain_state': 'idle', 'retrain_priority': 'low', 'portfolio_feedback_blocked': False},
            'allocation': {},
            'execution': {'missing_fields': []},
        },
    )
    monkeypatch.setattr(
        'natbin.incidents.reporting.inspect_runtime_freshness',
        lambda **kwargs: RuntimeHardeningReport(
            scope_tag=SCOPE_TAG,
            checked_at_utc=datetime.now(tz=UTC).isoformat(timespec='seconds'),
            stale_after_sec=900,
            lock={},
            artifacts=[],
            stale_artifacts=[],
            actions=[],
            mode='inspect',
        ),
    )
    monkeypatch.setattr('natbin.incidents.reporting._health_summary', lambda *args, **kwargs: {'state': 'healthy', 'message': 'cycle_ok'})
    monkeypatch.setattr('natbin.incidents.reporting._loop_summary', lambda *args, **kwargs: {'phase': 'cycle', 'message': 'cycle_ok'})
    monkeypatch.setattr('natbin.incidents.reporting.load_recent_scope_incidents', lambda **kwargs: [])
    monkeypatch.setattr('natbin.incidents.reporting._summarize_incidents', lambda recent: {'count': 0, 'by_type': {}, 'by_severity': {}, 'latest': None})

    practice_payload = incident_status_payload(repo_root=tmp_path, config_path=cfg, stage='practice', write_artifact=False)
    default_payload = incident_status_payload(repo_root=tmp_path, config_path=cfg, stage=None, write_artifact=False)

    assert practice_payload['severity'] == 'ok'
    assert not practice_payload['open_issues']
    assert default_payload['severity'] == 'warn'
    assert any(item['code'] == 'release_readiness_warn' for item in default_payload['open_issues'])
