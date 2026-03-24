from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .compat_helpers import env_first, safe_int
from .legacy_surface import (
    fallback_legacy_payload,
    legacy_payload_to_env_map,
    resolved_to_legacy_payload,
)


@dataclass(frozen=True)
class RuntimeScopeCompat:
    asset: str
    interval_sec: int
    timezone: str


__all__ = [
    'RuntimeScopeCompat',
    'runtime_scope_from_resolved',
    'resolved_to_legacy_dict',
    'resolved_to_legacy_env_map',
    'load_runtime_resolved_config',
    'load_legacy_compatible_config',
    'apply_resolved_to_environment',
    'dump_compat_debug_json',
]


def _resolve_from_new_loader(
    asset: str | None = None,
    interval_sec: int | None = None,
    profile: str | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> Any | None:
    """Resolve config via the modern loader.

    We only fall back to the legacy env-shaped dict when the modern loader is
    genuinely unavailable (for example, during a partial bootstrap). Schema
    validation or runtime resolution errors must propagate so the caller fails
    closed instead of silently discarding modern blocks such as
    ``security``, ``notifications`` and ``intelligence``.
    """
    try:
        from natbin.config.loader import load_resolved_config  # type: ignore
    except Exception:
        return None

    kwargs: dict[str, Any] = {}
    if asset is not None:
        kwargs['asset'] = asset
    if interval_sec is not None:
        kwargs['interval_sec'] = int(interval_sec)
    if profile is not None:
        kwargs['profile'] = profile
    if cli_overrides:
        kwargs['cli_overrides'] = dict(cli_overrides)

    try:
        return load_resolved_config(**kwargs)
    except TypeError as exc:
        message = str(exc)
        if 'unexpected keyword argument' not in message and 'positional argument' not in message:
            raise
        return load_resolved_config(asset=asset, interval_sec=interval_sec)


def runtime_scope_from_resolved(resolved: Any) -> RuntimeScopeCompat:
    asset = getattr(resolved, 'asset', None) or env_first('ASSET', default='EURUSD-OTC')
    interval_sec = getattr(resolved, 'interval_sec', None) or safe_int(env_first('INTERVAL_SEC', default='300'), 300)
    timezone = getattr(resolved, 'timezone', None) or env_first('TIMEZONE', 'TZ', default='America/Sao_Paulo')
    return RuntimeScopeCompat(asset=str(asset), interval_sec=int(interval_sec), timezone=str(timezone))


def resolved_to_legacy_dict(resolved: Any) -> dict[str, Any]:
    return resolved_to_legacy_payload(resolved)


def load_runtime_resolved_config(
    asset: str | None = None,
    interval_sec: int | None = None,
    profile: str | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> Any:
    resolved = _resolve_from_new_loader(asset=asset, interval_sec=interval_sec, profile=profile, cli_overrides=cli_overrides)
    if resolved is not None:
        return resolved
    return fallback_legacy_payload()


def load_legacy_compatible_config(
    asset: str | None = None,
    interval_sec: int | None = None,
    profile: str | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = load_runtime_resolved_config(asset=asset, interval_sec=interval_sec, profile=profile, cli_overrides=cli_overrides)
    if isinstance(resolved, dict):
        return dict(resolved)
    return resolved_to_legacy_dict(resolved)


def resolved_to_legacy_env_map(resolved_or_legacy: Any) -> dict[str, str]:
    payload = dict(resolved_or_legacy) if isinstance(resolved_or_legacy, dict) else resolved_to_legacy_dict(resolved_or_legacy)
    return legacy_payload_to_env_map(payload)


def apply_resolved_to_environment(resolved_or_legacy: Any) -> dict[str, str]:
    mapping = resolved_to_legacy_env_map(resolved_or_legacy)
    for key, value in mapping.items():
        os.environ[key] = value
    return mapping


def dump_compat_debug_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False, default=str), encoding='utf-8')
