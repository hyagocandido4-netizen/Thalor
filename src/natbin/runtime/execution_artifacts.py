from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..state.control_repo import write_control_artifact



def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None



def write_execution_artifacts(*, repo_root: str | Path, ctx, orders_payload: dict[str, Any] | None = None, reconcile_payload: dict[str, Any] | None = None) -> None:
    if orders_payload is not None:
        write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='orders', payload=orders_payload)
        write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='execution', payload=orders_payload)
    if reconcile_payload is not None:
        write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='reconcile', payload=reconcile_payload)


__all__ = ['read_json', 'write_execution_artifacts']
