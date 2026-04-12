from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..runtime.perf import load_json_cached


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def parse_iso(raw: Any) -> datetime | None:
    if raw in (None, ''):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def age_sec_from_dt(dt: datetime | None, *, now: datetime | None = None) -> float | None:
    if dt is None:
        return None
    return max(0.0, ((now or now_utc()) - dt).total_seconds())


def file_age_sec(path: Path, *, now: datetime | None = None) -> float | None:
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except Exception:
        return None
    return age_sec_from_dt(stamp, now=now)


def read_jsonish(path: Path) -> dict[str, Any] | None:
    obj = load_json_cached(str(path))
    return obj if isinstance(obj, dict) else None


def first_present(mapping: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ''):
            return mapping.get(key)
    return None


@dataclass(frozen=True)
class ArtifactStatus:
    name: str
    path: str
    exists: bool
    readable: bool
    fresh: bool | None
    age_sec: float | None
    max_age_sec: int | None
    status: str
    message: str
    payload_at_utc: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_artifact(
    *,
    name: str,
    path: str | Path | None,
    required: bool = True,
    max_age_sec: int | None = None,
    timestamp_keys: Sequence[str] = ('at_utc', 'generated_at_utc', 'updated_at_utc', 'finished_at_utc', 'checked_at_utc'),
    now: datetime | None = None,
) -> ArtifactStatus:
    current = now or now_utc()
    if path in (None, ''):
        return ArtifactStatus(
            name=name,
            path=str(path or ''),
            exists=False,
            readable=False,
            fresh=False if required else None,
            age_sec=None,
            max_age_sec=max_age_sec,
            status='error' if required else 'warn',
            message='Artifact path ausente' if required else 'Artifact opcional sem path',
        )
    resolved = Path(path)
    if not resolved.exists():
        return ArtifactStatus(
            name=name,
            path=str(resolved),
            exists=False,
            readable=False,
            fresh=False if required else None,
            age_sec=None,
            max_age_sec=max_age_sec,
            status='error' if required else 'warn',
            message='Artifact ausente' if required else 'Artifact opcional ausente',
        )
    payload = read_jsonish(resolved)
    readable = payload is not None or resolved.is_file()
    payload_stamp_raw = first_present(payload or {}, list(timestamp_keys)) if isinstance(payload, dict) else None
    payload_stamp = parse_iso(payload_stamp_raw)
    age_sec = age_sec_from_dt(payload_stamp, now=current)
    if age_sec is None:
        age_sec = file_age_sec(resolved, now=current)
    fresh: bool | None = None
    if max_age_sec is not None and age_sec is not None:
        fresh = age_sec <= float(max_age_sec)
    if not readable:
        status = 'error' if required else 'warn'
        msg = 'Artifact ilegível' if required else 'Artifact opcional ilegível'
    elif fresh is False:
        status = 'error' if required else 'warn'
        msg = f'Artifact stale ({round(float(age_sec or 0), 3)}s > {int(max_age_sec)}s)'
    else:
        status = 'ok'
        msg = 'Artifact presente e fresco' if fresh in {True, None} else 'Artifact presente'
    return ArtifactStatus(
        name=name,
        path=str(resolved),
        exists=True,
        readable=bool(readable),
        fresh=fresh,
        age_sec=None if age_sec is None else round(float(age_sec), 3),
        max_age_sec=max_age_sec,
        status=status,
        message=msg,
        payload_at_utc=str(payload_stamp_raw) if payload_stamp_raw not in (None, '') else None,
    )


def sqlite_open(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def sqlite_tables(path: Path) -> list[str]:
    con = sqlite_open(path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return [str(row[0]) for row in rows]
    finally:
        con.close()


def sqlite_quick_check(path: Path) -> str:
    con = sqlite_open(path)
    try:
        row = con.execute('PRAGMA quick_check').fetchone()
        return str(row[0]) if row else 'unknown'
    finally:
        con.close()


def sqlite_count(path: Path, table: str) -> int | None:
    con = sqlite_open(path)
    try:
        row = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None
    finally:
        con.close()


def count_nonempty_lines(path: Path) -> int | None:
    try:
        return sum(1 for line in path.read_text(encoding='utf-8', errors='replace').splitlines() if line.strip())
    except Exception:
        return None


def summarize_status(items: Iterable[dict[str, Any]]) -> str:
    statuses = [str(item.get('status') or 'ok') for item in items]
    if any(s == 'error' for s in statuses):
        return 'error'
    if any(s == 'warn' for s in statuses):
        return 'warn'
    return 'ok'


def safe_import(name: str) -> tuple[bool, str | None]:
    try:
        __import__(name)
        return True, None
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'

