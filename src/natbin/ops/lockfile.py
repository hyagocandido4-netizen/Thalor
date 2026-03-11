from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LockAcquireResult:
    acquired: bool
    lock_path: str
    pid: int | None = None
    age_sec: int | None = None
    stale_removed: bool = False
    detail: str | None = None

    def __bool__(self) -> bool:  # pragma: no cover
        """Backwards-compatible truthiness.

        Several legacy helpers treated lock acquisition as a boolean.
        Returning a richer result object is useful for diagnostics, but we
        keep ``if acquire_lock(...):`` semantics by mapping truthiness to
        the ``acquired`` field.
        """

        return bool(self.acquired)


_JSON_TS_KEYS = ('heartbeat_at_utc', 'created_at_utc')


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _dt_from_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _pid_is_running(pid: int) -> bool:
    """Best-effort check whether a PID is still running.

    Cross-platform (Windows + POSIX) without extra dependencies.

    Note: PID reuse is possible on all OSes. We treat a running PID as an
    active lock holder to be conservative.
    """

    if pid <= 0:
        return False

    if os.name == 'nt':
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
            if handle == 0:
                err = int(ctypes.windll.kernel32.GetLastError())
                return err == 5
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True

    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True
    return True


def _read_lock_text(lock_path: Path) -> str:
    try:
        return lock_path.read_text(encoding='utf-8', errors='ignore').strip()
    except Exception:
        return ''


def _parse_lock_object(lock_path: Path) -> dict[str, Any] | None:
    raw = _read_lock_text(lock_path)
    if not raw:
        return None
    if raw.startswith('{'):
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    try:
        pid = int(raw.split()[0])
    except Exception:
        return None
    return {'pid': pid}


def _lock_timestamp(obj: dict[str, Any] | None) -> datetime | None:
    if not isinstance(obj, dict):
        return None
    for key in _JSON_TS_KEYS:
        dt = _dt_from_iso(obj.get(key))
        if dt is not None:
            return dt
    return None


def _parse_lock_payload(lock_path: Path) -> tuple[int | None, datetime | None]:
    """Parse lock payload.

    Accepts either JSON (preferred) or legacy plain PID string.
    Returns ``(pid, created_or_heartbeat_utc)``.
    """

    obj = _parse_lock_object(lock_path)
    if not isinstance(obj, dict):
        return None, None
    pid = obj.get('pid')
    try:
        pid_i = int(pid) if pid is not None else None
    except Exception:
        pid_i = None
    return pid_i, _lock_timestamp(obj)


def _lock_payload(*, owner: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _utc_now().isoformat(timespec='seconds')
    payload: dict[str, Any] = {
        'pid': int(os.getpid()),
        'created_at_utc': now,
        'heartbeat_at_utc': now,
    }
    if isinstance(owner, dict):
        for key, value in owner.items():
            if value is None:
                continue
            if isinstance(value, Path):
                payload[str(key)] = str(value)
            else:
                payload[str(key)] = value
    return payload


def acquire_lock(lock_path: Path, *, force: bool = False, owner: dict[str, Any] | None = None) -> LockAcquireResult:
    """Acquire an exclusive lock file.

    Behavior:
    - Creates the lock file atomically (``O_EXCL``).
    - If it already exists:
        * If PID inside is not running anymore -> remove as stale and retry.
        * Otherwise -> refuse.
    - If ``force=True``, removes the lock file before attempting to acquire.
    """

    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if force:
        try:
            lock_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            try:
                if lock_path.exists():
                    lock_path.unlink()
            except Exception:
                pass

    payload = _lock_payload(owner=owner)

    def _try_create() -> bool:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(payload, ensure_ascii=False))
        return True

    try:
        _try_create()
        return LockAcquireResult(acquired=True, lock_path=str(lock_path), pid=int(os.getpid()), age_sec=0, stale_removed=False)
    except FileExistsError:
        pid, stamp = _parse_lock_payload(lock_path)
        now = _utc_now()
        age_sec = None
        try:
            st = lock_path.stat()
            mtime = datetime.fromtimestamp(float(st.st_mtime), tz=UTC)
            age_sec = max(0, int((now - mtime).total_seconds()))
        except Exception:
            age_sec = None

        if pid is not None and not _pid_is_running(pid):
            try:
                lock_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                try:
                    if lock_path.exists():
                        lock_path.unlink()
                except Exception:
                    pass
            try:
                _try_create()
                return LockAcquireResult(
                    acquired=True,
                    lock_path=str(lock_path),
                    pid=int(os.getpid()),
                    age_sec=0,
                    stale_removed=True,
                    detail=f'stale_lock_removed:pid={pid}',
                )
            except Exception as e:
                return LockAcquireResult(
                    acquired=False,
                    lock_path=str(lock_path),
                    pid=pid,
                    age_sec=age_sec,
                    stale_removed=True,
                    detail=f'stale_lock_remove_failed:{type(e).__name__}',
                )

        stamp_iso = stamp.isoformat(timespec='seconds') if isinstance(stamp, datetime) else None
        detail = f'held_by_pid:{pid}' if pid is not None else 'held_by_unknown_pid'
        if stamp_iso:
            detail += f':heartbeat_at_utc={stamp_iso}'
        return LockAcquireResult(acquired=False, lock_path=str(lock_path), pid=pid, age_sec=age_sec, stale_removed=False, detail=detail)
    except Exception as e:
        return LockAcquireResult(acquired=False, lock_path=str(lock_path), pid=None, age_sec=None, stale_removed=False, detail=f'acquire_failed:{type(e).__name__}')


def refresh_lock(lock_path: Path, *, owner: dict[str, Any] | None = None) -> bool:
    """Refresh a lock heartbeat in-place for the current PID.

    Returns ``True`` on success. When the file does not exist, belongs to a
    different PID or cannot be updated, returns ``False``.
    """

    obj = _parse_lock_object(lock_path)
    if not isinstance(obj, dict):
        return False
    pid = obj.get('pid')
    try:
        pid_i = int(pid) if pid is not None else None
    except Exception:
        pid_i = None
    if pid_i is not None and pid_i != int(os.getpid()):
        return False

    obj.setdefault('pid', int(os.getpid()))
    obj.setdefault('created_at_utc', _utc_now().isoformat(timespec='seconds'))
    obj['heartbeat_at_utc'] = _utc_now().isoformat(timespec='seconds')
    if isinstance(owner, dict):
        for key, value in owner.items():
            if value is None:
                continue
            obj[str(key)] = str(value) if isinstance(value, Path) else value
    try:
        lock_path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
        return True
    except Exception:
        return False


def release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        try:
            if lock_path.exists():
                lock_path.unlink()
        except Exception:
            pass


def read_lock_info(lock_path: Path) -> dict[str, Any]:
    """Best-effort read of lock file for diagnostics."""

    obj = _parse_lock_object(lock_path)
    pid, stamp = _parse_lock_payload(lock_path)
    heartbeat = _dt_from_iso((obj or {}).get('heartbeat_at_utc')) if isinstance(obj, dict) else None
    created_at = _dt_from_iso((obj or {}).get('created_at_utc')) if isinstance(obj, dict) else None
    info: dict[str, Any] = {
        'lock_path': str(lock_path),
        'exists': bool(lock_path.exists()),
        'pid': pid,
        'created_at_utc': created_at.isoformat(timespec='seconds') if isinstance(created_at, datetime) else None,
        'heartbeat_at_utc': heartbeat.isoformat(timespec='seconds') if isinstance(heartbeat, datetime) else None,
        'stamp_utc': stamp.isoformat(timespec='seconds') if isinstance(stamp, datetime) else None,
    }
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {'pid', 'created_at_utc', 'heartbeat_at_utc'}:
                continue
            info[str(key)] = value
    try:
        st = lock_path.stat()
        mtime = datetime.fromtimestamp(float(st.st_mtime), tz=UTC)
        info['mtime_utc'] = mtime.isoformat(timespec='seconds')
        info['size_bytes'] = int(st.st_size)
        info['age_sec'] = max(0, int((_utc_now() - mtime).total_seconds()))
    except Exception:
        pass
    if heartbeat is not None:
        info['heartbeat_age_sec'] = max(0, int((_utc_now() - heartbeat).total_seconds()))
    if created_at is not None:
        info['created_age_sec'] = max(0, int((_utc_now() - created_at).total_seconds()))
    if pid is not None:
        info['pid_running'] = bool(_pid_is_running(pid))
    return info
