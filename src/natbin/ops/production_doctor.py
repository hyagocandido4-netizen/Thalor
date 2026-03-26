from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config.execution_mode import execution_mode_uses_broker_submit

from ..control.plan import build_context
from ..runtime.failsafe import CircuitBreakerPolicy, RuntimeFailsafe
from ..runtime.broker_surface import adapter_from_context
from ..runtime.perf import load_json_cached
from ..security.audit import audit_security_posture
from ..state.control_repo import RuntimeControlRepository, read_control_artifact, write_control_artifact
from .artifact_retention import build_retention_payload


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _status_error_or_skip(strict: bool) -> str:
    return 'error' if strict else 'skip'


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


def _resolve_path(repo: Path, raw: str | Path | None) -> Path | None:
    if raw in (None, ''):
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = repo / p
    return p


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


def _count_csv_rows(path: Path) -> int | None:
    if not path.exists() or not path.is_file():
        return None
    count = 0
    try:
        with path.open('r', encoding='utf-8', errors='replace') as fh:
            for count, _line in enumerate(fh, start=1):
                pass
    except Exception:
        return None
    return max(0, count - 1)


def _ensure_writeable(path: Path) -> tuple[bool, str | None]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / '.doctor_write_probe'
        probe.write_text('ok\n', encoding='utf-8')
        probe.unlink(missing_ok=True)
        return True, None
    except Exception as exc:
        return False, f'{type(exc).__name__}:{exc}'


def _failsafe_from_ctx(ctx, repo: Path) -> RuntimeFailsafe:
    fs = dict(ctx.resolved_config.get('failsafe') or {})
    kill_file = _resolve_path(repo, fs.get('kill_switch_file') or 'runs/KILL_SWITCH') or (repo / 'runs' / 'KILL_SWITCH')
    drain_file = _resolve_path(repo, fs.get('drain_mode_file') or 'runs/DRAIN_MODE') or (repo / 'runs' / 'DRAIN_MODE')
    policy = CircuitBreakerPolicy(
        failures_to_open=int(fs.get('breaker_failures_to_open') or 3),
        cooldown_minutes=int(fs.get('breaker_cooldown_minutes') or 15),
        half_open_trials=int(fs.get('breaker_half_open_trials') or 1),
    )
    return RuntimeFailsafe(
        kill_switch_file=kill_file,
        kill_switch_env_var=str(fs.get('kill_switch_env_var') or 'THALOR_KILL_SWITCH'),
        drain_mode_file=drain_file,
        drain_mode_env_var=str(fs.get('drain_mode_env_var') or 'THALOR_DRAIN_MODE'),
        global_fail_closed=bool(fs.get('global_fail_closed', True)),
        market_context_fail_closed=bool(fs.get('market_context_fail_closed', True)),
        policy=policy,
    )


def _control_artifact_age_sec(payload: dict[str, Any] | None) -> float | None:
    if not isinstance(payload, dict):
        return None
    stamp = _parse_iso(payload.get('at_utc'))
    if stamp is None:
        return None
    return max(0.0, (_now_utc() - stamp).total_seconds())


def _broker_reason_normalized(reason: str | None) -> str | None:
    if reason in (None, ''):
        return None
    raw = str(reason)
    low = raw.lower()
    if 'invalid_credentials' in low or 'wrong credentials' in low:
        return 'iqoption_invalid_credentials'
    if 'missing_credentials' in low:
        return 'iqoption_missing_credentials'
    if 'dependency_missing' in low:
        return 'iqoption_dependency_missing'
    if 'timeout' in low:
        return 'iqoption_connect_timeout'
    return raw


def build_production_doctor_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    probe_broker: bool = False,
    strict_runtime_artifacts: bool = True,
    enforce_live_broker_prereqs: bool = True,
    market_context_max_age_sec: int | None = None,
    min_dataset_rows: int = 100,
    write_artifact: bool = True,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    checks: list[dict[str, Any]] = []
    now_utc = _now_utc()
    exec_cfg = dict(ctx.resolved_config.get('execution') or {})
    broker_cfg = dict(ctx.resolved_config.get('broker') or {})
    execution_live = bool(exec_cfg.get('enabled')) and execution_mode_uses_broker_submit(exec_cfg.get('mode')) and str(exec_cfg.get('provider') or 'fake') == 'iqoption'
    execution_account_mode = str(exec_cfg.get('account_mode') or 'PRACTICE').upper()
    broker_balance_mode = str(broker_cfg.get('balance_mode') or execution_account_mode or 'PRACTICE').upper()

    security = audit_security_posture(
        repo_root=repo,
        config_path=ctx.config.config_path,
        resolved_config=ctx.resolved_config,
        source_trace=list(ctx.source_trace),
    )
    security_errors = [item for item in list(security.get('checks') or []) if str(item.get('status')) == 'error']
    only_missing_broker_creds = (
        not execution_live
        and security_errors
        and all(str(item.get('name')) == 'broker_credentials_present' for item in security_errors)
    )
    if bool(security.get('blocked')) and not only_missing_broker_creds:
        checks.append(_check('security_posture', 'error', 'Security posture bloqueia operação', severity=security.get('severity')))
    elif only_missing_broker_creds:
        checks.append(_check('security_posture', 'ok', 'Security posture aceita ausência de credenciais fora do modo live', severity='ok'))
    elif str(security.get('severity') or 'ok') == 'warn':
        checks.append(_check('security_posture', 'warn', 'Security posture com avisos', severity=security.get('severity')))
    else:
        checks.append(_check('security_posture', 'ok', 'Security posture limpa', severity=security.get('severity')))

    fs = _failsafe_from_ctx(ctx, repo)
    kill_active, kill_reason = fs.is_kill_switch_active(dict(os.environ))
    drain_active, drain_reason = fs.is_drain_mode_active(dict(os.environ))
    if kill_active:
        checks.append(_check('failsafe_kill_switch', 'error', 'Kill-switch ativo', reason=kill_reason))
    else:
        checks.append(_check('failsafe_kill_switch', 'ok', 'Kill-switch desligado'))
    if drain_active:
        checks.append(_check('failsafe_drain_mode', 'warn', 'Drain mode ativo', reason=drain_reason))
    else:
        checks.append(_check('failsafe_drain_mode', 'ok', 'Drain mode desligado'))

    control_repo = RuntimeControlRepository(repo / 'runs' / 'runtime_control.sqlite3')
    breaker = control_repo.load_breaker(str(ctx.config.asset), int(ctx.config.interval_sec))
    breaker = fs.evaluate_circuit(breaker, now_utc)
    if str(breaker.state) == 'open':
        checks.append(_check('circuit_breaker', 'error', 'Circuit breaker aberto', reason=breaker.reason, opened_until_utc=breaker.opened_until_utc.isoformat() if breaker.opened_until_utc else None))
    elif str(breaker.state) == 'half_open':
        checks.append(_check('circuit_breaker', 'warn', 'Circuit breaker em half-open', reason=breaker.reason))
    else:
        checks.append(_check('circuit_breaker', 'ok', 'Circuit breaker fechado'))

    runs_ok, runs_err = _ensure_writeable(repo / 'runs')
    control_ok, control_err = _ensure_writeable(repo / 'runs' / 'control')
    logs_ok, logs_err = _ensure_writeable(repo / 'runs' / 'logs')
    if runs_ok and control_ok and logs_ok:
        checks.append(_check('runtime_paths_writeable', 'ok', 'runs/control/logs graváveis'))
    else:
        checks.append(_check('runtime_paths_writeable', 'error', 'Falha ao gravar em paths de runtime', errors=[err for err in [runs_err, control_err, logs_err] if err]))

    dataset_path = _resolve_path(repo, ((ctx.resolved_config.get('data') or {}).get('dataset_path') if isinstance(ctx.resolved_config, dict) else None)) or _resolve_path(repo, ctx.config.dataset_path)
    dataset_rows = _count_csv_rows(dataset_path) if dataset_path is not None else None
    if dataset_path is None or not dataset_path.exists():
        checks.append(_check('dataset_ready', _status_error_or_skip(strict_runtime_artifacts), 'Dataset ausente', dataset_path=str(dataset_path) if dataset_path is not None else None))
    elif dataset_rows is None:
        checks.append(_check('dataset_ready', _status_error_or_skip(strict_runtime_artifacts), 'Dataset ilegível', dataset_path=str(dataset_path)))
    elif int(dataset_rows) < int(min_dataset_rows):
        checks.append(_check('dataset_ready', _status_error_or_skip(strict_runtime_artifacts), 'Dataset com poucas linhas para operação', dataset_path=str(dataset_path), rows=int(dataset_rows), min_rows=int(min_dataset_rows)))
    else:
        checks.append(_check('dataset_ready', 'ok', 'Dataset pronto', dataset_path=str(dataset_path), rows=int(dataset_rows)))

    market_path = Path(ctx.scoped_paths.get('market_context') or '') if ctx.scoped_paths.get('market_context') else None
    market_payload = load_json_cached(str(market_path)) if market_path is not None and market_path.exists() else None
    max_age = int(market_context_max_age_sec or max(int(ctx.config.interval_sec) * 3, 600))
    if market_path is None or not market_path.exists() or not isinstance(market_payload, dict):
        checks.append(_check('market_context', _status_error_or_skip(strict_runtime_artifacts), 'Market context ausente', max_age_sec=max_age))
    else:
        stamp = _parse_iso(market_payload.get('at_utc'))
        age_sec = None if stamp is None else max(0.0, (now_utc - stamp).total_seconds())
        if age_sec is None:
            checks.append(_check('market_context', _status_error_or_skip(strict_runtime_artifacts), 'Market context sem timestamp válido', path=str(market_path), max_age_sec=max_age))
        elif age_sec > max_age:
            checks.append(_check('market_context', _status_error_or_skip(strict_runtime_artifacts), 'Market context stale', path=str(market_path), age_sec=round(age_sec, 3), max_age_sec=max_age, open_source=market_payload.get('open_source')))
        elif market_payload.get('market_open') is False:
            checks.append(_check('market_context', 'warn', 'Market context válido, porém mercado fechado', path=str(market_path), age_sec=round(age_sec, 3), open_source=market_payload.get('open_source')))
        else:
            checks.append(_check('market_context', 'ok', 'Market context fresco', path=str(market_path), age_sec=round(age_sec, 3), open_source=market_payload.get('open_source')))

    eff_latest = Path(ctx.scoped_paths.get('effective_config') or '') if ctx.scoped_paths.get('effective_config') else None
    eff_control = Path(ctx.scoped_paths.get('effective_config_control') or '') if ctx.scoped_paths.get('effective_config_control') else None
    latest_ok = bool(eff_latest and eff_latest.exists() and isinstance(load_json_cached(str(eff_latest)), dict))
    control_ok_eff = bool(eff_control and eff_control.exists() and isinstance(load_json_cached(str(eff_control)), dict))
    if latest_ok and control_ok_eff:
        checks.append(_check('effective_config_artifacts', 'ok', 'Effective config latest/control presentes', latest_path=str(eff_latest), control_path=str(eff_control)))
    else:
        checks.append(_check('effective_config_artifacts', 'error', 'Effective config latest/control ausentes ou inválidos', latest_path=str(eff_latest) if eff_latest else None, control_path=str(eff_control) if eff_control else None))

    loop_status = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status')
    health = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='health')
    loop_age = _control_artifact_age_sec(loop_status)
    health_age = _control_artifact_age_sec(health)
    freshness_limit = max(int(ctx.config.interval_sec) * 4, 900)
    if loop_age is None or health_age is None:
        checks.append(_check('control_freshness', 'skip' if not strict_runtime_artifacts else 'warn', 'Artifacts de loop/health ainda não emitidos', freshness_limit_sec=freshness_limit))
    elif loop_age > freshness_limit or health_age > freshness_limit:
        checks.append(_check('control_freshness', 'warn', 'Artifacts de loop/health estão defasados', loop_age_sec=round(loop_age, 3), health_age_sec=round(health_age, 3), freshness_limit_sec=freshness_limit))
    else:
        checks.append(_check('control_freshness', 'ok', 'Artifacts de loop/health frescos', loop_age_sec=round(loop_age, 3), health_age_sec=round(health_age, 3), freshness_limit_sec=freshness_limit))

    from .intelligence_surface import build_intelligence_surface_payload

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
        checks.append(_check('intelligence_surface', 'ok', 'Surface de intelligence pronta.', summary=intelligence.get('summary') or {}))

    if not bool(exec_cfg.get('enabled')):
        checks.append(_check('broker_preflight', 'ok', 'Execução desabilitada; preflight live não requerido', execution_mode=exec_cfg.get('mode'), provider=exec_cfg.get('provider')))
        broker_health = None
    elif not execution_live:
        checks.append(_check('broker_preflight', 'ok', 'Execução habilitada fora do modo live IQ; preflight passivo suficiente', execution_mode=exec_cfg.get('mode'), provider=exec_cfg.get('provider')))
        broker_health = None
    else:
        adapter = adapter_from_context(ctx, repo_root=repo)
        dep = adapter._dependency_status() if hasattr(adapter, '_dependency_status') else {'available': True, 'reason': None}
        email, password = adapter._credentials() if hasattr(adapter, '_credentials') else (None, None)
        if not bool(dep.get('available', True)):
            status = 'error' if enforce_live_broker_prereqs else 'skip'
            checks.append(_check('broker_preflight', status, 'Dependência do broker ausente', reason='iqoption_dependency_missing'))
            broker_health = {'ready': False, 'healthy': False, 'reason': 'iqoption_dependency_missing', 'probed': False}
        elif not email or not password:
            status = 'error' if enforce_live_broker_prereqs else 'skip'
            checks.append(_check('broker_preflight', status, 'Credenciais do broker ausentes', reason='iqoption_missing_credentials'))
            broker_health = {'ready': False, 'healthy': False, 'reason': 'iqoption_missing_credentials', 'probed': False}
        elif probe_broker:
            session = adapter.healthcheck()
            norm_reason = _broker_reason_normalized(getattr(session, 'reason', None))
            if bool(session.ready):
                checks.append(_check('broker_preflight', 'ok', 'Broker live respondeu ao probe', reason=norm_reason, checked_at_utc=session.checked_at_utc, probed=True))
            else:
                checks.append(_check('broker_preflight', 'error', 'Broker live falhou no probe', reason=norm_reason, checked_at_utc=session.checked_at_utc, probed=True))
            broker_health = {**session.as_dict(), 'reason': norm_reason, 'probed': True}
        else:
            checks.append(_check('broker_preflight', 'ok', 'Preflight passivo do broker OK', reason=None, dependency_available=True, credentials_present=True, probed=False))
            broker_health = {'ready': True, 'healthy': True, 'reason': None, 'probed': False}

    retention = build_retention_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        apply=False,
        write_artifact=False,
    )
    if int(retention.get('candidates_total') or 0) > 0:
        checks.append(_check('retention_backlog', 'warn', 'Há artefatos antigos elegíveis para retenção', candidates_total=int(retention.get('candidates_total') or 0), categories=retention.get('categories')))
    else:
        checks.append(_check('retention_backlog', 'ok', 'Sem backlog de retenção'))

    severity = _severity_from_checks(checks)
    blockers = [item['name'] for item in checks if str(item.get('status')) == 'error']
    warnings = [item['name'] for item in checks if str(item.get('status')) == 'warn']
    actions: list[str] = []
    if 'dataset_ready' in blockers:
        actions.append('Execute natbin.collect_recent e natbin.make_dataset para reconstruir o dataset.')
    if 'market_context' in blockers:
        actions.append('Execute natbin.refresh_market_context ou runtime_app observe --once para regenerar o market_context.')
    if 'broker_preflight' in blockers:
        actions.append('Verifique THALOR_SECRETS_FILE / arquivos de credenciais e rode runtime_app doctor --probe-broker.')
    if 'failsafe_kill_switch' in blockers:
        actions.append('Desative o kill-switch se a operação live estiver autorizada.')
    if 'intelligence_surface' in warnings:
        actions.append('Revise runtime_app intelligence / portfolio status e regenere os artifacts de intelligence se necessário.')
    if 'retention_backlog' in warnings:
        actions.append('Execute runtime_app retention --apply para remover artefatos antigos.')

    ready_for_practice = severity == 'ok' and execution_live and execution_account_mode == 'PRACTICE' and broker_balance_mode == 'PRACTICE' and not kill_active and broker_health is not None and bool(broker_health.get('ready'))
    ready_for_real = severity == 'ok' and execution_live and execution_account_mode == 'REAL' and broker_balance_mode == 'REAL' and not kill_active and broker_health is not None and bool(broker_health.get('ready'))
    payload = {
        'at_utc': now_utc.isoformat(timespec='seconds'),
        'kind': 'production_doctor',
        'ok': severity != 'error',
        'severity': severity,
        'ready_for_cycle': severity != 'error',
        'ready_for_live': ready_for_practice or ready_for_real,
        'ready_for_practice': ready_for_practice,
        'ready_for_real': ready_for_real,
        'probe_broker': bool(probe_broker),
        'strict_runtime_artifacts': bool(strict_runtime_artifacts),
        'enforce_live_broker_prereqs': bool(enforce_live_broker_prereqs),
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': ctx.scope.scope_tag,
        },
        'execution': {
            'enabled': bool(exec_cfg.get('enabled')),
            'mode': str(exec_cfg.get('mode') or 'disabled'),
            'provider': str(exec_cfg.get('provider') or 'fake'),
            'account_mode': execution_account_mode,
        },
        'broker': {
            'provider': str(broker_cfg.get('provider') or exec_cfg.get('provider') or 'unknown'),
            'balance_mode': broker_balance_mode,
        },
        'checks': checks,
        'blockers': blockers,
        'warnings': warnings,
        'actions': actions,
        'broker_health': broker_health,
        'intelligence': {
            'enabled': intelligence.get('enabled'),
            'severity': intelligence.get('severity'),
            'warnings': intelligence.get('warnings'),
            'summary': intelligence.get('summary'),
            'allocation': intelligence.get('allocation'),
            'execution': intelligence.get('execution'),
        },
        'retention_preview': {
            'candidates_total': int(retention.get('candidates_total') or 0),
            'categories': retention.get('categories') or {},
        },
    }
    if write_artifact:
        write_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='doctor', payload=payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Build a production-hardening doctor payload for the current scope')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--probe-broker', action='store_true')
    ap.add_argument('--relaxed', action='store_true', help='Downgrade missing runtime artifacts to skip instead of error')
    ap.add_argument('--market-context-max-age-sec', type=int, default=None)
    ap.add_argument('--min-dataset-rows', type=int, default=100)
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_production_doctor_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        probe_broker=bool(ns.probe_broker),
        strict_runtime_artifacts=not bool(ns.relaxed),
        market_context_max_age_sec=ns.market_context_max_age_sec,
        min_dataset_rows=int(ns.min_dataset_rows),
        write_artifact=True,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
