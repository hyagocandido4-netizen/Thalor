from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..incidents.reporting import incident_status_payload
from .production_doctor import build_production_doctor_payload
from ..state.control_repo import control_artifact_paths, write_control_artifact


TOOL_VERSION = 1


def _now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _top_doctor_blockers(doctor: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    checks = list(doctor.get('checks') or [])
    return [item for item in checks if str(item.get('status')) in {'error', 'warn'}][: max(0, int(limit))]


def _connectivity_summary(incidents: dict[str, Any]) -> dict[str, Any]:
    breaker = dict(incidents.get('breaker') or {})
    connectivity = dict(breaker.get('connectivity') or {})
    return {
        'transport_ready': connectivity.get('transport_ready'),
        'transport_enabled': connectivity.get('transport_enabled'),
        'endpoint_count': connectivity.get('endpoint_count'),
        'active_endpoint_name': connectivity.get('active_endpoint_name'),
        'last_transport_error': connectivity.get('last_transport_error'),
        'last_transport_failure_utc': connectivity.get('last_transport_failure_utc'),
    }


def build_root_cause_triage_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, limit: int = 20, window_hours: int = 24, write_artifact: bool = True) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    incidents = incident_status_payload(repo_root=repo, config_path=ctx.config.config_path, limit=limit, window_hours=window_hours, write_artifact=False)
    doctor = build_production_doctor_payload(repo_root=repo, config_path=ctx.config.config_path, probe_broker=False, strict_runtime_artifacts=False, write_artifact=False)
    breaker = dict(incidents.get('breaker') or {})
    primary = dict(breaker.get('primary_cause') or {})
    symptom = dict(breaker.get('symptom') or {})
    blockers = _top_doctor_blockers(doctor)
    blocker_names = [str(item.get('name')) for item in blockers if item.get('name')]
    artifact_paths = control_artifact_paths(repo_root=repo, asset=str(ctx.config.asset), interval_sec=int(ctx.config.interval_sec))
    payload = {
        'at_utc': _now_utc(),
        'kind': 'root_cause_triage',
        'tool': 'natbin.root_cause_triage',
        'tool_version': TOOL_VERSION,
        'ok': bool(incidents.get('ok', True)) and bool(doctor.get('ok', True)),
        'severity': incidents.get('severity') or doctor.get('severity') or 'unknown',
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': {'asset': ctx.config.asset, 'interval_sec': int(ctx.config.interval_sec), 'timezone': ctx.config.timezone, 'scope_tag': ctx.scope.scope_tag},
        'primary_cause': primary,
        'current_symptom': symptom,
        'connectivity': _connectivity_summary(incidents),
        'issue_codes': [str(item.get('code')) for item in list(incidents.get('open_issues') or []) if item.get('code')],
        'doctor_blocker_checks': blockers,
        'root_cause_chain': [item for item in [primary.get('code'), symptom.get('code'), *blocker_names] if item not in (None, '', 'none')],
        'recommended_actions': list(incidents.get('recommended_actions') or [])[:5],
        'artifacts': {'triage': artifact_paths.get('triage'), 'breaker': artifact_paths.get('breaker'), 'connectivity': artifact_paths.get('connectivity'), 'doctor': artifact_paths.get('doctor'), 'incidents': artifact_paths.get('incidents')},
    }
    if write_artifact:
        write_control_artifact(repo_root=repo, asset=str(ctx.config.asset), interval_sec=int(ctx.config.interval_sec), name='triage', payload=payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Build a concise primary-cause triage summary from control artifacts and live-readiness surfaces.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--window-hours', type=int, default=24)
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_root_cause_triage_payload(repo_root=ns.repo_root, config_path=ns.config, limit=max(1, int(ns.limit or 1)), window_hours=max(1, int(ns.window_hours or 1)), write_artifact=True)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get('ok', True)) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
