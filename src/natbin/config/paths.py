from __future__ import annotations

"""Path-resolution helpers for the typed configuration layer.

The config foundation must be able to resolve files relative to an explicit
``repo_root`` instead of the current working directory. This module centralizes
that logic so runtime_app / runtime_daemon / quota helpers all agree on the
same semantics.
"""

import os
from pathlib import Path

CONFIG_LIVE_PRACTICE_REL = Path("config") / "live_controlled_practice.yaml"
CONFIG_LIVE_REAL_REL = Path("config") / "live_controlled_real.yaml"
CONFIG_BASE_REL = Path("config") / "base.yaml"
CONFIG_LEGACY_REL = Path("config.yaml")
ENV_REL = Path(".env")


def _infer_repo_root_from_config_path(config_path: Path) -> Path:
    path = Path(config_path).resolve()
    if path.name == "base.yaml" and path.parent.name == "config":
        return path.parent.parent.resolve()
    return path.parent.resolve()


def resolve_repo_root(*, repo_root: str | Path | None = None, config_path: str | Path | None = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    env_root = os.getenv('THALOR_REPO_ROOT')
    if env_root is not None and str(env_root).strip() != '':
        return Path(str(env_root).strip()).resolve()
    if config_path is not None:
        p = Path(config_path)
        if p.is_absolute():
            return _infer_repo_root_from_config_path(p)
    return Path('.').resolve()


def resolve_config_path(*, repo_root: str | Path | None = None, config_path: str | Path | None = None) -> Path:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    if config_path is None:
        preferred = [
            (root / CONFIG_LIVE_PRACTICE_REL).resolve(),
            (root / CONFIG_LIVE_REAL_REL).resolve(),
        ]
        for candidate in preferred:
            if candidate.exists():
                return candidate
        env_cfg = os.getenv('THALOR_CONFIG_PATH') or os.getenv('THALOR_CONFIG')
        if env_cfg is not None and str(env_cfg).strip() != '':
            p = Path(str(env_cfg).strip())
            if p.is_absolute():
                return p.resolve()
            return (root / p).resolve()
        modern = (root / CONFIG_BASE_REL).resolve()
        if modern.exists():
            return modern
        return (root / CONFIG_LEGACY_REL).resolve()
    path = Path(config_path)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def resolve_env_path(*, repo_root: str | Path | None = None, env_path: str | Path | None = None, config_path: str | Path | None = None) -> Path:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    if env_path is None:
        return (root / ENV_REL).resolve()
    path = Path(env_path)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()
