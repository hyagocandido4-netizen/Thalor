from __future__ import annotations

from typing import Any

from ...runtime.observer_surface import ObserverConfigSurface, load_observer_cfg, resolve_observer_surface


def load_cfg(
    *,
    repo_root: str | None = None,
    config_path: str | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return load_observer_cfg(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
    )


__all__ = ['ObserverConfigSurface', 'load_cfg', 'resolve_observer_surface']
