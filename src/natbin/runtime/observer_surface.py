from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config.compat_runtime import resolved_to_legacy_dict, resolved_to_legacy_env_map
from ..config.loader import load_resolved_config
from ..config.paths import resolve_config_path, resolve_repo_root
from .scope import live_signals_csv_path, market_context_path


@dataclass(frozen=True)
class ObserverConfigSurface:
    repo_root: Path
    config_path: Path
    resolved: Any
    legacy_cfg: dict[str, Any]
    cfg: dict[str, Any]
    best: dict[str, Any]
    legacy_env: dict[str, str]
    asset: str
    interval_sec: int
    timezone: str


def _resolve_asset_interval_overrides(
    *,
    asset: str | None = None,
    interval_sec: int | None = None,
) -> tuple[str | None, int | None]:
    asset_override = asset if asset is not None else (os.getenv('ASSET') or None)
    interval_override = interval_sec
    if interval_override is None:
        raw_interval = os.getenv('INTERVAL_SEC')
        if raw_interval is not None and str(raw_interval).strip() != '':
            try:
                interval_override = int(str(raw_interval).strip())
            except Exception:
                interval_override = None
    return asset_override, interval_override


def _build_best_payload(legacy_cfg: dict[str, Any]) -> dict[str, Any]:
    best: dict[str, Any] = {
        'threshold': float(legacy_cfg.get('threshold', 0.02)),
        'thresh_on': str(legacy_cfg.get('thresh_on', 'ev')),
        'gate_mode': str(legacy_cfg.get('gate_mode', 'cp')),
        'meta_model': str(legacy_cfg.get('meta_model', 'hgb')),
        'tune_dir': str(legacy_cfg.get('tune_dir', '') or ''),
        'bounds': dict(legacy_cfg.get('bounds') or {}),
        'k': int(legacy_cfg.get('topk_k', 3)),
    }
    if legacy_cfg.get('cp_alpha') is not None:
        best['cp_alpha'] = float(legacy_cfg.get('cp_alpha'))
    return best


def _build_cfg_payload(*, legacy_cfg: dict[str, Any], resolved: Any) -> dict[str, Any]:
    return {
        'data': {
            'asset': str(legacy_cfg.get('asset', getattr(resolved, 'asset', 'UNKNOWN'))),
            'interval_sec': int(legacy_cfg.get('interval_sec', getattr(resolved, 'interval_sec', 300))),
            'timezone': str(legacy_cfg.get('timezone', getattr(resolved, 'timezone', 'UTC'))),
        },
        'phase2': {
            'dataset_path': str(legacy_cfg.get('dataset_path', getattr(getattr(resolved, 'data', None), 'dataset_path', 'data/dataset_phase2.csv'))),
        },
        'best': _build_best_payload(legacy_cfg),
    }


def resolve_observer_surface(
    *,
    repo_root: str | Path | None = None,
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
) -> ObserverConfigSurface:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    asset_override, interval_override = _resolve_asset_interval_overrides(asset=asset, interval_sec=interval_sec)
    resolved = load_resolved_config(
        config_path=cfg_path,
        repo_root=root,
        asset=asset_override,
        interval_sec=interval_override,
    )
    legacy_cfg = resolved_to_legacy_dict(resolved)
    cfg = _build_cfg_payload(legacy_cfg=legacy_cfg, resolved=resolved)
    legacy_env = resolved_to_legacy_env_map(resolved)
    return ObserverConfigSurface(
        repo_root=root,
        config_path=cfg_path,
        resolved=resolved,
        legacy_cfg=legacy_cfg,
        cfg=cfg,
        best=dict(cfg['best']),
        legacy_env=legacy_env,
        asset=str(cfg['data']['asset']),
        interval_sec=int(cfg['data']['interval_sec']),
        timezone=str(cfg['data']['timezone']),
    )


def load_observer_cfg(
    *,
    repo_root: str | Path | None = None,
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    surface = resolve_observer_surface(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
    )
    return dict(surface.cfg), dict(surface.best)


def build_observer_environment(
    *,
    repo_root: str | Path,
    config_path: str | Path | None = None,
    topk: int = 3,
    lookback_candles: int = 2000,
) -> dict[str, str | None]:
    surface = resolve_observer_surface(repo_root=repo_root, config_path=config_path)

    tz_name = str(surface.legacy_env.get('TIMEZONE', surface.timezone))
    try:
        now_local = datetime.now(tz=ZoneInfo(tz_name))
    except Exception:
        tz_name = str(surface.timezone or 'UTC')
        try:
            now_local = datetime.now(tz=ZoneInfo(tz_name))
        except Exception:
            tz_name = 'UTC'
            now_local = datetime.now(tz=ZoneInfo('UTC'))

    day = now_local.strftime('%Y-%m-%d')
    updates: dict[str, str | None] = dict(surface.legacy_env)
    updates.update(
        {
            # Respect the profile-resolved gate semantics first; env can still
            # override upstream when explicitly injected into legacy_env.
            'GATE_FAIL_CLOSED': str(surface.legacy_env.get('GATE_FAIL_CLOSED', os.getenv('GATE_FAIL_CLOSED', '1') or '1')),
            'LOOKBACK_CANDLES': str(int(lookback_candles)),
            'THALOR_CONFIG_PATH': str(surface.config_path),
            'MARKET_CONTEXT_PATH': str(
                market_context_path(asset=surface.asset, interval_sec=surface.interval_sec, out_dir=surface.repo_root / 'runs')
            ),
            'LIVE_SIGNALS_PATH': str(
                live_signals_csv_path(day=day, asset=surface.asset, interval_sec=surface.interval_sec, out_dir=surface.repo_root / 'runs')
            ),
        }
    )

    if int(topk) > 0:
        updates['TOPK_K'] = str(int(topk))
    else:
        updates['TOPK_K'] = None

    return updates
