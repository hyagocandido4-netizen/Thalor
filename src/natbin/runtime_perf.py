from __future__ import annotations

"""Small performance/scalability helpers for runtime-heavy paths.

Package H keeps behaviour stable while reducing avoidable IO/recompute inside a
single process. All helpers are intentionally conservative and fail-open to the
old behaviour if something unexpected happens.
"""

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
import sqlite3


@dataclass(frozen=True)
class FileFingerprint:
    path: str
    mtime_ns: int
    size: int


_JSON_CACHE: dict[str, tuple[FileFingerprint, Any]] = {}


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    s = str(raw).strip().lower()
    if s == '':
        return bool(default)
    return s not in {'0', 'false', 'f', 'no', 'n', 'off'}


def _fingerprint(path: Path) -> FileFingerprint:
    st = path.stat()
    return FileFingerprint(path=str(path.resolve()), mtime_ns=int(getattr(st, 'st_mtime_ns', int(st.st_mtime * 1e9))), size=int(st.st_size))


def apply_runtime_sqlite_pragmas(con: sqlite3.Connection) -> None:
    """Apply low-risk pragmas for runtime repos.

    These are intentionally conservative: WAL + NORMAL + busy_timeout improve
    concurrent runtime behaviour without changing logical semantics.
    """
    if not _truthy('RUNTIME_SQLITE_PRAGMAS', default=True):
        return
    busy_ms = os.getenv('RUNTIME_SQLITE_BUSY_TIMEOUT_MS', '5000').strip() or '5000'
    try:
        con.execute(f'PRAGMA busy_timeout={int(float(busy_ms))}')
    except Exception:
        pass
    for sql in (
        'PRAGMA journal_mode=WAL',
        'PRAGMA synchronous=NORMAL',
        'PRAGMA temp_store=MEMORY',
        'PRAGMA foreign_keys=ON',
    ):
        try:
            con.execute(sql)
        except Exception:
            pass


def load_json_cached(path: str | Path) -> Any | None:
    p = Path(path)
    if not p.exists():
        return None
    if not _truthy('RUNTIME_JSON_CACHE_ENABLE', default=True):
        try:
            return json.loads(p.read_text(encoding='utf-8', errors='replace'))
        except Exception:
            return None
    try:
        fp = _fingerprint(p)
    except Exception:
        return None
    key = fp.path
    cached = _JSON_CACHE.get(key)
    if cached is not None:
        cached_fp, cached_obj = cached
        if cached_fp == fp:
            return deepcopy(cached_obj)
    try:
        obj = json.loads(p.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return None
    _JSON_CACHE[key] = (fp, obj)
    return deepcopy(obj)


def write_text_if_changed(path: str | Path, text: str, *, encoding: str = 'utf-8') -> bool:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        if p.exists():
            current = p.read_text(encoding=encoding, errors='replace')
            if current == text:
                return False
    except Exception:
        pass
    p.write_text(text, encoding=encoding)
    return True
