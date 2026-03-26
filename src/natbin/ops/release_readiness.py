from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config.execution_mode import execution_mode_uses_broker_submit

from ..alerting.telegram import alerts_status_payload
from ..control.ops import gate_status
from ..control.plan import build_context
from ..ops.release_hygiene import REQUIRED_RELEASE_FILES, build_release_report
from ..security.audit import audit_security_posture
from ..state.control_repo import read_control_artifact, write_control_artifact


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _check(name: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {'name': name, 'status': status, 'message': message}
    if extra:
        item.update(extra)
    return item


def _severity_from_checks(checks: list[dict[str, Any]]) -> str:
    if any(str(item.get('status')) == 'error' for item in checks):
        return 'error'
    if any(str(item.get('status')) == 'warn' for item in checks):
        return 'warn'
    return 'ok'


def _parse_iso(raw: Any) -> datetime | None:
    if raw in (None, ''):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _recent_scope_incident_count(*, repo: Path, scope_tag: str, hours: int = 24) -> tuple[int, list[str]]:
    base = repo / 'runs' / 'incidents'
    if not base.exists():
        return 0, []
    since = datetime.now(tz=UTC) - timedelta(hours=max(1, int(hours)))
    count = 0
    types: list[str] = []
    for path in sorted(base.glob(f'incidents_*_{scope_tag}.jsonl')):
        try:
            for raw in path.read_text(encoding='utf-8', errors='replace').splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                stamp = _parse_iso(obj.get('recorded_at_utc')) or _parse_iso((obj.get('snapshot') or {}).get('observed_at_utc') if isinstance(obj.get('snapshot'), dict) else None)
                if stamp is not None and stamp < since:
                    continue
                sev = str(obj.get('severity') or 'info')
                if sev in {'warn', 'warning', 'error', 'critical'}:
                    count += 1
                    t = str(obj.get('incident_type') or 'unknown')
                    if t and t not in types:
                        types.append(t)
        except Exception:
            continue
    return count, types[:8]


def _incident_surface_check(*, repo: Path, asset: str, interval_sec: int, scope_tag: str, tg: dict[str, Any]) -> dict[str, Any]:
    payload = read_control_artifact(repo_root=repo, asset=asset, interval_sec=interval_sec, name='incidents') or {}
    sev = str((payload.get('status') or payload).get('severity') if isinstance(payload, dict) else 'ok')
    issue_count = len(list(((payload.get('status') or payload).get('open_issues') or []) if isinstance(payload, dict) else []))
    recent_incidents, incident_types = _recent_scope_incident_count(repo=repo, scope_tag=scope_tag, hours=24)
    recent_counts = dict(tg.get('recent_counts') or {})
    if recent_incidents > 0:
        return _check('incident_surface', 'warn', 'Incidentes operacionais recentes no scope', recent_incidents=recent_incidents, incident_types=incident_types, last_incident_severity=sev, issue_count=issue_count)
    if int(recent_counts.get('failed') or 0) > 0:
        return _check('incident_surface', 'warn', 'Fila de alertas com failures recentes', recent_counts=recent_counts, last_incident_severity=sev, issue_count=issue_count)
    if issue_count > 0 and sev in {'warn', 'error'}:
        return _check('incident_surface', 'warn', 'Surface de incidentes ainda contém pendências abertas', last_incident_severity=sev, issue_count=issue_count)
    return _check('incident_surface', 'ok', 'Surface de incidentes limpa', last_incident_severity=sev, issue_count=issue_count, recent_counts=recent_counts)


def build_release_readiness_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    checks: list[dict[str, Any]] = []

    security = audit_security_posture(
        repo_root=repo,
        config_path=ctx.config.config_path,
        resolved_config=ctx.resolved_config,
        source_trace=list(ctx.source_trace),
    )
    if bool(security.get('blocked')):
        checks.append(_check('security_posture', 'error', 'Security posture bloqueia release live', severity=security.get('severity')))
    elif str(security.get('severity') or 'ok') == 'warn':
        checks.append(_check('security_posture', 'warn', 'Security posture com avisos pendentes', severity=security.get('severity')))
    else:
        checks.append(_check('security_posture', 'ok', 'Security posture limpa para release', severity=security.get('severity')))

    gates = gate_status(repo_root=repo, config_path=ctx.config.config_path)
    if bool((gates.get('kill_switch') or {}).get('active')):
        checks.append(_check('kill_switch', 'error', 'Kill-switch ativo'))
    else:
        checks.append(_check('kill_switch', 'ok', 'Kill-switch desligado'))
    if bool((gates.get('drain_mode') or {}).get('active')):
        checks.append(_check('drain_mode', 'warn', 'Drain mode ativo'))
    else:
        checks.append(_check('drain_mode', 'ok', 'Drain mode desligado'))

    runtime = dict(ctx.resolved_config.get('runtime') or {})
    if bool(runtime.get('startup_invalidate_stale_artifacts', True)):
        checks.append(_check('runtime_stale_guard', 'ok', 'Startup stale guard habilitado'))
    else:
        checks.append(_check('runtime_stale_guard', 'warn', 'Startup stale guard desabilitado'))
    if bool(runtime.get('lock_refresh_enable', False)):
        checks.append(_check('runtime_lock_refresh', 'ok', 'Runtime lock refresh habilitado'))
    else:
        checks.append(_check('runtime_lock_refresh', 'warn', 'Runtime lock refresh desabilitado'))

    from ..ops.production_doctor import build_production_doctor_payload

    doctor = build_production_doctor_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        probe_broker=False,
        strict_runtime_artifacts=False,
        enforce_live_broker_prereqs=False,
        write_artifact=True,
    )
    doctor_sev = str(doctor.get('severity') or 'ok')
    if doctor_sev == 'error':
        checks.append(_check('production_doctor', 'error', 'Production doctor encontrou blockers', blockers=doctor.get('blockers') or []))
    elif doctor_sev == 'warn':
        checks.append(_check('production_doctor', 'warn', 'Production doctor com avisos', warnings=doctor.get('warnings') or []))
    else:
        checks.append(_check('production_doctor', 'ok', 'Production doctor sem blockers', warnings=doctor.get('warnings') or []))

    from ..ops.intelligence_surface import build_intelligence_surface_payload

    intelligence = build_intelligence_surface_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        write_artifact=True,
    )
    intelligence_enabled = bool(intelligence.get('enabled'))
    intelligence_sev = str(intelligence.get('severity') or 'ok')
    if not intelligence_enabled:
        checks.append(_check('intelligence_surface', 'ok', 'Intelligence desabilitada no profile atual.'))
    elif intelligence_sev == 'error':
        checks.append(_check('intelligence_surface', 'error', 'Surface de intelligence encontrou blockers.', warnings=intelligence.get('warnings') or []))
    elif intelligence_sev == 'warn':
        checks.append(_check('intelligence_surface', 'warn', 'Surface de intelligence com avisos operacionais.', warnings=intelligence.get('warnings') or []))
    else:
        checks.append(_check('intelligence_surface', 'ok', 'Surface de intelligence pronta para operação.', summary=intelligence.get('summary') or {}))

    execution = dict(ctx.resolved_config.get('execution') or {})
    broker = dict(ctx.resolved_config.get('broker') or {})
    execution_account_mode = str(execution.get('account_mode') or 'PRACTICE').upper()
    broker_balance_mode = str(broker.get('balance_mode') or execution_account_mode or 'PRACTICE').upper()
    live_mode = bool(execution.get('enabled')) and execution_mode_uses_broker_submit(execution.get('mode')) and str(execution.get('provider') or 'fake') == 'iqoption'
    if live_mode:
        checks.append(_check('execution_mode', 'ok', 'Execução live IQ habilitada'))
    elif bool(execution.get('enabled')):
        checks.append(_check('execution_mode', 'warn', f"Execução habilitada em modo {execution.get('mode') or 'unknown'}"))
    else:
        checks.append(_check('execution_mode', 'warn', 'Execução desabilitada; release ainda está paper/local'))

    alerts = alerts_status_payload(repo_root=repo, resolved_config=ctx.resolved_config, limit=20)
    tg = dict(alerts.get('telegram') or {})
    if bool(tg.get('enabled')) and bool(tg.get('send_enabled')) and bool(tg.get('credentials_present')):
        checks.append(_check('telegram_alerting', 'ok', 'Telegram pronto para envio', credential_trace=tg.get('credential_trace')))
    elif bool(tg.get('enabled')):
        checks.append(_check('telegram_alerting', 'warn', 'Telegram configurado sem envio ativo ou sem credenciais', credential_trace=tg.get('credential_trace')))
    else:
        checks.append(_check('telegram_alerting', 'warn', 'Telegram desabilitado'))

    checks.append(_incident_surface_check(repo=repo, asset=ctx.config.asset, interval_sec=int(ctx.config.interval_sec), scope_tag=str(ctx.scope.scope_tag), tg=tg))

    dashboard_ok = False
    try:
        from ..dashboard import app as _dash  # noqa: F401
        dashboard_ok = True
    except Exception:
        dashboard_ok = False
    checks.append(_check('dashboard_import', 'ok' if dashboard_ok else 'error', 'Dashboard importável' if dashboard_ok else 'Dashboard não importável'))

    docker_required = ['Dockerfile', 'docker-compose.yml', 'docker-compose.prod.yml']
    missing_docker = [name for name in docker_required if not (repo / name).exists()]
    checks.append(
        _check(
            'docker_profiles',
            'ok' if not missing_docker else 'warn',
            'Perfis Docker encontrados' if not missing_docker else 'Arquivos Docker ausentes',
            missing=missing_docker,
        )
    )

    docs_required = [
        'docs/OPERATIONS.md',
        'docs/DOCKER.md',
        'docs/ALERTING_M7.md',
        'docs/PRODUCTION_CHECKLIST_M7.md',
        'docs/DIAGRAMS_M7.md',
        'docs/INCIDENT_RUNBOOKS_M71.md',
        'docs/LIVE_OPS_HARDENING_M71.md',
    ]
    missing_docs = [name for name in docs_required if not (repo / name).exists()]
    checks.append(
        _check(
            'runbooks_docs',
            'ok' if not missing_docs else 'error',
            'Runbooks/documentação final presentes' if not missing_docs else 'Runbooks/documentação final ausentes',
            missing=missing_docs,
        )
    )

    missing_release_files = [name for name in REQUIRED_RELEASE_FILES if not (repo / name).exists()]
    if missing_release_files:
        checks.append(_check('release_bundle_required_files', 'error', 'Arquivos mínimos para bundle limpo ausentes', missing=missing_release_files))
    else:
        checks.append(_check('release_bundle_required_files', 'ok', 'Arquivos mínimos para bundle limpo presentes'))

    try:
        release_report = build_release_report(repo_root=repo)
        if release_report.ok:
            checks.append(_check('release_hygiene', 'ok', 'Release hygiene pronta', included_files=release_report.included_files))
        else:
            checks.append(_check('release_hygiene', 'error', 'Release hygiene com pendência', warnings=release_report.warnings))
    except Exception as exc:
        release_report = None
        checks.append(_check('release_hygiene', 'error', f'Falha ao gerar release hygiene: {type(exc).__name__}:{exc}'))

    multi_asset = dict(ctx.resolved_config.get('multi_asset') or {})
    if bool(multi_asset.get('enabled')):
        checks.append(_check('multi_asset', 'ok', 'Multi-asset habilitado', max_parallel_assets=multi_asset.get('max_parallel_assets')))
    else:
        checks.append(_check('multi_asset', 'warn', 'Multi-asset desabilitado'))

    severity = _severity_from_checks(checks)
    ready_for_live = severity == 'ok' and live_mode
    ready_for_practice = ready_for_live and execution_account_mode == 'PRACTICE' and broker_balance_mode == 'PRACTICE'
    ready_for_real = ready_for_live and execution_account_mode == 'REAL' and broker_balance_mode == 'REAL'
    payload = {
        'at_utc': _now_utc(),
        'kind': 'release_readiness',
        'ok': severity != 'error',
        'ready_for_live': ready_for_live,
        'ready_for_practice': ready_for_practice,
        'ready_for_real': ready_for_real,
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': ctx.scope.scope_tag,
        },
        'execution_live': live_mode,
        'execution_account_mode': execution_account_mode,
        'broker_balance_mode': broker_balance_mode,
        'checks': checks,
        'alerts': alerts,
        'gates': gates,
        'security': {
            'blocked': security.get('blocked'),
            'severity': security.get('severity'),
            'credential_source': security.get('credential_source'),
        },
        'intelligence': {
            'enabled': intelligence.get('enabled'),
            'severity': intelligence.get('severity'),
            'warnings': intelligence.get('warnings'),
            'summary': intelligence.get('summary'),
            'allocation': intelligence.get('allocation'),
            'execution': intelligence.get('execution'),
        },
        'doctor': {
            'severity': doctor.get('severity'),
            'ready_for_cycle': doctor.get('ready_for_cycle'),
            'ready_for_live': doctor.get('ready_for_live'),
            'ready_for_practice': doctor.get('ready_for_practice'),
            'ready_for_real': doctor.get('ready_for_real'),
            'warnings': doctor.get('warnings'),
            'blockers': doctor.get('blockers'),
        },
        'release_hygiene': release_report.as_dict() if release_report is not None else None,
    }
    write_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='release', payload=payload)
    return payload
