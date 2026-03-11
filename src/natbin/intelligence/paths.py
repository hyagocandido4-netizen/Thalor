
from __future__ import annotations

from pathlib import Path

from ..runtime.scope import build_scope


def intelligence_scope_dir(*, repo_root: str | Path, scope_tag: str, artifact_dir: str | Path = 'runs/intelligence') -> Path:
    root = Path(repo_root).resolve()
    base = Path(artifact_dir)
    if not base.is_absolute():
        base = root / base
    path = base / str(scope_tag)
    path.mkdir(parents=True, exist_ok=True)
    return path


def pack_path(*, repo_root: str | Path, scope_tag: str, artifact_dir: str | Path = 'runs/intelligence') -> Path:
    return intelligence_scope_dir(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir) / 'pack.json'


def latest_eval_path(*, repo_root: str | Path, scope_tag: str, artifact_dir: str | Path = 'runs/intelligence') -> Path:
    return intelligence_scope_dir(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir) / 'latest_eval.json'


def drift_state_path(*, repo_root: str | Path, scope_tag: str, artifact_dir: str | Path = 'runs/intelligence') -> Path:
    return intelligence_scope_dir(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir) / 'drift_state.json'


def retrain_trigger_path(*, repo_root: str | Path, scope_tag: str, artifact_dir: str | Path = 'runs/intelligence') -> Path:
    return intelligence_scope_dir(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir) / 'retrain_trigger.json'


def default_scope_tag(asset: str, interval_sec: int) -> str:
    return build_scope(asset=str(asset), interval_sec=int(interval_sec)).scope_tag
