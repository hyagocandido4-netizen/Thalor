from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Iterable

from ..ops.lockfile import read_lock_info, refresh_lock
from ..state.control_repo import write_control_artifact
from .health import build_health_payload, build_status_payload, write_health_payload, write_status_payload
from .scope import daemon_lock_path


TRACKED_CANONICAL_ARTIFACTS: tuple[str, ...] = (
    'loop_status',
    'health',
    'precheck',
    'execution',
    'orders',
    'reconcile',
)

TRACKED_SIDECAR_PATH_KEYS: tuple[str, ...] = (
    'status',
    'health_legacy',
)


@dataclass(frozen=True)
class ArtifactFreshness:
    name: str
    path: str
    exists: bool
    age_sec: int | None
    stale_after_sec: int
    stale: bool
    at_utc: str | None = None
    state: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeHardeningReport:
    scope_tag: str
    checked_at_utc: str
    stale_after_sec: int
    lock: dict[str, Any]
    artifacts: list[ArtifactFreshness]
    stale_artifacts: list[ArtifactFreshness]
    actions: list[dict[str, Any]]
    mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            'scope_tag': self.scope_tag,
            'checked_at_utc': self.checked_at_utc,
            'stale_after_sec': self.stale_after_sec,
            'lock': self.lock,
            'artifacts': [a.as_dict() for a in self.artifacts],
            'stale_artifacts': [a.as_dict() for a in self.stale_artifacts],
            'actions': self.actions,
            'mode': self.mode,
        }


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _fmt_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec='seconds')


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _runtime_cfg(ctx) -> dict[str, Any]:
    raw = ctx.resolved_config.get('runtime') if isinstance(ctx.resolved_config, dict) else getattr(ctx.resolved_config, 'runtime', None)
    if raw is None:
        return {}
    if hasattr(raw, 'model_dump'):
        return raw.model_dump(mode='python')
    if isinstance(raw, dict):
        return raw
    try:
        return dict(raw)
    except Exception:
        return {}


def stale_artifact_after_sec(ctx) -> int:
    cfg = _runtime_cfg(ctx)
    raw = cfg.get('stale_artifact_after_sec')
    try:
        if raw is not None and int(raw) > 0:
            return int(raw)
    except Exception:
        pass
    interval = max(60, int(getattr(ctx.config, 'interval_sec', 0) or 0))
    return max(interval * 3, 600)


def startup_invalidate_enabled(ctx) -> bool:
    return bool(_runtime_cfg(ctx).get('startup_invalidate_stale_artifacts', True))


def lifecycle_artifacts_enabled(ctx) -> bool:
    return bool(_runtime_cfg(ctx).get('startup_lifecycle_artifacts', True))


def lock_refresh_enabled(ctx) -> bool:
    return bool(_runtime_cfg(ctx).get('lock_refresh_enable', True))


def _load_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _artifact_timestamp(path: Path, payload: dict[str, Any] | None) -> datetime | None:
    if isinstance(payload, dict):
        for key in ('at_utc', 'now_utc', 'finished_at_utc', 'started_at_utc', 'updated_at_utc', 'checked_at_utc'):
            dt = _parse_iso(payload.get(key))
            if dt is not None:
                return dt
    try:
        return datetime.fromtimestamp(float(path.stat().st_mtime), tz=UTC)
    except Exception:
        return None


def _artifact_state(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ('state', 'phase', 'event'):
        raw = payload.get(key)
        if raw is not None and str(raw).strip():
            return str(raw)
    return None


def _artifact_message(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get('message')
    return str(raw) if raw is not None and str(raw).strip() else None


def _already_invalidated(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    state = str(payload.get('state') or payload.get('phase') or '').lower()
    msg = str(payload.get('message') or '').lower()
    return state == 'stale' or 'stale_artifact_invalidated' in msg


def iter_tracked_artifacts(*, repo_root: str | Path, ctx) -> Iterable[tuple[str, Path]]:
    for name in TRACKED_CANONICAL_ARTIFACTS:
        p_raw = ctx.control_paths.get(name)
        if p_raw:
            yield name, Path(str(p_raw))
    for key in TRACKED_SIDECAR_PATH_KEYS:
        p_raw = ctx.scoped_paths.get(key)
        if p_raw:
            yield key, Path(str(p_raw))


def inspect_runtime_freshness(*, repo_root: str | Path, ctx, now_utc: datetime | None = None) -> RuntimeHardeningReport:
    now = now_utc or _utcnow()
    stale_after = stale_artifact_after_sec(ctx)
    lock_path = daemon_lock_path(asset=ctx.config.asset, interval_sec=int(ctx.config.interval_sec), out_dir=Path(repo_root).resolve() / 'runs')
    lock = read_lock_info(lock_path)
    artifacts: list[ArtifactFreshness] = []
    stale_artifacts: list[ArtifactFreshness] = []
    for name, path in iter_tracked_artifacts(repo_root=repo_root, ctx=ctx):
        payload = _load_json_dict(path) if path.exists() else None
        stamp = _artifact_timestamp(path, payload) if path.exists() else None
        age_sec = max(0, int((now - stamp).total_seconds())) if stamp is not None else None
        stale = bool(path.exists() and age_sec is not None and age_sec > stale_after and not _already_invalidated(payload))
        item = ArtifactFreshness(
            name=name,
            path=str(path),
            exists=bool(path.exists()),
            age_sec=age_sec,
            stale_after_sec=stale_after,
            stale=stale,
            at_utc=_fmt_utc(stamp),
            state=_artifact_state(payload),
            message=_artifact_message(payload),
        )
        artifacts.append(item)
        if stale:
            stale_artifacts.append(item)
    return RuntimeHardeningReport(
        scope_tag=str(ctx.scope.scope_tag),
        checked_at_utc=_fmt_utc(now) or '',
        stale_after_sec=stale_after,
        lock=lock,
        artifacts=artifacts,
        stale_artifacts=stale_artifacts,
        actions=[],
        mode='inspect',
    )


def _stale_common(*, ctx, item: ArtifactFreshness, now_utc: datetime) -> dict[str, Any]:
    return {
        'scope_tag': str(ctx.scope.scope_tag),
        'stale_detected_at_utc': _fmt_utc(now_utc),
        'stale_age_sec': item.age_sec,
        'stale_after_sec': item.stale_after_sec,
        'source_artifact': item.name,
        'source_path': item.path,
        'previous_at_utc': item.at_utc,
        'previous_state': item.state,
        'previous_message': item.message,
    }


def _invalidate_one(*, repo_root: Path, ctx, item: ArtifactFreshness, now_utc: datetime) -> dict[str, Any]:
    common = _stale_common(ctx=ctx, item=item, now_utc=now_utc)
    if item.name in {'loop_status', 'status'}:
        payload = build_status_payload(
            asset=str(ctx.config.asset),
            interval_sec=int(ctx.config.interval_sec),
            phase='stale',
            state='stale',
            message='stale_artifact_invalidated',
            next_wake_utc=None,
            sleep_reason='startup_guard',
            report=common,
            quota={},
            failsafe={},
            market_context={},
        )
        payload.update(common)
        if item.name == 'loop_status':
            write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status', payload=payload)
        else:
            write_status_payload(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, payload=payload, out_dir=repo_root / 'runs')
        return {'artifact': item.name, 'invalidated': True, 'state': 'stale', 'path': item.path}

    if item.name in {'health', 'health_legacy'}:
        payload = build_health_payload(
            asset=str(ctx.config.asset),
            interval_sec=int(ctx.config.interval_sec),
            state='stale',
            message='stale_artifact_invalidated',
            quota={},
            failsafe={},
            market_context={},
            last_cycle_ok=None,
        )
        payload.update(common)
        if item.name == 'health':
            write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='health', payload=payload)
        else:
            write_health_payload(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, payload=payload, out_dir=repo_root / 'runs')
        return {'artifact': item.name, 'invalidated': True, 'state': 'stale', 'path': item.path}

    payload = {
        'at_utc': _fmt_utc(now_utc),
        'phase': 'stale',
        'state': 'stale',
        'message': 'stale_artifact_invalidated',
        'asset': str(ctx.config.asset),
        'interval_sec': int(ctx.config.interval_sec),
    }
    payload.update(common)
    write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name=item.name, payload=payload)
    return {'artifact': item.name, 'invalidated': True, 'state': 'stale', 'path': item.path}


def invalidate_stale_runtime_artifacts(*, repo_root: str | Path, ctx, report: RuntimeHardeningReport, now_utc: datetime | None = None) -> list[dict[str, Any]]:
    now = now_utc or _utcnow()
    root = Path(repo_root).resolve()
    actions: list[dict[str, Any]] = []
    for item in report.stale_artifacts:
        try:
            actions.append(_invalidate_one(repo_root=root, ctx=ctx, item=item, now_utc=now))
        except Exception as exc:
            actions.append({'artifact': item.name, 'invalidated': False, 'error': f'{type(exc).__name__}:{exc}', 'path': item.path})
    return actions


def write_runtime_lifecycle(*, repo_root: str | Path, ctx, event: str, payload: dict[str, Any] | None = None, now_utc: datetime | None = None) -> dict[str, Any]:
    now = now_utc or _utcnow()
    body: dict[str, Any] = {
        'at_utc': _fmt_utc(now),
        'event': str(event),
        'scope': {
            'asset': str(ctx.config.asset),
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': str(ctx.scope.scope_tag),
            'timezone': str(ctx.config.timezone),
        },
    }
    if isinstance(payload, dict):
        body.update(payload)
    write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='lifecycle', payload=body)
    return body


def refresh_runtime_lock(*, lock_path: Path, ctx, owner: dict[str, Any] | None = None) -> bool:
    if not lock_refresh_enabled(ctx):
        return False
    return refresh_lock(lock_path, owner=owner)


def startup_sanitize_runtime(*, repo_root: str | Path, ctx, mode: str, lock_path: Path, owner: dict[str, Any] | None = None, now_utc: datetime | None = None) -> dict[str, Any]:
    now = now_utc or _utcnow()
    if owner is not None:
        refresh_runtime_lock(lock_path=lock_path, ctx=ctx, owner=owner)
    inspected = inspect_runtime_freshness(repo_root=repo_root, ctx=ctx, now_utc=now)
    actions: list[dict[str, Any]] = []
    if startup_invalidate_enabled(ctx):
        actions = invalidate_stale_runtime_artifacts(repo_root=repo_root, ctx=ctx, report=inspected, now_utc=now)
    payload = inspected.as_dict()
    payload['actions'] = actions
    payload['mode'] = str(mode)
    payload['sanitized'] = True
    write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='guard', payload=payload)
    if lifecycle_artifacts_enabled(ctx):
        write_runtime_lifecycle(
            repo_root=repo_root,
            ctx=ctx,
            event='startup',
            payload={
                'mode': str(mode),
                'lock_path': str(lock_path),
                'guard': {
                    'checked_at_utc': payload.get('checked_at_utc'),
                    'stale_after_sec': payload.get('stale_after_sec'),
                    'stale_artifact_count': len(payload.get('stale_artifacts') or []),
                    'actions_count': len(actions),
                },
            },
            now_utc=now,
        )
    return payload
