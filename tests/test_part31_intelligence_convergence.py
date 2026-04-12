from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from natbin.intelligence.paths import latest_eval_path, pack_path
from natbin.intelligence.refresh import refresh_config_intelligence
from natbin.usecases.observer import runner
from natbin.usecases.observer.model_cache import cache_supports_gate
from scripts.tools import portfolio_cp_meta_maintenance as cp_maint


ASSET = 'EURUSD-OTC'
INTERVAL = 300
SCOPE_TAG = 'EURUSD-OTC_300s'


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: practice',
                'multi_asset:',
                '  enabled: false',
                'decision:',
                '  tune_dir: runs/tune',
                'execution:',
                '  enabled: false',
                'intelligence:',
                '  enabled: true',
                '  artifact_dir: runs/intelligence',
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


def test_cache_supports_gate_detects_missing_cp() -> None:
    payload = {
        'cal': object(),
        'iso': object(),
        'meta_model': SimpleNamespace(model=object(), iso=object(), cp=None),
    }
    assert cache_supports_gate(payload, 'meta') is True
    assert cache_supports_gate(payload, 'cp') is False
    payload['meta_model'] = SimpleNamespace(model=object(), iso=object(), cp=object())
    assert cache_supports_gate(payload, 'cp') is True


def test_runner_rebuilds_cache_when_cp_gate_incompatible(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = data_dir / 'dataset_phase2.csv'
    df = pd.DataFrame(
        {
            'ts': [1700000000, 1700000300],
            'y_open_close': [1, 1],
            'f_vol48': [0.5, 0.5],
            'f_bb_width20': [0.5, 0.5],
            'f_atr14': [0.5, 0.5],
            'close': [1.0, 1.1],
        }
    )
    df.to_csv(dataset_path, index=False)

    cfg = {
        'data': {'asset': ASSET, 'interval_sec': INTERVAL, 'timezone': 'UTC'},
        'phase2': {'dataset_path': str(dataset_path)},
    }
    best = {
        'threshold': 0.01,
        'thresh_on': 'ev',
        'gate_mode': 'cp',
        'meta_model': 'hgb',
        'tune_dir': 'runs/tune',
        'bounds': {'vol_lo': 0.0, 'vol_hi': 1.0, 'bb_lo': 0.0, 'bb_hi': 1.0, 'atr_lo': 0.0, 'atr_hi': 1.0},
        'k': 1,
    }

    saved: dict[str, object] = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PAYOUT', '0.8')
    monkeypatch.setenv('GATE_FAIL_CLOSED', '1')
    monkeypatch.setattr(runner, 'load_cfg', lambda: (cfg, best))
    monkeypatch.setattr(
        runner,
        'load_cache',
        lambda asset, interval_sec: {'cal': 'cal', 'iso': 'iso', 'meta_model': SimpleNamespace(model=object(), iso=object(), cp=None), 'meta': {'train_end_ts': 1700000000}},
    )
    monkeypatch.setattr(runner, 'should_retrain', lambda *a, **k: False)
    monkeypatch.setattr(runner, 'train_base_cal_iso_meta', lambda **kwargs: ('cal2', 'iso2', SimpleNamespace(model=object(), iso=object(), cp=object())))
    monkeypatch.setattr(runner, 'save_cache', lambda *a, **k: saved.setdefault('payload', a[2]))
    monkeypatch.setattr(
        runner,
        'compute_scores',
        lambda **kwargs: (
            pd.Series([0.40, 0.80]).to_numpy(dtype=float),
            pd.Series([0.70, 0.90]).to_numpy(dtype=float),
            pd.Series([0.10, 0.80]).to_numpy(dtype=float),
            'cp_meta_iso',
        ),
    )
    monkeypatch.setattr(runner, 'executed_today_count', lambda *a, **k: 0)
    monkeypatch.setattr(runner, 'already_executed', lambda *a, **k: False)
    monkeypatch.setattr(runner, 'last_executed_ts', lambda *a, **k: None)
    monkeypatch.setattr(runner, 'heal_state_from_signals', lambda *a, **k: 1)
    monkeypatch.setattr(runner, 'already_state_only', lambda *a, **k: False)
    monkeypatch.setattr(runner, 'mark_executed', lambda *a, **k: None)
    monkeypatch.setattr(runner, 'maybe_apply_cp_alpha_env', lambda *a, **k: 0.0)
    monkeypatch.setattr(runner, 'write_sqlite_signal', lambda row: None)
    monkeypatch.setattr(runner, 'append_csv', lambda row: 'runs/live_signals_v2.csv')
    monkeypatch.setattr(runner, 'write_daily_summary', lambda **kwargs: 'runs/daily_summary.json')
    monkeypatch.setattr(runner, 'write_latest_decision_snapshot', lambda row: Path('runs/latest.json'))
    monkeypatch.setattr(runner, 'write_detailed_decision_snapshot', lambda row: None)
    monkeypatch.setattr(runner, 'build_incident_from_decision', lambda row: None)
    monkeypatch.setattr(runner, 'append_incident_event', lambda incident: Path('runs/incident.json'))

    runner.main()

    out = capsys.readouterr().out
    payload = dict(saved['payload'])
    meta = dict(payload['meta'])
    assert meta['refresh_reason'] == 'cache_incompatible_gate:cp'
    assert meta['cp_available'] is True
    assert '[P31] observer_cache_refresh reason=cache_incompatible_gate:cp' in out


def test_refresh_config_intelligence_writes_placeholder_latest_eval_when_decision_missing(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    pack = pack_path(repo_root=tmp_path, scope_tag=SCOPE_TAG)
    pack.parent.mkdir(parents=True, exist_ok=True)
    pack.write_text(
        json.dumps(
            {
                'kind': 'intelligence_pack',
                'metadata': {'training_rows': 321, 'training_strategy': 'bootstrap'},
            },
            indent=2,
        ),
        encoding='utf-8',
    )

    payload = refresh_config_intelligence(
        repo_root=tmp_path,
        config_path=cfg,
        asset=ASSET,
        interval_sec=INTERVAL,
        rebuild_pack=False,
        materialize_portfolio=False,
    )

    assert payload['ok'] is True
    item = payload['items'][0]
    assert item['decision_present'] is False
    assert item['latest_eval_present'] is True
    eval_payload = json.loads(latest_eval_path(repo_root=tmp_path, scope_tag=SCOPE_TAG).read_text(encoding='utf-8'))
    assert eval_payload['status'] == 'decision_missing'
    assert eval_payload['pack_available'] is True
    assert eval_payload['pack_training_rows'] == 321


def test_portfolio_cp_meta_maintenance_reaudits_and_marks_repairs(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(cp_maint, '_repo_root', lambda explicit: tmp_path)
    closure_results = iter([
        {
            'closure_state': 'healthy_waiting_signal',
            'recommended_action': 'wait_regime_rescan_track_cp_meta_debt',
            'closure_debts': [{'name': 'secondary_cp_meta_debt', 'scope_tags': ['EURGBP-OTC_300s']}],
        },
        {
            'closure_state': 'healthy_waiting_signal',
            'recommended_action': 'wait_regime_rescan',
            'closure_debts': [],
        },
    ])
    monkeypatch.setattr(cp_maint, 'run_closure_report', lambda repo_root, config, timeout_sec=420: next(closure_results))
    audit_results = iter([
        {
            'summary': {'cp_meta_missing_scopes': 1, 'missing_artifact_scopes': 0, 'stale_artifact_scopes': 0, 'watch_scopes': 0, 'hold_scopes': 1, 'actionable_scopes': 0},
            'scope_results': [{'scope': {'scope_tag': 'EURGBP-OTC_300s'}, 'cp_meta_missing': True}],
        },
        {
            'summary': {'cp_meta_missing_scopes': 0, 'missing_artifact_scopes': 0, 'stale_artifact_scopes': 0, 'watch_scopes': 1, 'hold_scopes': 0, 'actionable_scopes': 0},
            'scope_results': [{'scope': {'scope_tag': 'EURGBP-OTC_300s'}, 'cp_meta_missing': False}],
        },
    ])
    monkeypatch.setattr(cp_maint, 'run_signal_artifact_audit', lambda repo_root, config, timeout_sec=300: next(audit_results))
    monkeypatch.setattr(
        cp_maint,
        'execute_scope_maintenance',
        lambda repo_root, config, scope_tag, timeout_sec, intelligence_timeout_sec, dry_run: [
            cp_maint.StepResult('asset_prepare', scope_tag, ['py'], 0, False, '', '', True),
            cp_maint.StepResult('asset_candidate', scope_tag, ['py'], 0, False, '', '', True, parsed={'candidate': {'reason': 'regime_block'}}),
            cp_maint.StepResult('intelligence_refresh', scope_tag, ['py'], 0, False, '', '', True),
        ],
    )

    rc = cp_maint.main(['--repo-root', str(tmp_path), '--config', 'config/practice.yaml', '--json'])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['repaired_scope_tags'] == ['EURGBP-OTC_300s']
    assert payload['summary_delta']['cp_meta_missing_scopes']['before'] == 1
    assert payload['summary_delta']['cp_meta_missing_scopes']['after'] == 0
    assert payload['summary_delta']['cp_meta_missing_scopes']['delta'] == -1
