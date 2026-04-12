from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..alerting.telegram import alerts_status_payload, dispatch_telegram_alert
from ..control.ops import gate_status
from ..control.plan import build_context
from ..ops.intelligence_surface import build_intelligence_surface_payload
from ..ops.release_readiness import build_release_readiness_payload
from ..runtime.hardening import inspect_runtime_freshness
from ..security.audit import audit_security_posture
from ..state.control_repo import read_control_artifact, write_control_artifact

SEVERITY_ORDER = {'ok': 0, 'info': 0, 'warn': 1, 'warning': 1, 'error': 2, 'critical': 2}

DRILL_SCENARIOS: dict[str, dict[str, Any]] = {
    'broker_down': {
        'title': 'Broker / submit indisponível',
        'summary': 'Usar drain mode, congelar novos submits e confirmar reconciliação antes de retomar live.',
        'steps': [
            'Ativar drain mode para bloquear novas entradas sem perder reconciliação.',
            'Executar reconcile e revisar orders/execution antes de reiniciar submit live.',
            'Conferir broker guard, rate limit, time filter e credenciais antes de remover drain.',
        ],
        'commands': [
            'python -m natbin.runtime_app ops drain on --repo-root . --config config/multi_asset.yaml --reason broker_down',
            'python -m natbin.runtime_app reconcile --repo-root . --config config/multi_asset.yaml --json',
            'python -m natbin.runtime_app orders --repo-root . --config config/multi_asset.yaml --json',
            'python -m natbin.runtime_app alerts status --repo-root . --config config/multi_asset.yaml --json',
        ],
    },
    'db_lock': {
        'title': 'Lock / contenção de runtime',
        'summary': 'Validar lockfile/owner, stale artifacts e colisão de scheduler antes de relançar o loop.',
        'steps': [
            'Confirmar se existe outro processo legítimo segurando o scope.',
            'Revisar guard/lifecycle e invalidar stale artifacts se necessário.',
            'Reiniciar apenas um loop por scope após a contenção ser resolvida.',
        ],
        'commands': [
            'python -m natbin.runtime_app status --repo-root . --config config/multi_asset.yaml --json',
            'python -m natbin.runtime_app health --repo-root . --config config/multi_asset.yaml --json',
            'python scripts/tools/runtime_hardening_smoke.py',
        ],
    },
    'market_context_stale': {
        'title': 'Market context stale / fail-closed',
        'summary': 'Revalidar coleta, dataset, payout/contexto e só depois remover o bloqueio operacional.',
        'steps': [
            'Revisar precheck/health e confirmar que o contexto de mercado foi renovado.',
            'Confirmar que dataset e refresh_market_context rodaram no scope correto.',
            'Liberar live somente depois de um ciclo saudável com health ok.',
        ],
        'commands': [
            'python -m natbin.runtime_app precheck --repo-root . --config config/multi_asset.yaml --json',
            'python -m natbin.runtime_app health --repo-root . --config config/multi_asset.yaml --json',
            'python -m natbin.runtime_app observe --repo-root . --config config/multi_asset.yaml --once --json',
        ],
    },
    'alert_queue': {
        'title': 'Fila de alertas atrasada',
        'summary': 'Limpar outbox, validar credenciais/Telegram e garantir que alertas operacionais sejam entregues.',
        'steps': [
            'Inspecionar status recente do Telegram e existência de credenciais.',
            'Rodar flush da fila e revisar itens failed/queued.',
            'Só considerar release verde novamente depois que a fila estiver limpa.',
        ],
        'commands': [
            'python -m natbin.runtime_app alerts status --repo-root . --config config/multi_asset.yaml --json',
            'python -m natbin.runtime_app alerts flush --repo-root . --config config/multi_asset.yaml --limit 20 --json',
            'python -m natbin.runtime_app release --repo-root . --config config/multi_asset.yaml --json',
        ],
    },
}


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec='seconds')


def _parse_iso(raw: Any) -> datetime | None:
    if raw in (None, ''):
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _incident_timestamp(item: dict[str, Any]) -> datetime | None:
    for key in ('recorded_at_utc', 'at_utc', 'created_at_utc', 'observed_at_utc'):
        dt = _parse_iso(item.get(key))
        if dt is not None:
            return dt
    snap = item.get('snapshot') or {}
    if isinstance(snap, dict):
        for key in ('recorded_at_utc', 'observed_at_utc', 'at_utc'):
            dt = _parse_iso(snap.get(key))
            if dt is not None:
                return dt
    day = str(item.get('day') or (snap.get('day') if isinstance(snap, dict) else '') or '').strip()
    ts = item.get('ts') if item.get('ts') is not None else (snap.get('ts') if isinstance(snap, dict) else None)
    try:
        if day and ts is not None:
            val = int(ts)
            if val > 0:
                return datetime.fromtimestamp(val, tz=UTC)
    except Exception:
        return None
    return None


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda x: (_incident_timestamp(x) or datetime(1970, 1, 1, tzinfo=UTC), str(x.get('incident_type') or '')))


def _summarize_incidents(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    for item in items:
        by_type[str(item.get('incident_type') or 'unknown')] += 1
        by_severity[str(item.get('severity') or 'info')] += 1
    latest = _sort_items(list(items))[-1] if items else None
    return {
        'total': len(items),
        'by_type': dict(sorted(by_type.items())),
        'by_severity': dict(sorted(by_severity.items())),
        'latest': latest,
    }


def _scope_incident_files(repo_root: Path, scope_tag: str) -> list[Path]:
    base = repo_root / 'runs' / 'incidents'
    if not base.exists():
        return []
    return sorted(base.glob(f'incidents_*_{scope_tag}.jsonl'))


def load_recent_scope_incidents(*, repo_root: str | Path, scope_tag: str, limit: int = 20, window_hours: int = 24) -> list[dict[str, Any]]:
    root = Path(repo_root).resolve()
    since = _now() - timedelta(hours=max(1, int(window_hours)))
    items: list[dict[str, Any]] = []
    for path in _scope_incident_files(root, scope_tag):
        try:
            with path.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    ts = _incident_timestamp(obj)
                    if ts is not None and ts < since:
                        continue
                    obj['_path'] = str(path)
                    if ts is not None:
                        obj['_ts_utc'] = _iso(ts)
                    items.append(obj)
        except Exception:
            continue
    items = _sort_items(items)
    return items[-max(1, int(limit)):]


def _issue(code: str, severity: str, message: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        'code': str(code),
        'severity': 'warn' if str(severity) == 'warning' else str(severity),
        'message': str(message),
    }
    if extra:
        item.update(extra)
    return item


def _overall_severity(items: list[dict[str, Any]]) -> str:
    max_level = 0
    for item in items:
        level = SEVERITY_ORDER.get(str(item.get('severity') or 'ok'), 0)
        if level > max_level:
            max_level = level
    if max_level >= 2:
        return 'error'
    if max_level >= 1:
        return 'warn'
    return 'ok'


def _health_summary(repo_root: str | Path, asset: str, interval_sec: int) -> dict[str, Any] | None:
    payload = read_control_artifact(repo_root=repo_root, asset=asset, interval_sec=interval_sec, name='health')
    return payload if isinstance(payload, dict) else None


def _loop_summary(repo_root: str | Path, asset: str, interval_sec: int) -> dict[str, Any] | None:
    payload = read_control_artifact(repo_root=repo_root, asset=asset, interval_sec=interval_sec, name='loop_status')
    return payload if isinstance(payload, dict) else None


def _breaker_summary(repo_root: str | Path, asset: str, interval_sec: int) -> dict[str, Any]:
    payload = read_control_artifact(repo_root=repo_root, asset=asset, interval_sec=interval_sec, name='breaker')
    if not isinstance(payload, dict):
        return {
            'present': False,
            'primary_cause': {'code': 'none', 'category': 'none', 'detail': None},
            'symptom': {'code': 'none', 'detail': None},
            'connectivity': {},
            'last_transport_error': None,
        }
    primary_cause = payload.get('primary_cause') if isinstance(payload.get('primary_cause'), dict) else {'code': 'none', 'category': 'none', 'detail': None}
    symptom = payload.get('symptom') if isinstance(payload.get('symptom'), dict) else {'code': 'none', 'detail': None}
    connectivity = payload.get('connectivity') if isinstance(payload.get('connectivity'), dict) else {}
    last_transport_error = payload.get('last_transport_error')
    if last_transport_error in (None, ''):
        last_transport_error = connectivity.get('last_transport_error')
    return {
        'present': True,
        'primary_cause': primary_cause,
        'symptom': symptom,
        'connectivity': connectivity,
        'last_transport_error': last_transport_error,
    }


def _recommended_actions(*, repo_root: Path, config_path: str, asset: str, interval_sec: int, issues: list[dict[str, Any]], recent_incidents: list[dict[str, Any]], stage: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(action_id: str, reason: str, commands: list[str]) -> None:
        if action_id in seen:
            return
        seen.add(action_id)
        out.append({'id': action_id, 'reason': reason, 'commands': commands})

    issue_codes = {str(item.get('code') or '') for item in issues}
    incident_types = {str(item.get('incident_type') or '') for item in recent_incidents}
    repo_s = str(repo_root)
    cfg_s = str(config_path)
    stage_key = str(stage or '').strip().lower()

    if 'kill_switch_active' in issue_codes:
        add('killswitch_review', 'Kill-switch está ativo; confirmar motivo antes de voltar a submeter ordens.', [
            f'python -m natbin.runtime_app ops killswitch status --repo-root {repo_s} --config {cfg_s}',
            f'python -m natbin.runtime_app release --repo-root {repo_s} --config {cfg_s} --json',
        ])
    if 'drain_mode_active' in issue_codes:
        add('drain_mode_review', 'Drain mode está ativo; revisar reconcile/orders antes de desabilitar.', [
            f'python -m natbin.runtime_app reconcile --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app orders --repo-root {repo_s} --config {cfg_s} --json',
        ])
    if 'runtime_stale_artifacts' in issue_codes:
        add('runtime_restart', 'Há artefatos stale; validar um único owner do scope e reiniciar o loop com hardening.', [
            f'python -m natbin.runtime_app status --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app health --repo-root {repo_s} --config {cfg_s} --json',
        ])
    if 'recent_warning_incidents' in issue_codes or 'market_context_stale' in incident_types or 'gate_fail_closed' in incident_types:
        add('market_context_refresh', 'Incidentes recentes apontam bloqueio operacional; revalidar precheck/health antes de retomar live.', [
            f'python -m natbin.runtime_app precheck --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app health --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app observe --repo-root {repo_s} --config {cfg_s} --once --json',
        ])
    if 'telegram_failed_alerts' in issue_codes or 'telegram_queued_alerts' in issue_codes:
        add('alert_queue_flush', 'Existem alertas queued/failed; limpar outbox antes do próximo gate live.', [
            f'python -m natbin.runtime_app alerts status --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app alerts flush --repo-root {repo_s} --config {cfg_s} --limit 20 --json',
        ])
    if 'release_readiness_error' in issue_codes or 'release_readiness_warn' in issue_codes:
        add('release_review', 'Release readiness não está limpa; revisar checks antes de operar live.', [
            f'python -m natbin.runtime_app release --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app security --repo-root {repo_s} --config {cfg_s} --json',
        ])
    if 'health_not_ok' in issue_codes or 'loop_failure_recent' in issue_codes:
        add('health_reconcile', 'Último health/loop não está saudável; revisar execução e reconciliação.', [
            f'python -m natbin.runtime_app health --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app orders --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app reconcile --repo-root {repo_s} --config {cfg_s} --json',
        ])
    if {'intelligence_surface_warn', 'intelligence_retrain_pending', 'intelligence_feedback_block', 'intelligence_traceability_warn'} & issue_codes:
        add('intelligence_review', 'Há avisos na surface de intelligence; revisar score/alocação/retrain antes do próximo ciclo.', [
            f'python -m natbin.runtime_app intelligence --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app portfolio status --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.intelligence_pack --repo-root {repo_s} --asset {asset} --interval-sec {interval_sec} --json',
        ])
    if not out:
        add('steady_state_review', 'Superfície operacional está limpa; manter checagens de rotina antes do próximo release/live.', [
            f'python -m natbin.runtime_app release --repo-root {repo_s} --config {cfg_s} --json',
            f'python -m natbin.runtime_app incidents drill --repo-root {repo_s} --config {cfg_s} --scenario broker_down --json',
        ])
    return out


def incident_status_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, limit: int = 20, window_hours: int = 24, write_artifact: bool = True, stage: str | None = None) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    now = _now()
    stage_key = str(stage or '').strip().lower()
    release = build_release_readiness_payload(repo_root=repo, config_path=ctx.config.config_path)
    alerts = alerts_status_payload(repo_root=repo, resolved_config=ctx.resolved_config, limit=max(5, int(limit)))
    gates = gate_status(repo_root=repo, config_path=ctx.config.config_path)
    security = audit_security_posture(
        repo_root=repo,
        config_path=ctx.config.config_path,
        resolved_config=ctx.resolved_config,
        source_trace=list(ctx.source_trace),
    )
    intelligence = build_intelligence_surface_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        write_artifact=True,
    )
    freshness = inspect_runtime_freshness(repo_root=repo, ctx=ctx, now_utc=now)
    health = _health_summary(repo, ctx.config.asset, int(ctx.config.interval_sec))
    loop_status = _loop_summary(repo, ctx.config.asset, int(ctx.config.interval_sec))
    breaker = _breaker_summary(repo, ctx.config.asset, int(ctx.config.interval_sec))
    recent_incidents = load_recent_scope_incidents(repo_root=repo, scope_tag=ctx.scope.scope_tag, limit=limit, window_hours=window_hours)
    incident_summary = _summarize_incidents(recent_incidents)

    issues: list[dict[str, Any]] = []
    release_sev = str(release.get('severity') or 'ok')
    if release_sev == 'error':
        issues.append(_issue('release_readiness_error', 'error', 'Release readiness contém bloqueadores abertos.', severity_release=release_sev))
    elif release_sev == 'warn' and stage_key != 'practice':
        issues.append(_issue('release_readiness_warn', 'warn', 'Release readiness ainda contém avisos pendentes.', severity_release=release_sev))

    if bool(security.get('blocked')):
        issues.append(_issue('security_blocked', 'error', 'Auditoria de segurança bloqueia operação/release.', security_severity=security.get('severity')))
    elif str(security.get('severity') or 'ok') == 'warn':
        issues.append(_issue('security_warn', 'warn', 'Auditoria de segurança com avisos pendentes.', security_severity=security.get('severity')))

    intelligence_enabled = bool(intelligence.get('enabled'))
    intelligence_summary = dict(intelligence.get('summary') or {})
    intelligence_allocation = dict(intelligence.get('allocation') or {})
    intelligence_execution = dict(intelligence.get('execution') or {})
    intelligence_sev = str(intelligence.get('severity') or 'ok')
    retrain_state = str(intelligence_summary.get('retrain_state') or '')
    retrain_priority = str(intelligence_summary.get('retrain_priority') or '')
    if intelligence_enabled and intelligence_sev in {'warn', 'error'}:
        issues.append(_issue('intelligence_surface_warn', 'warn' if intelligence_sev == 'warn' else 'error', 'Surface operacional de intelligence contém avisos.', warnings=intelligence.get('warnings') or []))
    if intelligence_enabled and (retrain_priority.lower() == 'high' or retrain_state.lower() in {'queued', 'cooldown'}):
        issues.append(_issue('intelligence_retrain_pending', 'warn', 'Scope requer atenção de retrain / monitoramento.', retrain_state=retrain_state or None, retrain_priority=retrain_priority or None))
    if intelligence_enabled and bool(intelligence_summary.get('portfolio_feedback_blocked')):
        issues.append(_issue('intelligence_feedback_block', 'warn', 'Portfolio feedback está bloqueando o scope.', reason=intelligence_summary.get('portfolio_feedback_reason') or intelligence_summary.get('block_reason')))
    missing_trace_fields = list(intelligence_execution.get('missing_fields') or [])
    if intelligence_enabled and missing_trace_fields:
        issues.append(_issue('intelligence_traceability_warn', 'warn', 'Intent recente sem trilha completa de intelligence/alocação.', missing_fields=missing_trace_fields, allocation_id=intelligence_allocation.get('allocation_id')))

    if bool((gates.get('kill_switch') or {}).get('active')):
        issues.append(_issue('kill_switch_active', 'error', 'Kill-switch ativo.', reason=(gates.get('kill_switch') or {}).get('reason')))
    if bool((gates.get('drain_mode') or {}).get('active')):
        issues.append(_issue('drain_mode_active', 'warn', 'Drain mode ativo.', reason=(gates.get('drain_mode') or {}).get('reason')))

    breaker_primary = dict(breaker.get('primary_cause') or {})
    breaker_primary_code = str(breaker_primary.get('code') or 'none')
    breaker_transport_error = breaker.get('last_transport_error')
    breaker_issue_extra = {}
    if breaker_primary_code not in {'', 'none'}:
        breaker_issue_extra = {
            'primary_cause_code': breaker_primary_code,
            'last_transport_error': breaker_transport_error,
        }
        issues.append(_issue('breaker_primary_cause', 'warn', 'Breaker surface indica causa primária operacional.', **breaker_issue_extra))

    if freshness.stale_artifacts:
        issues.append(_issue('runtime_stale_artifacts', 'error', 'Há artefatos stale no scope.', stale_artifacts=[a.name for a in freshness.stale_artifacts]))

    tg = dict(alerts.get('telegram') or {})
    recent_counts = dict(tg.get('recent_counts') or {})
    if int(recent_counts.get('failed') or 0) > 0:
        issues.append(_issue('telegram_failed_alerts', 'warn', 'Há alertas Telegram failed recentes.', recent_counts=recent_counts))
    if bool(tg.get('send_enabled')) and int(recent_counts.get('queued') or 0) > 0:
        issues.append(_issue('telegram_queued_alerts', 'warn', 'Há alertas Telegram queued com envio habilitado.', recent_counts=recent_counts))

    warning_count = int((incident_summary.get('by_severity') or {}).get('warning') or 0) + int((incident_summary.get('by_severity') or {}).get('warn') or 0)
    if warning_count > 0:
        issues.append(_issue('recent_warning_incidents', 'warn', 'Existem incidentes operacionais recentes no scope.', count=warning_count, by_type=incident_summary.get('by_type')))

    health_state = str((health or {}).get('state') or 'unknown')
    if health_state not in {'healthy', 'ok', 'unknown'}:
        issues.append(_issue('health_not_ok', 'warn', f'Health do scope não está saudável: {health_state}.', state=health_state, health_message=(health or {}).get('message'), **breaker_issue_extra))

    loop_message = str((loop_status or {}).get('message') or '')
    loop_phase = str((loop_status or {}).get('phase') or '')
    if loop_phase.lower() in {'error', 'failed'} or 'failure' in loop_message.lower():
        issues.append(_issue('loop_failure_recent', 'warn', 'Loop status recente aponta falha operacional.', phase=loop_phase, loop_message=loop_message, **breaker_issue_extra))

    severity = _overall_severity(issues)
    recommended = _recommended_actions(
        repo_root=repo,
        config_path=str(ctx.config.config_path),
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        issues=issues,
        recent_incidents=recent_incidents,
        stage=stage_key or None,
    )
    payload = {
        'at_utc': _iso(now),
        'kind': 'incident_status',
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'timezone': ctx.config.timezone,
            'scope_tag': ctx.scope.scope_tag,
        },
        'window_hours': int(window_hours),
        'limit': int(limit),
        'stage': stage_key or None,
        'gates': gates,
        'release': {
            'severity': release.get('severity'),
            'ready_for_live': release.get('ready_for_live'),
            'execution_live': release.get('execution_live'),
        },
        'security': {
            'severity': security.get('severity'),
            'blocked': security.get('blocked'),
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
        'alerts': {
            'telegram': {
                'enabled': tg.get('enabled'),
                'send_enabled': tg.get('send_enabled'),
                'credentials_present': tg.get('credentials_present'),
                'recent_counts': recent_counts,
                'recent': list(tg.get('recent') or [])[-5:],
            }
        },
        'health': health,
        'loop_status': loop_status,
        'breaker': breaker,
        'runtime_freshness': freshness.as_dict(),
        'incidents': {
            **incident_summary,
            'recent': recent_incidents,
        },
        'open_issues': issues,
        'recommended_actions': recommended,
    }
    if write_artifact:
        write_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='incidents', payload=payload)
    return payload


def incident_reports_dir(*, repo_root: str | Path) -> Path:
    path = Path(repo_root).resolve() / 'runs' / 'incidents' / 'reports'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_report_files(*, repo_root: Path, scope_tag: str, payload: dict[str, Any], at_utc: str) -> dict[str, str]:
    reports_dir = incident_reports_dir(repo_root=repo_root)
    stamp = str(at_utc).replace(':', '').replace('-', '').replace('+00:00', 'Z')
    latest_path = reports_dir / f'incident_report_latest_{scope_tag}.json'
    report_path = reports_dir / f'incident_report_{stamp}_{scope_tag}.json'
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    latest_path.write_text(text, encoding='utf-8')
    report_path.write_text(text, encoding='utf-8')
    return {'latest_report_path': str(latest_path), 'report_path': str(report_path)}


def incident_report_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, limit: int = 20, window_hours: int = 24, write_artifact: bool = True, stage: str | None = None) -> dict[str, Any]:
    status = incident_status_payload(repo_root=repo_root, config_path=config_path, limit=limit, window_hours=window_hours, write_artifact=write_artifact, stage=stage)
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    release_full = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='release') or build_release_readiness_payload(repo_root=repo, config_path=ctx.config.config_path)
    security_full = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='security') or audit_security_posture(
        repo_root=repo,
        config_path=ctx.config.config_path,
        resolved_config=ctx.resolved_config,
        source_trace=list(ctx.source_trace),
    )
    report = {
        'at_utc': status.get('at_utc'),
        'kind': 'incident_report',
        'ok': status.get('ok'),
        'severity': status.get('severity'),
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': status.get('scope'),
        'stage': status.get('stage'),
        'status': status,
        'release_full': release_full,
        'security_full': security_full,
        'ops_snapshot': {
            'gates': status.get('gates'),
            'runtime_freshness': status.get('runtime_freshness'),
            'health': status.get('health'),
            'loop_status': status.get('loop_status'),
            'intelligence': status.get('intelligence'),
        },
        'timeline': {
            'recent_incidents': (status.get('incidents') or {}).get('recent') or [],
            'recent_alerts': ((status.get('alerts') or {}).get('telegram') or {}).get('recent') or [],
        },
        'recommended_actions': status.get('recommended_actions') or [],
    }
    report['artifacts'] = _write_report_files(repo_root=repo, scope_tag=str(ctx.scope.scope_tag), payload=report, at_utc=str(status.get('at_utc') or _iso(_now())))
    if write_artifact:
        write_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='incidents', payload=report)
    return report


def incident_alert_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, limit: int = 20, window_hours: int = 24, force_send: bool | None = None) -> dict[str, Any]:
    status = incident_status_payload(repo_root=repo_root, config_path=config_path, limit=limit, window_hours=window_hours, write_artifact=True)
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    tg = ((status.get('alerts') or {}).get('telegram') or {})
    issues = list(status.get('open_issues') or [])
    recent = (status.get('incidents') or {}).get('latest') or {}
    lines = [
        f"scope={status.get('scope', {}).get('scope_tag')}",
        f"severity={status.get('severity')}",
        f"open_issues={len(issues)}",
        f"telegram_recent_counts={tg.get('recent_counts')}",
    ]
    if recent:
        lines.append(f"latest_incident={recent.get('incident_type')}:{recent.get('severity')}")
    if issues:
        lines.append('issues=' + ','.join(str(item.get('code')) for item in issues[:6]))
    alert = dispatch_telegram_alert(
        repo_root=ctx.repo_root,
        resolved_config=ctx.resolved_config,
        title='Thalor incident status',
        lines=lines,
        severity=str(status.get('severity') or 'info'),
        source='runtime_app.incidents_alert',
        force_send=force_send,
    )
    payload = {
        'at_utc': _iso(_now()),
        'kind': 'incident_alert',
        'ok': True,
        'status': status,
        'alert': alert,
    }
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='incidents', payload=payload)
    return payload


def incident_drill_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, scenario: str = 'broker_down') -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    key = str(scenario or 'broker_down').strip().lower()
    data = DRILL_SCENARIOS.get(key, DRILL_SCENARIOS['broker_down'])
    repo = Path(ctx.repo_root).resolve()
    commands = [cmd.replace('config/multi_asset.yaml', str(ctx.config.config_path)).replace(' --repo-root . ', f' --repo-root {repo} ') for cmd in list(data.get('commands') or [])]
    payload = {
        'at_utc': _iso(_now()),
        'kind': 'incident_drill',
        'ok': True,
        'scenario': key,
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'timezone': ctx.config.timezone,
            'scope_tag': ctx.scope.scope_tag,
        },
        'title': data.get('title'),
        'summary': data.get('summary'),
        'steps': list(data.get('steps') or []),
        'commands': commands,
        'notes': 'Drill sem side effects: os comandos são sugeridos, não executados automaticamente.',
    }
    write_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='incidents', payload=payload)
    return payload
