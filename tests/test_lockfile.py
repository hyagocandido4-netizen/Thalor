from __future__ import annotations

import json
from pathlib import Path

from natbin.ops.lockfile import acquire_lock, release_lock, read_lock_info


def test_lock_acquire_and_release(tmp_path: Path):
    p = tmp_path / "a.lock"
    r = acquire_lock(p)
    assert r.acquired
    assert p.exists()
    info = read_lock_info(p)
    assert info["exists"] is True
    release_lock(p)
    assert not p.exists()


def test_stale_lock_is_removed(tmp_path: Path):
    p = tmp_path / "b.lock"
    p.write_text(json.dumps({"pid": 999999, "created_at_utc": "2026-03-09T00:00:00+00:00"}), encoding="utf-8")
    r = acquire_lock(p)
    assert r.acquired
    assert r.stale_removed is True
    release_lock(p)
