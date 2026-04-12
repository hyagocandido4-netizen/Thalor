from __future__ import annotations

from typing import Any, Mapping

from .compat_helpers import (
    bounds_dict,
    env_first,
    first_present,
    portable_path_str,
    pull,
    safe_bool,
    safe_float,
    safe_int,
    safe_secret,
)


LEGACY_OPTIONAL_ENV_FIELDS: dict[str, tuple[str, ...]] = {
    'cp_alpha': ('CP_ALPHA',),
    'cp_bootstrap_fallback': ('CP_BOOTSTRAP_FALLBACK',),
    'cpreg_enable': ('CPREG_ENABLE',),
    'cpreg_alpha_start': ('CPREG_ALPHA_START',),
    'cpreg_alpha_end': ('CPREG_ALPHA_END',),
    'cpreg_slot2_mult': ('CPREG_SLOT2_MULT',),
    'meta_iso_blend': ('META_ISO_BLEND',),
    'regime_mode': ('REGIME_MODE',),
    'market_open': ('MARKET_OPEN',),
    'payout': ('PAYOUT',),
}


def fallback_legacy_payload() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        'asset': env_first('ASSET', 'THALOR__ASSET', default='EURUSD-OTC'),
        'interval_sec': safe_int(env_first('INTERVAL_SEC', 'THALOR__INTERVAL_SEC', default='300'), 300),
        'timezone': env_first('TIMEZONE', 'TZ', 'THALOR__TIMEZONE', default='America/Sao_Paulo'),
        'lookback_candles': safe_int(env_first('LOOKBACK_CANDLES', 'THALOR__LOOKBACK_CANDLES', default='2000'), 2000),
        'market_db_path': portable_path_str(env_first('MARKET_DB_PATH', 'THALOR__MARKET_DB_PATH', default='data/market.sqlite3'), 'data/market.sqlite3'),
        'db_path': portable_path_str(env_first('MARKET_DB_PATH', 'THALOR__MARKET_DB_PATH', default='data/market.sqlite3'), 'data/market.sqlite3'),
        'dataset_path': portable_path_str(env_first('DATASET_PATH', 'THALOR__DATASET_PATH', default='data/dataset_phase2.csv'), 'data/dataset_phase2.csv'),
        'max_batch': safe_int(env_first('MAX_BATCH', 'THALOR__DATA__MAX_BATCH', default='1000'), 1000),
        'payout_default': safe_float(env_first('PAYOUT', 'THALOR__PAYOUT', default='0.8'), 0.8),
        'topk_k': safe_int(env_first('TOPK_K', 'THALOR__TOPK_K', default='3'), 3),
        'balance_mode': env_first('IQ_BALANCE_MODE', 'THALOR__BALANCE_MODE', default='PRACTICE'),
        'gate_mode': env_first('GATE_MODE', 'THALOR__GATE_MODE', default='cp'),
        'fail_closed': safe_bool(env_first('GATE_FAIL_CLOSED', 'THALOR__DECISION__FAIL_CLOSED', default='1'), True),
        'meta_model': env_first('META_MODEL', 'THALOR__DECISION__META_MODEL', default='hgb'),
        'thresh_on': env_first('THRESH_ON', 'THALOR__DECISION__THRESH_ON', default='ev'),
        'threshold': safe_float(env_first('THRESHOLD', 'THALOR__THRESHOLD', default='0.02'), 0.02),
        'iq_email': env_first('IQ_EMAIL', 'THALOR__BROKER__EMAIL', default=''),
        'iq_password': env_first('IQ_PASSWORD', 'THALOR__BROKER__PASSWORD', default=''),
        'runs_dir': 'runs',
        'n_splits': 6,
        'threshold_min': 0.60,
        'threshold_max': 0.80,
        'threshold_step': 0.01,
    }
    if (value := env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cp_alpha'])) is not None:
        cfg['cp_alpha'] = safe_float(value, 0.05)
    if (value := env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cpreg_enable'])) is not None:
        cfg['cpreg_enable'] = safe_bool(value, False)
    if (value := env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cpreg_alpha_start'])) is not None:
        cfg['cpreg_alpha_start'] = safe_float(value, 0.06)
    if (value := env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cpreg_alpha_end'])) is not None:
        cfg['cpreg_alpha_end'] = safe_float(value, 0.09)
    if (value := env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cpreg_slot2_mult'])) is not None:
        cfg['cpreg_slot2_mult'] = safe_float(value, 0.85)
    if (value := env_first(*LEGACY_OPTIONAL_ENV_FIELDS['meta_iso_blend'])) is not None:
        cfg['meta_iso_blend'] = safe_float(value, 1.0)
    if (value := env_first(*LEGACY_OPTIONAL_ENV_FIELDS['regime_mode'])) is not None:
        cfg['regime_mode'] = str(value)
    if (value := env_first(*LEGACY_OPTIONAL_ENV_FIELDS['market_open'])) is not None:
        cfg['market_open'] = safe_bool(value, True)
    return cfg


def resolved_to_legacy_payload(resolved: Any) -> dict[str, Any]:
    threshold = safe_float(
        first_present(
            pull(resolved, 'runtime_overrides', 'threshold', default=None),
            pull(resolved, 'decision', 'threshold', default=0.02),
        ),
        0.02,
    )
    cp_alpha_value = first_present(
        pull(resolved, 'runtime_overrides', 'cp_alpha', default=None),
        pull(resolved, 'decision', 'cp_alpha', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cp_alpha']),
    )
    cpreg_enable_value = first_present(
        pull(resolved, 'runtime_overrides', 'cpreg_enable', default=None),
        pull(resolved, 'decision', 'cpreg', 'enabled', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cpreg_enable']),
    )
    cp_bootstrap_fallback_value = first_present(
        pull(resolved, 'runtime_overrides', 'cp_bootstrap_fallback', default=None),
        pull(resolved, 'decision', 'cp_bootstrap_fallback', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cp_bootstrap_fallback']),
    )
    cpreg_alpha_start_value = first_present(
        pull(resolved, 'runtime_overrides', 'cpreg_alpha_start', default=None),
        pull(resolved, 'decision', 'cpreg', 'alpha_start', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cpreg_alpha_start']),
    )
    cpreg_alpha_end_value = first_present(
        pull(resolved, 'runtime_overrides', 'cpreg_alpha_end', default=None),
        pull(resolved, 'decision', 'cpreg', 'alpha_end', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cpreg_alpha_end']),
    )
    cpreg_slot2_mult_value = first_present(
        pull(resolved, 'runtime_overrides', 'cpreg_slot2_mult', default=None),
        pull(resolved, 'decision', 'cpreg', 'slot2_mult', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['cpreg_slot2_mult']),
    )
    meta_iso_blend_value = first_present(
        pull(resolved, 'runtime_overrides', 'meta_iso_blend', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['meta_iso_blend']),
    )
    regime_mode_value = first_present(
        pull(resolved, 'runtime_overrides', 'regime_mode', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['regime_mode']),
    )
    market_open_value = first_present(
        pull(resolved, 'runtime_overrides', 'market_open', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['market_open']),
    )
    payout_value = first_present(
        pull(resolved, 'runtime_overrides', 'payout', default=None),
        env_first(*LEGACY_OPTIONAL_ENV_FIELDS['payout'], default='0.8'),
    )

    out: dict[str, Any] = {
        'asset': str(getattr(resolved, 'asset', None) or env_first('ASSET', default='EURUSD-OTC')),
        'interval_sec': safe_int(getattr(resolved, 'interval_sec', None) or env_first('INTERVAL_SEC', default='300'), 300),
        'timezone': str(getattr(resolved, 'timezone', None) or env_first('TIMEZONE', 'TZ', default='America/Sao_Paulo')),
        'lookback_candles': safe_int(pull(resolved, 'data', 'lookback_candles', default=2000), 2000),
        'market_db_path': portable_path_str(pull(resolved, 'data', 'db_path', default='data/market.sqlite3'), 'data/market.sqlite3'),
        'db_path': portable_path_str(pull(resolved, 'data', 'db_path', default='data/market.sqlite3'), 'data/market.sqlite3'),
        'dataset_path': portable_path_str(pull(resolved, 'data', 'dataset_path', default='data/dataset_phase2.csv'), 'data/dataset_phase2.csv'),
        'max_batch': safe_int(pull(resolved, 'data', 'max_batch', default=1000), 1000),
        'payout_default': safe_float(payout_value, 0.8),
        'topk_k': safe_int(env_first('TOPK_K', default='3'), 3),
        'balance_mode': str(pull(resolved, 'broker', 'balance_mode', default='PRACTICE')),
        'gate_mode': str(pull(resolved, 'decision', 'gate_mode', default='cp')),
        'fail_closed': safe_bool(pull(resolved, 'decision', 'fail_closed', default=True), True),
        'meta_model': str(pull(resolved, 'decision', 'meta_model', default='hgb')),
        'thresh_on': str(pull(resolved, 'decision', 'thresh_on', default='ev')),
        'threshold': threshold,
        'tune_dir': portable_path_str(pull(resolved, 'decision', 'tune_dir', default='') or '', ''),
        'bounds': bounds_dict(pull(resolved, 'decision', 'bounds', default=None)),
        'iq_email': str(pull(resolved, 'broker', 'email', default=env_first('IQ_EMAIL', default=''))),
        'iq_password': safe_secret(pull(resolved, 'broker', 'password', default=env_first('IQ_PASSWORD', default=''))),
        'runs_dir': 'runs',
        'n_splits': 6,
        'threshold_min': 0.60,
        'threshold_max': 0.80,
        'threshold_step': 0.01,
    }
    if cp_alpha_value is not None:
        out['cp_alpha'] = safe_float(cp_alpha_value, 0.05)
    if cp_bootstrap_fallback_value is not None:
        out['cp_bootstrap_fallback'] = str(cp_bootstrap_fallback_value).strip().lower() or 'off'
    if cpreg_enable_value is not None:
        out['cpreg_enable'] = safe_bool(cpreg_enable_value, False)
    if cpreg_alpha_start_value is not None:
        out['cpreg_alpha_start'] = safe_float(cpreg_alpha_start_value, 0.06)
    if cpreg_alpha_end_value is not None:
        out['cpreg_alpha_end'] = safe_float(cpreg_alpha_end_value, 0.09)
    if cpreg_slot2_mult_value is not None:
        out['cpreg_slot2_mult'] = safe_float(cpreg_slot2_mult_value, 0.85)
    if meta_iso_blend_value is not None:
        out['meta_iso_blend'] = safe_float(meta_iso_blend_value, 1.0)
    if regime_mode_value is not None:
        out['regime_mode'] = str(regime_mode_value)
    if market_open_value is not None:
        out['market_open'] = safe_bool(market_open_value, True)
    return out


def legacy_payload_to_env_map(payload: Mapping[str, Any]) -> dict[str, str]:
    mapping = {
        'ASSET': str(payload.get('asset', 'EURUSD-OTC')),
        'INTERVAL_SEC': str(payload.get('interval_sec', 300)),
        'TIMEZONE': str(payload.get('timezone', 'America/Sao_Paulo')),
        'LOOKBACK_CANDLES': str(payload.get('lookback_candles', 2000)),
        'MARKET_DB_PATH': portable_path_str(payload.get('market_db_path', 'data/market.sqlite3'), 'data/market.sqlite3'),
        'DATASET_PATH': portable_path_str(payload.get('dataset_path', 'data/dataset_phase2.csv'), 'data/dataset_phase2.csv'),
        'MAX_BATCH': str(payload.get('max_batch', 1000)),
        'PAYOUT': str(payload.get('payout_default', 0.8)),
        'TOPK_K': str(payload.get('topk_k', 3)),
        'IQ_BALANCE_MODE': str(payload.get('balance_mode', 'PRACTICE')),
        'GATE_MODE': str(payload.get('gate_mode', 'cp')),
        'GATE_FAIL_CLOSED': '1' if bool(payload.get('fail_closed', True)) else '0',
        'META_MODEL': str(payload.get('meta_model', 'hgb')),
        'THRESH_ON': str(payload.get('thresh_on', 'ev')),
        'THRESHOLD': str(payload.get('threshold', 0.02)),
    }
    if payload.get('cp_alpha') is not None:
        mapping['CP_ALPHA'] = str(payload.get('cp_alpha'))
    if payload.get('cp_bootstrap_fallback') is not None:
        mapping['CP_BOOTSTRAP_FALLBACK'] = str(payload.get('cp_bootstrap_fallback'))
    if payload.get('cpreg_enable') is not None:
        mapping['CPREG_ENABLE'] = '1' if bool(payload.get('cpreg_enable')) else '0'
    if payload.get('cpreg_alpha_start') is not None:
        mapping['CPREG_ALPHA_START'] = str(payload.get('cpreg_alpha_start'))
    if payload.get('cpreg_alpha_end') is not None:
        mapping['CPREG_ALPHA_END'] = str(payload.get('cpreg_alpha_end'))
    if payload.get('cpreg_slot2_mult') is not None:
        mapping['CPREG_SLOT2_MULT'] = str(payload.get('cpreg_slot2_mult'))
    if payload.get('meta_iso_blend') is not None:
        mapping['META_ISO_BLEND'] = str(payload.get('meta_iso_blend'))
    if payload.get('regime_mode') is not None:
        mapping['REGIME_MODE'] = str(payload.get('regime_mode'))
    if payload.get('market_open') is not None:
        mapping['MARKET_OPEN'] = '1' if bool(payload.get('market_open')) else '0'
    return mapping
