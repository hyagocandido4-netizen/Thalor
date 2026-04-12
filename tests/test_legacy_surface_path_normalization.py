from __future__ import annotations

from pathlib import PureWindowsPath
from types import SimpleNamespace

from natbin.config.compat_helpers import portable_path_str
from natbin.config.legacy_surface import resolved_to_legacy_payload


def _resolved_stub() -> SimpleNamespace:
    return SimpleNamespace(
        asset='EURUSD-OTC',
        interval_sec=300,
        timezone='UTC',
        data=SimpleNamespace(
            db_path=PureWindowsPath(r'data\market.sqlite3'),
            dataset_path=PureWindowsPath(r'data\from_base.csv'),
            lookback_candles=2000,
            max_batch=1000,
        ),
        broker=SimpleNamespace(balance_mode='PRACTICE', email='bot@example.com', password='secret'),
        decision=SimpleNamespace(
            gate_mode='cp',
            cp_bootstrap_fallback='auto',
            meta_model='hgb',
            thresh_on='ev',
            threshold=0.11,
            tune_dir=PureWindowsPath(r'runs\from_base'),
            bounds=SimpleNamespace(vol_lo=0.1, vol_hi=0.2, bb_lo=0.3, bb_hi=0.4, atr_lo=0.5, atr_hi=0.6),
            cp_alpha=0.05,
            fail_closed=True,
            cpreg=SimpleNamespace(enabled=True, alpha_start=0.12, alpha_end=0.18, slot2_mult=0.77),
        ),
        runtime_overrides=SimpleNamespace(
            threshold=0.22,
            cp_alpha=0.07,
            cp_bootstrap_fallback='auto',
            cpreg_alpha_start=0.15,
            cpreg_alpha_end=0.25,
            cpreg_slot2_mult=0.66,
            payout=0.91,
            market_open=False,
            meta_iso_blend=0.55,
            regime_mode='soft',
        ),
    )


def test_portable_path_str_normalizes_windows_style_paths() -> None:
    assert portable_path_str(PureWindowsPath(r'runs\live_signals.sqlite3')) == 'runs/live_signals.sqlite3'
    assert portable_path_str(r'data\from_base.csv') == 'data/from_base.csv'


def test_resolved_to_legacy_payload_normalizes_pathlike_fields() -> None:
    payload = resolved_to_legacy_payload(_resolved_stub())

    assert payload['dataset_path'] == 'data/from_base.csv'
    assert payload['db_path'] == 'data/market.sqlite3'
    assert payload['market_db_path'] == 'data/market.sqlite3'
    assert payload['tune_dir'] == 'runs/from_base'
    assert payload['cp_bootstrap_fallback'] == 'auto'
    assert payload['fail_closed'] is True
