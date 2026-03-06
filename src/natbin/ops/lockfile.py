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
        keep `if acquire_lock(...):` semantics by mapping truthiness to
        the `acquired` field.
        """

        return bool(self.acquired)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _pid_is_running(pid: int) -> bool:
    """Best-effort check whether a PID is still running.

    Cross-platform (Windows + POSIX) without extra dependencies.

    Note: PID reuse is possible on all OSes. We treat a running PID as an
    active lock holder to be conservative.
    """

    if pid <= 0:
        return False

    # Windows
    if os.name == 'nt':
        try:
            import ctypes  # stdlib

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
            if handle == 0:
                # If access is denied, the process likely exists but we cannot query it.
                err = int(ctypes.windll.kernel32.GetLastError())
                return err == 5
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            # Fail closed: assume running.
            return True

    # POSIX
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        # Fail closed: assume running.
        return True
    return True


def _parse_lock_payload(lock_path: Path) -> tuple[int | None, datetime | None]:
    """Parse lock payload.

    Accepts either JSON (preferred) or legacy plain PID string.
    Returns (pid, created_at_utc).
    """

    try:
        raw = lock_path.read_text(encoding='utf-8', errors='ignore').strip()
    except Exception:
        return None, None

    if not raw:
        return None, None

    if raw.startswith('{'):
        try:
            obj = json.loads(raw)
            pid = obj.get('pid')
            created = obj.get('created_at_utc')
            pid_i = int(pid) if pid is not None else None
            created_dt = None
            if isinstance(created, str) and created.strip():
                try:
                    created_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=UTC)
                    created_dt = created_dt.astimezone(UTC)
                except Exception:
                    created_dt = None
            return pid_i, created_dt
        except Exception:
            # Fall back to legacy parsing.
            pass

    # Legacy: raw = "<pid>" (maybe with whitespace)
    try:
        pid_i = int(raw.split()[0])
        return pid_i, None
    except Exception:
        return None, None


def acquire_lock(lock_path: Path, *, force: bool = False) -> LockAcquireResult:
    """Acquire an exclusive lock file.

    Behavior:
    - Creates the lock file atomically (O_EXCL).
    - If it already exists:
        * If PID inside is not running anymore -> remove as stale and retry.
        * Otherwise -> refuse.
    - If `force=True`, removes the lock file before attempting to acquire.
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

    def _try_create() -> bool:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(
                json.dumps(
                    {
                        'pid': int(os.getpid()),
                        'created_at_utc': _utc_now().isoformat(timespec='seconds'),
                    },
                    ensure_ascii=False,
                )
            )
        return True

    try:
        _try_create()
        return LockAcquireResult(acquired=True, lock_path=str(lock_path), pid=int(os.getpid()), age_sec=0, stale_removed=False)
    except FileExistsError:
        pid, created_at = _parse_lock_payload(lock_path)
        now = _utc_now()
        # Prefer filesystem mtime for age.
        age_sec = None
        try:
            st = lock_path.stat()
            mtime = datetime.fromtimestamp(float(st.st_mtime), tz=UTC)
            age_sec = max(0, int((now - mtime).total_seconds()))
        except Exception:
            age_sec = None

        # If we can prove the old PID is dead, remove and retry once.
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

        created_at_iso = created_at.isoformat(timespec='seconds') if isinstance(created_at, datetime) else None
        detail = f'held_by_pid:{pid}' if pid is not None else 'held_by_unknown_pid'
        if created_at_iso:
            detail += f':created_at_utc={created_at_iso}'
        return LockAcquireResult(acquired=False, lock_path=str(lock_path), pid=pid, age_sec=age_sec, stale_removed=False, detail=detail)
    except Exception as e:
        return LockAcquireResult(acquired=False, lock_path=str(lock_path), pid=None, age_sec=None, stale_removed=False, detail=f'acquire_failed:{type(e).__name__}')


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

    pid, created_at = _parse_lock_payload(lock_path)
    info: dict[str, Any] = {
        'lock_path': str(lock_path),
        'exists': bool(lock_path.exists()),
        'pid': pid,
        'created_at_utc': created_at.isoformat(timespec='seconds') if isinstance(created_at, datetime) else None,
    }
    try:
        st = lock_path.stat()
        info['mtime_utc'] = datetime.fromtimestamp(float(st.st_mtime), tz=UTC).isoformat(timespec='seconds')
        info['size_bytes'] = int(st.st_size)
    except Exception:
        pass
    if pid is not None:
        info['pid_running'] = bool(_pid_is_running(pid))
    return info
