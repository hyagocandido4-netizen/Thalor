#!/usr/bin/env python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from natbin.runtime_scope import repo_scope, loop_status_path, decision_latest_path, daemon_lock_path
from natbin.runtime_hardening import inspect_runtime_freshness
from natbin.control.plan import build_context


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _latest(glob_pat: str, base: Path) -> Path | None:
    files = sorted(base.glob(glob_pat), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def main() -> None:
    runs = Path('runs')
    scope = repo_scope()
    status = loop_status_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=runs)
    decision = decision_latest_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=runs)
    lock_path = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=runs)
    if not status.exists():
        status = _latest('observe_loop_auto_status*.json', runs)
    if not decision.exists():
        decision = _latest('decisions/decision_latest_*.json', runs)

    guard = runs / 'control' / scope.scope_tag / 'guard.json'
    lifecycle = runs / 'control' / scope.scope_tag / 'lifecycle.json'

    freshness = None
    try:
        ctx = build_context(repo_root='.')
        freshness = inspect_runtime_freshness(repo_root='.', ctx=ctx).as_dict()
    except Exception:
        freshness = None

    out: dict[str, Any] = {
        'scope': {'asset': scope.asset, 'interval_sec': scope.interval_sec, 'scope_tag': scope.scope_tag},
        'status_path': str(status) if status else '',
        'decision_path': str(decision) if decision else '',
        'guard_path': str(guard),
        'lifecycle_path': str(lifecycle),
        'lock_path': str(lock_path),
        'status': _load_json(status) if status else None,
        'decision': _load_json(decision) if decision else None,
        'guard': _load_json(guard) if guard.exists() else None,
        'lifecycle': _load_json(lifecycle) if lifecycle.exists() else None,
        'freshness': freshness,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
