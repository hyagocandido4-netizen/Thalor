from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config.execution_mode import execution_mode_uses_broker_submit

from ..alerting.telegram import alerts_status_payload
from ..config.loader import load_thalor_config
from ..control.ops import gate_status
from ..control.plan import build_context
from ..state.control_repo import write_control_artifact


SOAK_DIR_REL = Path('runs') / 'soak'
DEFAULT_MAX_STAKE = 5.0


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _fmt_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec='seconds')


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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _check(name: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {'name': str(name), 'status': str(status), 'message': str(message)}
    if extra:
        item.update(extra)
    return item


def _severity_from_checks(checks: list[dict[str, Any]]) -> str:
    if any(str(item.get('status')) == 'error' for item in checks):
        return 'error'
    if any(str(item.get('status')) == 'warn' for item in checks):
        return 'warn'
    return 'ok'


def _soak_path(repo: Path, scope_tag: str) -> Path:
    return repo / SOAK_DIR_REL / f'soak_latest_{scope_tag}.json'


def _soak_summary(*, repo: Path, scope_tag: str, stale_after_sec: int) -> dict[str, Any]:
    path = _soak_path(repo, scope_tag)
    payload = _read_json(path) if path.exists() else None
    stamp = _parse_iso((payload or {}).get('at_utc')) if isinstance(payload, dict) else None
    if stamp is None and path.exists():
        try:
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except Exception:
            stamp = None
    age_sec = None if stamp is None else max(0, int((_now_utc() - stamp).total_seconds()))
    exit_code = _safe_int((payload or {}).get('exit_code')) if isinstance(payload, dict) else None
    cycles_requested = _safe_int((payload or {}).get('cycles_requested')) if isinstance(payload, dict) else None
    cycles_completed = _safe_int((payload or {}).get('cycles_completed')) if isinstance(payload, dict) else None
    freshness = dict((payload or {}).get('freshness') or {}) if isinstance(payload, dict) else {}
    guard = (payload or {}).get('guard') if isinstance(payload, dict) else None
    status = 'warn'
    message = 'Nenhum soak recente encontrado'
    if path.exists() and isinstance(payload, dict):
        if exit_code == 0 and (cycles_completed or 0) >= 1 and (age_sec is not None and age_sec <= max(60, int(stale_after_sec))):
            status = 'ok'
            message = 'Soak recente concluído com sucesso'
        elif exit_code == 0 and (cycles_completed or 0) >= 1:
            status = 'warn'
            message = 'Soak encontrado, mas ficou stale'
        elif exit_code not in (None, 0):
            status = 'error'
            message = 'Soak recente terminou com erro'
        else:
            status = 'warn'
            message = 'Soak encontrado, porém incompleto'
    return {
        'path': str(path),
        'exists': bool(path.exists()),
        'status': status,
        'message': message,
        'at_utc': _fmt_utc(stamp),
        'age_sec': age_sec,
        'stale_after_sec': max(60, int(stale_after_sec)),
        'exit_code': exit_code,
        'cycles_requested': cycles_requested,
        'cycles_completed': cycles_completed,
        'freshness': freshness,
        'guard': guard,
        'payload': payload,
    }


def build_practice_readiness_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    max_stake_amount: float = DEFAULT_MAX_STAKE,
    soak_stale_after_sec: int | None = None,
    write_artifact: bool = True,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    resolved = dict(ctx.resolved_config or {})
    exec_cfg = dict(resolved.get('execution') or {})
    broker_cfg = dict(resolved.get('broker') or {})
    multi_asset = dict(resolved.get('multi_asset') or {})
    notifications = dict(resolved.get('notifications') or {})
    telegram_cfg = dict(notifications.get('telegram') or {})
    thalor_cfg = load_thalor_config(config_path=ctx.config.config_path, repo_root=repo)
    assets = list(getattr(thalor_cfg, 'assets', []) or [])

    execution_live = bool(exec_cfg.get('enabled')) and execution_mode_uses_broker_submit(exec_cfg.get('mode')) and str(exec_cfg.get('provider') or 'fake') == 'iqoption'
    execution_account_mode = str(exec_cfg.get('account_mode') or 'PRACTICE').upper()
    broker_balance_mode = str(broker_cfg.get('balance_mode') or execution_account_mode or 'PRACTICE').upper()
    stake = dict(exec_cfg.get('stake') or {})
    limits = dict(exec_cfg.get('limits') or {})
    guard_cfg = dict((resolved.get('security') or {}).get('guard') or {}) if isinstance(resolved.get('security'), dict) else {}
    if not guard_cfg:
        security = resolved.get('security')
        if hasattr(security, 'get'):
            guard_cfg = dict(security.get('guard') or {})
    stale_after = int(soak_stale_after_sec or max(int(ctx.config.interval_sec) * 6, 1800))

    checks: list[dict[str, Any]] = []

    if execution_live:
        checks.append(_check('execution_mode', 'ok', 'Execução live IQ habilitada para practice', provider=exec_cfg.get('provider'), mode=exec_cfg.get('mode')))
    elif bool(exec_cfg.get('enabled')):
        checks.append(_check('execution_mode', 'error', 'Execução habilitada fora do modo live IQ', provider=exec_cfg.get('provider'), mode=exec_cfg.get('mode')))
    else:
        checks.append(_check('execution_mode', 'error', 'Execução desabilitada; controlled practice exige live IQ PRACTICE', provider=exec_cfg.get('provider'), mode=exec_cfg.get('mode')))

    if execution_account_mode == 'PRACTICE':
        checks.append(_check('execution_account_mode', 'ok', 'execution.account_mode em PRACTICE', account_mode=execution_account_mode))
    else:
        checks.append(_check('execution_account_mode', 'error', 'execution.account_mode deve ser PRACTICE para READY-1', account_mode=execution_account_mode))

    if broker_balance_mode == 'PRACTICE':
        checks.append(_check('broker_balance_mode', 'ok', 'broker.balance_mode em PRACTICE', balance_mode=broker_balance_mode))
    else:
        checks.append(_check('broker_balance_mode', 'error', 'broker.balance_mode deve ser PRACTICE para READY-1', balance_mode=broker_balance_mode))

    scope_count = len(assets)
    multi_enabled = bool(multi_asset.get('enabled'))
    max_parallel_assets = _safe_int(multi_asset.get('max_parallel_assets')) or 1
    portfolio_topk_total = _safe_int(multi_asset.get('portfolio_topk_total')) or 1
    if not multi_enabled and scope_count <= 1 and max_parallel_assets <= 1 and portfolio_topk_total <= 1:
        checks.append(_check('controlled_scope', 'ok', 'Escopo controlado (single-asset / single-position)', multi_asset_enabled=multi_enabled, assets=scope_count, max_parallel_assets=max_parallel_assets, portfolio_topk_total=portfolio_topk_total))
    else:
        checks.append(_check('controlled_scope', 'error', 'READY-1 exige escopo controlado: multi_asset off, uma scope e portfolio_topk_total=1', multi_asset_enabled=multi_enabled, assets=scope_count, max_parallel_assets=max_parallel_assets, portfolio_topk_total=portfolio_topk_total))

    stake_amount = _safe_float(stake.get('amount'))
    if stake_amount is None or stake_amount <= 0:
        checks.append(_check('controlled_stake', 'error', 'Stake inválida para controlled practice', stake_amount=stake_amount, recommended_max=max_stake_amount))
    elif stake_amount <= float(max_stake_amount):
        checks.append(_check('controlled_stake', 'ok', 'Stake dentro do envelope recomendado para practice', stake_amount=stake_amount, recommended_max=max_stake_amount, currency=stake.get('currency')))
    else:
        checks.append(_check('controlled_stake', 'warn', 'Stake acima do envelope recomendado para controlled practice', stake_amount=stake_amount, recommended_max=max_stake_amount, currency=stake.get('currency')))

    max_pending_unknown = _safe_int(limits.get('max_pending_unknown'))
    max_open_positions = _safe_int(limits.get('max_open_positions'))
    if max_pending_unknown in (None, 1) and max_open_positions in (None, 1):
        checks.append(_check('execution_limits', 'ok', 'Execution limits controlados', max_pending_unknown=max_pending_unknown, max_open_positions=max_open_positions))
    else:
        checks.append(_check('execution_limits', 'error', 'READY-1 exige max_pending_unknown=1 e max_open_positions=1', max_pending_unknown=max_pending_unknown, max_open_positions=max_open_positions))

    guard_enabled = bool(guard_cfg.get('enabled', False))
    time_filter_enabled = bool(guard_cfg.get('time_filter_enable', False))
    if guard_enabled and time_filter_enabled:
        checks.append(_check('broker_guard', 'ok', 'Broker guard + time filter habilitados', live_only=guard_cfg.get('live_only'), allowed_start_local=guard_cfg.get('allowed_start_local'), allowed_end_local=guard_cfg.get('allowed_end_local')))
    elif guard_enabled:
        checks.append(_check('broker_guard', 'warn', 'Broker guard habilitado sem time filter', live_only=guard_cfg.get('live_only'), time_filter_enable=time_filter_enabled))
    else:
        checks.append(_check('broker_guard', 'warn', 'Broker guard desabilitado para controlled practice'))

    gates = gate_status(repo_root=repo, config_path=ctx.config.config_path)
    if bool((gates.get('kill_switch') or {}).get('active')):
        checks.append(_check('kill_switch', 'error', 'Kill-switch ativo'))
    else:
        checks.append(_check('kill_switch', 'ok', 'Kill-switch desligado'))
    if bool((gates.get('drain_mode') or {}).get('active')):
        checks.append(_check('drain_mode', 'warn', 'Drain mode ativo; observe --once não enviará novas ordens', reason=(gates.get('drain_mode') or {}).get('reason')))
    else:
        checks.append(_check('drain_mode', 'ok', 'Drain mode desligado'))

    from .production_doctor import build_production_doctor_payload

    doctor = build_production_doctor_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        probe_broker=False,
        strict_runtime_artifacts=True,
        enforce_live_broker_prereqs=True,
        write_artifact=True,
    )
    doctor_sev = str(doctor.get('severity') or 'ok')
    if doctor_sev == 'error':
        checks.append(_check('production_doctor', 'error', 'Production doctor encontrou blockers para practice', blockers=doctor.get('blockers') or [], warnings=doctor.get('warnings') or []))
    elif doctor_sev == 'warn':
        checks.append(_check('production_doctor', 'warn', 'Production doctor com avisos antes do practice', blockers=doctor.get('blockers') or [], warnings=doctor.get('warnings') or []))
    else:
        checks.append(_check('production_doctor', 'ok', 'Production doctor sem blockers para practice', warnings=doctor.get('warnings') or []))

    from .intelligence_surface import build_intelligence_surface_payload

    intelligence = build_intelligence_surface_payload(
        repo_root=repo,
        config_path=ctx.config.config_path,
        write_artifact=True,
    )
    intelligence_enabled = bool(intelligence.get('enabled'))
    intelligence_sev = str(intelligence.get('severity') or 'ok')
    if not intelligence_enabled:
        checks.append(_check('intelligence_surface', 'ok', 'Intelligence desabilitada no profile atual'))
    elif intelligence_sev == 'error':
        checks.append(_check('intelligence_surface', 'error', 'Surface de intelligence encontrou blockers', warnings=intelligence.get('warnings') or []))
    elif intelligence_sev == 'warn':
        checks.append(_check('intelligence_surface', 'warn', 'Surface de intelligence com avisos operacionais', warnings=intelligence.get('warnings') or []))
    else:
        checks.append(_check('intelligence_surface', 'ok', 'Surface de intelligence pronta para controlled practice', summary=intelligence.get('summary') or {}))

    alerts = alerts_status_payload(repo_root=repo, resolved_config=ctx.resolved_config, limit=20)
    tg = dict(alerts.get('telegram') or {})
    if bool(tg.get('enabled')) and bool(tg.get('send_enabled')) and bool(tg.get('credentials_present')):
        checks.append(_check('telegram_alerting', 'ok', 'Telegram pronto para practice', credential_trace=tg.get('credential_trace')))
    elif bool(tg.get('enabled')):
        checks.append(_check('telegram_alerting', 'warn', 'Telegram configurado sem envio ativo ou sem credenciais', credential_trace=tg.get('credential_trace')))
    else:
        checks.append(_check('telegram_alerting', 'ok', 'Telegram desabilitado neste profile', send_enabled=telegram_cfg.get('send_enabled')))

    soak = _soak_summary(repo=repo, scope_tag=str(ctx.scope.scope_tag), stale_after_sec=stale_after)
    checks.append(_check('runtime_soak', str(soak.get('status') or 'warn'), str(soak.get('message') or 'Nenhum soak recente'), path=soak.get('path'), age_sec=soak.get('age_sec'), cycles_completed=soak.get('cycles_completed'), cycles_requested=soak.get('cycles_requested'), exit_code=soak.get('exit_code')))

    from .live_validation import build_validation_plan

    validation_plan = build_validation_plan(
        stage='practice',
        repo_root=repo,
        config_path=str(ctx.config.config_path),
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        include_baseline_tests=False,
    )

    severity = _severity_from_checks(checks)
    ready_for_practice = severity == 'ok'
    payload = {
        'at_utc': _fmt_utc(_now_utc()),
        'kind': 'practice_readiness',
        'ok': severity != 'error',
        'severity': severity,
        'ready_for_practice': ready_for_practice,
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
            'stake_amount': stake_amount,
            'stake_currency': stake.get('currency'),
            'limits': {
                'max_pending_unknown': max_pending_unknown,
                'max_open_positions': max_open_positions,
            },
        },
        'broker': {
            'provider': str(broker_cfg.get('provider') or exec_cfg.get('provider') or 'unknown'),
            'balance_mode': broker_balance_mode,
        },
        'controlled_scope': {
            'multi_asset_enabled': multi_enabled,
            'assets_configured': scope_count,
            'max_parallel_assets': max_parallel_assets,
            'portfolio_topk_total': portfolio_topk_total,
        },
        'checks': checks,
        'gates': gates,
        'doctor': {
            'severity': doctor.get('severity'),
            'ready_for_cycle': doctor.get('ready_for_cycle'),
            'ready_for_live': doctor.get('ready_for_live'),
            'ready_for_practice': doctor.get('ready_for_practice'),
            'ready_for_real': doctor.get('ready_for_real'),
            'warnings': doctor.get('warnings'),
            'blockers': doctor.get('blockers'),
        },
        'intelligence': {
            'enabled': intelligence.get('enabled'),
            'severity': intelligence.get('severity'),
            'warnings': intelligence.get('warnings'),
            'summary': intelligence.get('summary'),
            'allocation': intelligence.get('allocation'),
            'execution': intelligence.get('execution'),
        },
        'alerts': alerts,
        'soak': {
            'status': soak.get('status'),
            'message': soak.get('message'),
            'path': soak.get('path'),
            'at_utc': soak.get('at_utc'),
            'age_sec': soak.get('age_sec'),
            'stale_after_sec': soak.get('stale_after_sec'),
            'exit_code': soak.get('exit_code'),
            'cycles_requested': soak.get('cycles_requested'),
            'cycles_completed': soak.get('cycles_completed'),
            'freshness': soak.get('freshness'),
            'guard': soak.get('guard'),
        },
        'validation': {
            'stage': validation_plan.stage,
            'manual_checks': list(validation_plan.manual_checks),
            'dangerous_stage': bool(validation_plan.dangerous_stage),
            'specs': [
                {
                    'name': item.name,
                    'required': bool(item.required),
                    'note': item.note,
                    'potentially_submits': bool(item.potentially_submits),
                    'cmd': list(item.cmd),
                }
                for item in list(validation_plan.specs)
            ],
        },
    }
    if write_artifact:
        write_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='practice', payload=payload)
    return payload


__all__ = ['build_practice_readiness_payload', 'DEFAULT_MAX_STAKE']
