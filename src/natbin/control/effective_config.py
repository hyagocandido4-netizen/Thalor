from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config.effective_dump import write_effective_config_latest, write_effective_config_snapshot
from ..state.control_repo import write_control_artifact


@dataclass(frozen=True)
class EffectiveConfigArtifacts:
    generated_at_utc: str
    cycle_id: str
    latest_path: str
    snapshot_path: str | None
    control_path: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _default_cycle_id(now: datetime | None = None) -> str:
    ref = now or _utc_now()
    return ref.strftime('%H%M%S')


def emit_effective_config_artifacts(
    *,
    repo_root: str | Path,
    config_path: str | Path,
    rcfg,
    scope,
    source_trace: list[str] | None = None,
    dump_snapshot: bool = True,
    cycle_id: str | None = None,
) -> EffectiveConfigArtifacts:
    root = Path(repo_root).resolve()
    now = _utc_now()
    cid = str(cycle_id or _default_cycle_id(now))
    latest_path = write_effective_config_latest(rcfg, repo_root=root)
    snapshot_path = None
    if dump_snapshot:
        day = now.astimezone(UTC).date().isoformat()
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(str(getattr(rcfg, 'timezone', 'UTC') or 'UTC'))
            day = now.astimezone(tz).date().isoformat()
        except Exception:
            pass
        snapshot_path = write_effective_config_snapshot(rcfg, repo_root=root, day=day, cycle_id=cid)

    payload = {
        'generated_at_utc': now.isoformat(timespec='seconds'),
        'cycle_id': cid,
        'repo_root': str(root),
        'config_path': str(Path(config_path).resolve()),
        'scope': asdict(scope),
        'source_trace': list(source_trace or []),
        'resolved_config': rcfg.as_dict(),
        'latest_path': str(latest_path),
        'snapshot_path': str(snapshot_path) if snapshot_path is not None else None,
    }
    control_path = write_control_artifact(
        repo_root=root,
        asset=scope.asset,
        interval_sec=scope.interval_sec,
        name='effective_config',
        payload=payload,
    )
    return EffectiveConfigArtifacts(
        generated_at_utc=payload['generated_at_utc'],
        cycle_id=cid,
        latest_path=str(latest_path),
        snapshot_path=str(snapshot_path) if snapshot_path is not None else None,
        control_path=str(control_path),
    )
