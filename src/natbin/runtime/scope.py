from __future__ import annotations

"""Centralized runtime scope/path helpers.

Package H reduces duplicated path and scope building across runtime helpers.
The goal is not to replace the scheduler yet, but to give Python-side tools a
single authoritative naming layer for sidecars and runtime artifacts.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..state.summary_paths import sanitize_asset, sanitize_interval, repo_asset, repo_interval_sec


@dataclass(frozen=True)
class RuntimeScope:
    asset: str
    interval_sec: int

    @property
    def asset_tag(self) -> str:
        return sanitize_asset(self.asset)

    @property
    def interval_tag(self) -> str:
        return f"{int(self.interval_sec)}s"

    @property
    def scope_tag(self) -> str:
        return f"{self.asset_tag}_{self.interval_tag}"


def repo_scope(*, config_path: str | Path | None = None, repo_root: str | Path | None = None, default_asset: str = 'UNKNOWN', default_interval_sec: int = 300) -> RuntimeScope:
    asset = repo_asset(config_path=config_path, repo_root=repo_root, default=default_asset)
    interval_sec = repo_interval_sec(config_path=config_path, repo_root=repo_root, default=default_interval_sec)
    return RuntimeScope(asset=str(asset), interval_sec=int(interval_sec))


def build_scope(asset: str, interval_sec: int) -> RuntimeScope:
    return RuntimeScope(asset=str(asset), interval_sec=int(interval_sec))


def sidecar_path(name: str, *, asset: str, interval_sec: int, out_dir: str | Path = 'runs', suffix: str = '.json') -> Path:
    scope = build_scope(asset, interval_sec)
    return Path(out_dir) / f"{name}_{scope.scope_tag}{suffix}"


def effective_env_path(*, asset: str, interval_sec: int, out_dir: str | Path = 'runs') -> Path:
    return sidecar_path('effective_env', asset=asset, interval_sec=interval_sec, out_dir=out_dir)


def market_context_path(*, asset: str, interval_sec: int, out_dir: str | Path = 'runs') -> Path:
    return sidecar_path('market_context', asset=asset, interval_sec=interval_sec, out_dir=out_dir)


def loop_status_path(*, asset: str, interval_sec: int, out_dir: str | Path = 'runs') -> Path:
    return sidecar_path('observe_loop_auto_status', asset=asset, interval_sec=interval_sec, out_dir=out_dir)


def transcript_log_path(*, day: str, asset: str, interval_sec: int, out_dir: str | Path = 'runs/logs') -> Path:
    scope = build_scope(asset, interval_sec)
    day_tag = str(day).replace('-', '')
    return Path(out_dir) / f"observe_loop_auto_{scope.scope_tag}_{day_tag}.log"


def live_signals_csv_path(*, day: str, asset: str, interval_sec: int, out_dir: str | Path = 'runs') -> Path:
    scope = build_scope(asset, interval_sec)
    day_tag = str(day).replace('-', '')
    return Path(out_dir) / f"live_signals_v2_{day_tag}_{scope.scope_tag}.csv"


def decision_latest_path(*, asset: str, interval_sec: int, out_dir: str | Path = 'runs') -> Path:
    scope = build_scope(asset, interval_sec)
    return Path(out_dir) / 'decisions' / f"decision_latest_{scope.scope_tag}.json"


def decision_snapshot_path(*, day: str, asset: str, interval_sec: int, ts: int, out_dir: str | Path = 'runs') -> Path:
    scope = build_scope(asset, interval_sec)
    day_tag = str(day).replace('-', '')
    return Path(out_dir) / 'decisions' / f"decision_{day_tag}_{scope.scope_tag}_{int(ts)}.json"


def incident_jsonl_path(*, day: str, asset: str, interval_sec: int, out_dir: str | Path = 'runs') -> Path:
    scope = build_scope(asset, interval_sec)
    day_tag = str(day).replace('-', '')
    return Path(out_dir) / 'incidents' / f"incidents_{day_tag}_{scope.scope_tag}.jsonl"



def health_snapshot_path(*, asset: str, interval_sec: int, out_dir: str | Path = 'runs') -> Path:
    scope = build_scope(asset, interval_sec)
    return Path(out_dir) / 'health' / f"health_latest_{scope.scope_tag}.json"

def latest_existing(paths: Iterable[Path]) -> Path | None:
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None
    return sorted(existing, key=lambda p: p.stat().st_mtime, reverse=True)[0]

def daemon_lock_path(*, asset: str, interval_sec: int, out_dir: str | Path = 'runs') -> Path:
    scope = build_scope(asset, interval_sec)
    return Path(out_dir) / f"runtime_daemon_{scope.scope_tag}.lock"

