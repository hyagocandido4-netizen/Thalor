from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml
from zoneinfo import ZoneInfo


def sanitize_asset(asset: str) -> str:
    out: list[str] = []
    for ch in str(asset or ''):
        if ch.isalnum() or ch in ('-', '_'):
            out.append(ch)
        else:
            out.append('_')
    s = ''.join(out).strip('_')
    return s or 'UNKNOWN'


def sanitize_interval(interval_sec: int | str | None) -> str | None:
    if interval_sec is None:
        return None
    try:
        iv = int(str(interval_sec).strip())
    except Exception:
        return None
    if iv <= 0:
        return None
    return f"{iv}s"


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    s = str(raw).strip().lower()
    if s == '':
        return bool(default)
    return s not in ('0', 'false', 'f', 'no', 'n', 'off')


def repo_asset(*, config_path: str = 'config.yaml', default: str = 'UNKNOWN') -> str:
    p = Path(config_path)
    if not p.exists():
        return default
    try:
        cfg = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        data = cfg.get('data') or {}
        asset = str(data.get('asset') or default).strip()
        return asset or default
    except Exception:
        return default


def repo_interval_sec(*, config_path: str = 'config.yaml', default: int = 300) -> int:
    p = Path(config_path)
    if not p.exists():
        return int(default)
    try:
        cfg = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        data = cfg.get('data') or {}
        return int(data.get('interval_sec') or default)
    except Exception:
        return int(default)


def repo_timezone(*, config_path: str = 'config.yaml', default: str = 'UTC') -> ZoneInfo:
    p = Path(config_path)
    tz_name = default
    if p.exists():
        try:
            cfg = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
            data = cfg.get('data') or {}
            tz_name = str(data.get('timezone') or default).strip() or default
        except Exception:
            tz_name = default
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(default)


def repo_timezone_name(*, config_path: str = 'config.yaml', default: str = 'UTC') -> str:
    tz = repo_timezone(config_path=config_path, default=default)
    return str(getattr(tz, 'key', str(tz)) or default)


def repo_now(*, config_path: str = 'config.yaml', default_tz: str = 'UTC') -> datetime:
    return datetime.now(tz=repo_timezone(config_path=config_path, default=default_tz))


def daily_summary_filename(day: str, asset: str | None = None, interval_sec: int | None = None) -> str:
    ymd = str(day).replace('-', '')
    if asset and interval_sec is not None:
        tag = sanitize_interval(interval_sec)
        if tag:
            return f"daily_summary_{ymd}_{sanitize_asset(asset)}_{tag}.json"
    if asset:
        return f"daily_summary_{ymd}_{sanitize_asset(asset)}.json"
    return f"daily_summary_{ymd}.json"


def daily_summary_path(*, day: str, asset: str | None = None, interval_sec: int | None = None, out_dir: str | Path = 'runs') -> Path:
    return Path(out_dir) / daily_summary_filename(day, asset, interval_sec)


def daily_summary_candidates(*, day: str, asset: str | None = None, interval_sec: int | None = None, out_dir: str | Path = 'runs', allow_legacy_fallback: bool | None = None) -> list[Path]:
    """Return candidate daily-summary files ordered from safest to loosest match.

    Important:
    - When both ``asset`` and ``interval_sec`` are provided, the correct summary is
      the interval-scoped one. Falling back to ``asset-only`` or global summaries
      can silently mix metrics across timeframes.
    - Legacy fallback is disabled by default and must be re-enabled explicitly via
      ``SUMMARY_LEGACY_FALLBACK=1`` or ``allow_legacy_fallback=True``.
    """
    if allow_legacy_fallback is None:
        allow_legacy_fallback = _env_truthy('SUMMARY_LEGACY_FALLBACK', default=False)

    out: list[Path] = []
    seen: set[str] = set()
    candidates: list[Path | None] = []

    if asset and interval_sec is not None:
        candidates.append(daily_summary_path(day=day, asset=asset, interval_sec=interval_sec, out_dir=out_dir))
        if allow_legacy_fallback:
            candidates.append(daily_summary_path(day=day, asset=asset, interval_sec=None, out_dir=out_dir))
            candidates.append(daily_summary_path(day=day, asset=None, interval_sec=None, out_dir=out_dir))
    elif asset:
        candidates.append(daily_summary_path(day=day, asset=asset, interval_sec=None, out_dir=out_dir))
        candidates.append(daily_summary_path(day=day, asset=None, interval_sec=None, out_dir=out_dir))
    else:
        candidates.append(daily_summary_path(day=day, asset=None, interval_sec=None, out_dir=out_dir))

    for p in candidates:
        if p is None:
            continue
        sp = str(p)
        if sp in seen:
            continue
        seen.add(sp)
        out.append(p)
    return out


def find_daily_summary_path(*, day: str, asset: str | None = None, interval_sec: int | None = None, out_dir: str | Path = 'runs', allow_legacy_fallback: bool | None = None) -> Path | None:
    for p in daily_summary_candidates(day=day, asset=asset, interval_sec=interval_sec, out_dir=out_dir, allow_legacy_fallback=allow_legacy_fallback):
        if p.exists():
            return p
    return None


def load_json_file(path: str | Path) -> Any | None:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return None


def load_daily_summary_checked(
    *,
    day: str,
    asset: str | None = None,
    interval_sec: int | None = None,
    out_dir: str | Path = 'runs',
    allow_legacy_fallback: bool | None = None,
    expected_timezone: str | None = None,
    require_timezone: bool | None = None,
) -> tuple[dict | None, Path | None, dict[str, Any]]:
    """Find, load and validate a daily-summary file against the expected identity.

    Returns ``(summary_dict_or_none, found_path_or_none, meta)`` to stay compatible
    with the auto-* controllers.
    """
    if require_timezone is None:
        require_timezone = _env_truthy('SUMMARY_REQUIRE_TIMEZONE', default=False)
    if allow_legacy_fallback is None:
        allow_legacy_fallback = _env_truthy('SUMMARY_LEGACY_FALLBACK', default=False)

    expected_path = daily_summary_path(day=day, asset=asset, interval_sec=interval_sec, out_dir=out_dir)
    found_path = find_daily_summary_path(
        day=day,
        asset=asset,
        interval_sec=interval_sec,
        out_dir=out_dir,
        allow_legacy_fallback=allow_legacy_fallback,
    )

    meta: dict[str, Any] = {
        'status': 'missing',
        'path': str(expected_path),
        'source': 'missing',
        'strict': bool(not allow_legacy_fallback),
        'issues': [],
        'warnings': [],
        'identity_ok': False,
        'legacy_fallback_used': False,
        'expected_day': str(day),
        'expected_asset': None if asset is None else str(asset),
        'expected_interval_sec': None if interval_sec is None else int(interval_sec),
        'expected_timezone': '' if expected_timezone is None else str(expected_timezone),
    }

    if found_path is None:
        return None, None, meta

    meta['path'] = str(found_path)
    meta['legacy_fallback_used'] = str(found_path) != str(expected_path)
    meta['source'] = 'legacy_fallback' if meta['legacy_fallback_used'] else 'exact'

    loaded = load_json_file(found_path)
    if not isinstance(loaded, dict):
        meta['status'] = 'invalid'
        meta['source'] = 'invalid_json'
        meta['issues'] = ['invalid_json']
        return None, found_path, meta

    issues: list[str] = []
    warnings: list[str] = []

    got_day = str(loaded.get('day') or '').strip()
    if got_day:
        if got_day != str(day):
            issues.append(f'day_mismatch:{got_day}')
    else:
        issues.append('day_missing')

    if asset is not None:
        got_asset = str(loaded.get('asset') or '').strip()
        if got_asset:
            if got_asset != str(asset):
                issues.append(f'asset_mismatch:{got_asset}')
        else:
            issues.append('asset_missing')

    if interval_sec is not None:
        got_iv = loaded.get('interval_sec')
        if got_iv is None or str(got_iv).strip() == '':
            issues.append('interval_sec_missing')
        else:
            try:
                iv = int(str(got_iv).strip())
            except Exception:
                issues.append(f'interval_sec_invalid:{got_iv}')
            else:
                if iv != int(interval_sec):
                    issues.append(f'interval_sec_mismatch:{iv}')

    if expected_timezone:
        got_tz = str(loaded.get('timezone') or '').strip()
        if got_tz:
            if got_tz != str(expected_timezone):
                issues.append(f'timezone_mismatch:{got_tz}')
        elif require_timezone:
            issues.append('timezone_missing')
        else:
            warnings.append('timezone_missing')

    meta['warnings'] = warnings
    meta['issues'] = issues + warnings
    if issues:
        meta['status'] = 'invalid'
        meta['source'] = 'identity_mismatch'
        return None, found_path, meta

    meta['status'] = 'ok'
    meta['identity_ok'] = True
    return loaded, found_path, meta


def auto_params_filename(*, day: str | None = None, asset: str | None = None, interval_sec: int | None = None) -> str:
    suffix = ''
    if asset and interval_sec is not None:
        tag = sanitize_interval(interval_sec)
        if tag:
            suffix = f"_{sanitize_asset(asset)}_{tag}"
    elif asset:
        suffix = f"_{sanitize_asset(asset)}"

    if day:
        ymd = str(day).replace('-', '')
        return f"auto_params_{ymd}{suffix}.json"
    if suffix:
        return f"auto_params{suffix}.json"
    return 'auto_params.json'


def auto_params_path(*, day: str | None = None, asset: str | None = None, interval_sec: int | None = None, out_dir: str | Path = 'runs') -> Path:
    return Path(out_dir) / auto_params_filename(day=day, asset=asset, interval_sec=interval_sec)


def find_auto_params_path(*, asset: str | None = None, interval_sec: int | None = None, out_dir: str | Path = 'runs', allow_legacy_fallback: bool = True) -> Path | None:
    candidates: list[Path] = []
    if asset and interval_sec is not None:
        candidates.append(auto_params_path(day=None, asset=asset, interval_sec=interval_sec, out_dir=out_dir))
    if allow_legacy_fallback:
        candidates.append(auto_params_path(day=None, asset=None, interval_sec=None, out_dir=out_dir))
    for p in candidates:
        if p.exists():
            return p
    return None
