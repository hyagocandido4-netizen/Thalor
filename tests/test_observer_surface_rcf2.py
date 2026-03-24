from __future__ import annotations

from pathlib import Path

from natbin.runtime.observer_surface import build_observer_environment, resolve_observer_surface


BASE_YAML = '''
version: "2.0"
data:
  dataset_path: data/from_base.csv
decision:
  gate_mode: cp
  meta_model: hgb
  thresh_on: ev
  threshold: 0.11
  cp_alpha: 0.05
  cpreg:
    enabled: true
    alpha_start: 0.12
    alpha_end: 0.18
    warmup_frac: 0.30
    ramp_end_frac: 0.80
    slot2_mult: 0.77
  tune_dir: runs/from_base
  bounds:
    vol_lo: 0.10
    vol_hi: 0.20
    bb_lo: 0.30
    bb_hi: 0.40
    atr_lo: 0.50
    atr_hi: 0.60
assets:
  - asset: EURUSD-OTC
    interval_sec: 300
    timezone: UTC
runtime_overrides:
  threshold: 0.22
  cp_alpha: 0.07
  cpreg_alpha_start: 0.15
  cpreg_alpha_end: 0.25
  cpreg_slot2_mult: 0.66
  meta_iso_blend: 0.55
  regime_mode: soft
  payout: 0.91
  market_open: false
'''.strip()


def test_observer_surface_shares_cfg_and_env_bridge(tmp_path: Path) -> None:
    config_dir = tmp_path / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    modern = config_dir / 'base.yaml'
    modern.write_text(BASE_YAML, encoding='utf-8')

    surface = resolve_observer_surface(repo_root=tmp_path, config_path=modern)

    assert surface.repo_root == tmp_path.resolve()
    assert surface.config_path == modern.resolve()
    assert surface.asset == 'EURUSD-OTC'
    assert surface.interval_sec == 300
    assert surface.cfg['phase2']['dataset_path'] == 'data/from_base.csv'
    assert surface.best['threshold'] == 0.22
    assert surface.best['cp_alpha'] == 0.07
    assert surface.legacy_env['CPREG_ENABLE'] == '1'
    assert surface.legacy_env['CPREG_SLOT2_MULT'] == '0.66'
    assert surface.legacy_env['MARKET_OPEN'] == '0'

    updates = build_observer_environment(repo_root=tmp_path, config_path=modern, topk=5, lookback_candles=321)
    assert updates['THALOR_CONFIG_PATH'] == str(modern.resolve())
    assert updates['TOPK_K'] == '5'
    assert updates['LOOKBACK_CANDLES'] == '321'
    assert updates['THRESHOLD'] == '0.22'
    assert 'EURUSD-OTC_300s' in str(updates['LIVE_SIGNALS_PATH'])
    assert 'EURUSD-OTC_300s' in str(updates['MARKET_CONTEXT_PATH'])
