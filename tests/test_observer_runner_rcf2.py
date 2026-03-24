from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from natbin.usecases.observer import runner


def test_runner_cp_gate_path_applies_cpreg_without_nameerror(tmp_path: Path, monkeypatch, capsys) -> None:
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
        'data': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'timezone': 'UTC'},
        'phase2': {'dataset_path': str(dataset_path)},
    }
    best = {
        'threshold': 0.01,
        'thresh_on': 'ev',
        'gate_mode': 'cp',
        'meta_model': 'hgb',
        'tune_dir': 'runs/tune',
        'bounds': {'vol_lo': 0.0, 'vol_hi': 1.0, 'bb_lo': 0.0, 'bb_hi': 1.0, 'atr_lo': 0.0, 'atr_hi': 1.0},
        'k': 3,
    }

    written: dict[str, object] = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PAYOUT', '0.8')
    monkeypatch.setenv('GATE_FAIL_CLOSED', '1')
    monkeypatch.setattr(runner, 'load_cfg', lambda: (cfg, best))
    monkeypatch.setattr(runner, 'load_cache', lambda asset, interval_sec: None)
    monkeypatch.setattr(runner, 'should_retrain', lambda *a, **k: True)
    monkeypatch.setattr(runner, 'train_base_cal_iso_meta', lambda **kwargs: ('cal', 'iso', 'meta'))
    monkeypatch.setattr(runner, 'save_cache', lambda *a, **k: None)
    monkeypatch.setattr(
        runner,
        'compute_scores',
        lambda **kwargs: (
            np.array([0.40, 0.80], dtype=float),
            np.array([0.70, 0.90], dtype=float),
            np.array([0.10, 0.80], dtype=float),
            'cp_meta_iso',
        ),
    )
    monkeypatch.setattr(runner, 'executed_today_count', lambda *a, **k: 0)
    monkeypatch.setattr(runner, 'already_executed', lambda *a, **k: False)
    monkeypatch.setattr(runner, 'last_executed_ts', lambda *a, **k: None)
    monkeypatch.setattr(runner, 'heal_state_from_signals', lambda *a, **k: 1)
    monkeypatch.setattr(runner, 'already_state_only', lambda *a, **k: False)
    monkeypatch.setattr(runner, 'mark_executed', lambda *a, **k: None)
    monkeypatch.setattr(runner, 'maybe_apply_cp_alpha_env', lambda *a, **k: 0.1234)
    monkeypatch.setattr(runner, 'write_sqlite_signal', lambda row: written.setdefault('row', dict(row)))
    monkeypatch.setattr(runner, 'append_csv', lambda row: 'runs/live_signals_v2.csv')
    monkeypatch.setattr(runner, 'write_daily_summary', lambda **kwargs: 'runs/daily_summary.json')
    monkeypatch.setattr(runner, 'write_latest_decision_snapshot', lambda row: Path('runs/latest.json'))
    monkeypatch.setattr(runner, 'write_detailed_decision_snapshot', lambda row: None)
    monkeypatch.setattr(runner, 'build_incident_from_decision', lambda row: None)
    monkeypatch.setattr(runner, 'append_incident_event', lambda incident: Path('runs/incident.json'))

    runner.main()

    out = capsys.readouterr().out
    row = dict(written['row'])
    assert row['action'] == 'CALL'
    assert row['reason'] == 'topk_emit'
    assert row['gate_mode_requested'] == 'cp'
    assert '[CPREG] cp_alpha_applied=0.1234 slot=1' in out
