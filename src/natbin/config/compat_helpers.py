from __future__ import annotations

import os
from typing import Any


def env_first(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        val = os.getenv(key)
        if val is not None and str(val).strip() != '':
            return str(val).strip()
    return default


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def safe_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return bool(value)
    s = str(value).strip().lower()
    if s in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    if s in {'0', 'false', 'no', 'n', 'off'}:
        return False
    return bool(default)


def safe_secret(value: Any, default: str = '') -> str:
    if value is None:
        return default
    try:
        getter = getattr(value, 'get_secret_value', None)
        if callable(getter):
            return str(getter())
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return default


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        dumper = getattr(value, 'model_dump', None)
        if callable(dumper):
            raw = dumper(mode='python')
            if isinstance(raw, dict):
                return dict(raw)
    except Exception:
        pass
    return {}


def portable_path_str(value: Any, default: str = '') -> str:
    if value is None:
        return str(default)
    try:
        as_posix = getattr(value, 'as_posix', None)
        if callable(as_posix):
            return str(as_posix())
    except Exception:
        pass
    try:
        text = str(value)
    except Exception:
        return str(default)
    return text.replace('\\', '/')


EXPECTED_BOUNDS_KEYS = ('vol_lo', 'vol_hi', 'bb_lo', 'bb_hi', 'atr_lo', 'atr_hi')


def bounds_dict(value: Any) -> dict[str, float]:
    raw = as_dict(value)
    out: dict[str, float] = {}
    for key in EXPECTED_BOUNDS_KEYS:
        if key not in raw:
            continue
        try:
            out[str(key)] = float(raw[key])
        except Exception:
            continue
    return out


def pull(obj: Any, *path: str, default: Any = None) -> Any:
    cur = obj
    for part in path:
        if cur is None:
            return default
        cur = getattr(cur, part, None)
    return default if cur is None else cur
