from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from natbin.config.models import RegimeBoundsSettings
from natbin.runtime.observe_once import prepare_observer_environment
from natbin.usecases.observe_signal_topk_perday import load_cfg


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / 'src' / 'natbin'


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


LEGACY_YAML = '''
data:
  asset: EURUSD-OTC
  interval_sec: 300
  timezone: UTC
phase2:
  dataset_path: data/from_legacy.csv
best:
  threshold: 0.99
  thresh_on: ev
  gate_mode: cp
  meta_model: hgb
  tune_dir: runs/from_legacy
  bounds:
    vol_lo: 0.91
    vol_hi: 0.92
    bb_lo: 0.93
    bb_hi: 0.94
    atr_lo: 0.95
    atr_hi: 0.96
'''.strip()


def _write_repo(tmp_path: Path) -> tuple[Path, Path]:
    config_dir = tmp_path / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    modern = config_dir / 'base.yaml'
    modern.write_text(BASE_YAML, encoding='utf-8')
    legacy = tmp_path / 'config.yaml'
    legacy.write_text(LEGACY_YAML, encoding='utf-8')
    return modern, legacy


def test_observer_load_cfg_prefers_selected_modern_config_and_runtime_overrides(tmp_path: Path) -> None:
    modern, _legacy = _write_repo(tmp_path)

    cfg, best = load_cfg(repo_root=tmp_path, config_path=modern)

    assert cfg['phase2']['dataset_path'] == 'data/from_base.csv'
    assert cfg['data']['asset'] == 'EURUSD-OTC'
    assert int(cfg['data']['interval_sec']) == 300
    assert best['threshold'] == pytest.approx(0.22)
    assert best['tune_dir'] == 'runs/from_base'
    assert best['bounds']['vol_lo'] == pytest.approx(0.10)
    assert best['bounds']['atr_hi'] == pytest.approx(0.60)
    assert best['cp_alpha'] == pytest.approx(0.07)


def test_observer_load_cfg_can_read_explicit_legacy_config_path(tmp_path: Path) -> None:
    _modern, legacy = _write_repo(tmp_path)

    cfg, best = load_cfg(repo_root=tmp_path, config_path=legacy)

    assert cfg['phase2']['dataset_path'] == 'data/from_legacy.csv'
    assert best['threshold'] == pytest.approx(0.99)
    assert best['tune_dir'] == 'runs/from_legacy'
    assert best['bounds']['bb_lo'] == pytest.approx(0.93)
    assert best['bounds']['atr_hi'] == pytest.approx(0.96)


def test_prepare_observer_environment_exports_runtime_override_surface(tmp_path: Path) -> None:
    modern, _legacy = _write_repo(tmp_path)

    updates = prepare_observer_environment(
        repo_root=tmp_path,
        config_path=modern,
        topk=5,
        lookback_candles=321,
    )

    assert updates['THALOR_CONFIG_PATH'] == str(modern.resolve())
    assert updates['ASSET'] == 'EURUSD-OTC'
    assert updates['INTERVAL_SEC'] == '300'
    assert updates['TIMEZONE'] == 'UTC'
    assert updates['THRESHOLD'] == '0.22'
    assert updates['CP_ALPHA'] == '0.07'
    assert updates['CPREG_ENABLE'] == '1'
    assert updates['CPREG_ALPHA_START'] == '0.15'
    assert updates['CPREG_ALPHA_END'] == '0.25'
    assert updates['CPREG_SLOT2_MULT'] == '0.66'
    assert updates['META_ISO_BLEND'] == '0.55'
    assert updates['REGIME_MODE'] == 'soft'
    assert updates['PAYOUT'] == '0.91'
    assert updates['MARKET_OPEN'] == '0'
    assert updates['TOPK_K'] == '5'
    assert updates['LOOKBACK_CANDLES'] == '321'
    assert 'EURUSD-OTC_300s' in str(updates['LIVE_SIGNALS_PATH'])
    assert 'EURUSD-OTC_300s' in str(updates['MARKET_CONTEXT_PATH'])


def test_regime_bounds_validation_rejects_inverted_ranges() -> None:
    with pytest.raises(ValidationError):
        RegimeBoundsSettings(
            vol_lo=0.20,
            vol_hi=0.10,
            bb_lo=0.30,
            bb_hi=0.40,
            atr_lo=0.50,
            atr_hi=0.60,
        )


EXPECTED_ROOT_SHIMS = {
    'backfill_candles.py',
    'collect_candles.py',
    'config2.py',
    'db.py',
    'dsio.py',
    'envutil.py',
    'iq_client.py',
    'paper_backtest.py',
    'paper_backtest_v2.py',
    'paper_backtest_v3.py',
    'paper_multiwindow_v3.py',
    'paper_pnl_backtest.py',
    'paper_topk_multiwindow.py',
    'paper_topk_perday_multiwindow.py',
    'paper_tune_v2.py',
    'refresh_daily_summary.py',
    'risk_report.py',
    'runtime_observability.py',
    'runtime_perf.py',
    'runtime_scope.py',
    'settings.py',
    'summary_paths.py',
    'sweep_thresholds.py',
    'train_walkforward.py',
    'tune_multiwindow_topk.py',
    'validate_gaps.py',
}


def test_root_shim_inventory_is_frozen_allowlist() -> None:
    actual = {
        py.name
        for py in SRC_ROOT.glob('*.py')
        if 'Compatibility shim.' in py.read_text(encoding='utf-8', errors='replace')
    }
    assert actual == EXPECTED_ROOT_SHIMS


@pytest.mark.parametrize(
    'relpath',
    [
        Path('src/natbin/usecases/observe_signal_topk_perday.py'),
        Path('src/natbin/usecases/observe_signal_latest.py'),
    ],
)
def test_operational_observers_do_not_hardcode_config_yaml(relpath: Path) -> None:
    txt = (REPO_ROOT / relpath).read_text(encoding='utf-8', errors='replace')
    assert 'Path("config.yaml")' not in txt
    assert "Path('config.yaml')" not in txt
    assert 'yaml.safe_load(Path("config.yaml")' not in txt
    assert "yaml.safe_load(Path('config.yaml')" not in txt
