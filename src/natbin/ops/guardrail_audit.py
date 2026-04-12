from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..control.commands import _evaluate_precheck
from ..control.plan import build_context
from ..ops.audit_common import summarize_status
from ..ops.diagnostic_utils import check, dedupe_actions, load_selected_scopes
from ..runtime.execution_hardening import evaluate_execution_hardening
from ..runtime.failsafe import CircuitBreakerPolicy, RuntimeFailsafe
from ..state.control_repo import RuntimeControlRepository, write_control_artifact, write_repo_control_artifact


def _failsafe_from_context(ctx, repo: Path) -> RuntimeFailsafe:
    cfg = dict(ctx.resolved_config or {})
    fs = dict(cfg.get('failsafe') or {})
    kill_file = Path(str(fs.get('kill_switch_file') or 'runs/KILL_SWITCH'))
    if not kill_file.is_absolute():
        kill_file = repo / kill_file
    drain_file = Path(str(fs.get('drain_mode_file') or 'runs/DRAIN_MODE'))
    if not drain_file.is_absolute():
        drain_file = repo / drain_file
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


def _scope_payload(*, repo: Path, cfg_path: Path, asset: str, interval_sec: int) -> dict[str, Any]:
    ctx = build_context(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)
    current = datetime.now(tz=UTC)
    checks: list[dict[str, Any]] = []
    actions: list[str] = []

    fs = _failsafe_from_context(ctx, repo)
    kill_active, kill_reason = fs.is_kill_switch_active(dict(os.environ))
    drain_active, drain_reason = fs.is_drain_mode_active(dict(os.environ))
    if kill_active:
        checks.append(check('kill_switch', 'error', 'Kill-switch ativo', reason=kill_reason))
        actions.append('Desative o kill-switch antes de tentar operar.')
    else:
        checks.append(check('kill_switch', 'ok', 'Kill-switch desligado'))
    if drain_active:
        checks.append(check('drain_mode', 'warn', 'Drain mode ativo', reason=drain_reason))
        actions.append('Desative o drain mode quando quiser permitir ciclos reais de operação.')
    else:
        checks.append(check('drain_mode', 'ok', 'Drain mode desligado'))

    control_repo = RuntimeControlRepository(repo / 'runs' / 'runtime_control.sqlite3')
    breaker = fs.evaluate_circuit(control_repo.load_breaker(asset, int(interval_sec)), current)
    if str(breaker.state) == 'open':
        checks.append(check('circuit_breaker', 'error', 'Circuit breaker aberto', reason=breaker.reason, opened_until_utc=breaker.opened_until_utc.isoformat() if breaker.opened_until_utc else None))
        actions.append('Aguarde o cooldown do circuit breaker ou limpe a causa primária antes de novo ciclo.')
    elif str(breaker.state) == 'half_open':
        checks.append(check('circuit_breaker', 'warn', 'Circuit breaker em half-open', reason=breaker.reason))
    else:
        checks.append(check('circuit_breaker', 'ok', 'Circuit breaker fechado'))

    exec_cfg = dict(ctx.resolved_config.get('execution') or {})
    broker_cfg = dict(ctx.resolved_config.get('broker') or {})
    exec_mode = str(exec_cfg.get('account_mode') or 'PRACTICE').upper()
    broker_mode = str(broker_cfg.get('balance_mode') or exec_mode).upper()
    if exec_mode != broker_mode:
        checks.append(check('mode_alignment', 'error', 'execution.account_mode diverge de broker.balance_mode', execution_account_mode=exec_mode, broker_balance_mode=broker_mode))
        actions.append('Alinhe execution.account_mode e broker.balance_mode no profile canônico.')
    else:
        checks.append(check('mode_alignment', 'ok', 'execution.account_mode alinhado com broker.balance_mode', account_mode=exec_mode))

    precheck = _evaluate_precheck(ctx=ctx, topk=3, sleep_align_offset_sec=3, now_utc=current, enforce_market_context=True)
    blocked = bool(precheck.get('blocked'))
    reason = str(precheck.get('reason') or '')
    if blocked:
        if reason == 'market_closed':
            checks.append(check('precheck', 'ok', 'Precheck bloqueado apenas pela janela atual de mercado; não é falha estrutural', reason=reason, next_wake_utc=precheck.get('next_wake_utc'), operational_no_trade=True))
        else:
            status = 'warn' if drain_active and drain_reason and drain_reason in reason else 'error'
            checks.append(check('precheck', status, 'Precheck bloqueado', reason=reason, next_wake_utc=precheck.get('next_wake_utc')))
    else:
        checks.append(check('precheck', 'ok', 'Precheck liberado', next_wake_utc=precheck.get('next_wake_utc')))

    hardening = evaluate_execution_hardening(repo_root=repo, ctx=ctx, write_artifact=True).as_dict()
    if hardening.get('allowed') is False:
        status = 'error' if bool(hardening.get('live_real_mode')) else 'warn'
        checks.append(check('execution_hardening', status, 'Execution hardening bloqueou ou limitou o scope', reason=hardening.get('reason'), details=hardening.get('details') or {}))
        actions.append('Revise execution.real_guard e a saúde do runtime_execution.sqlite3 antes de liberar submits reais.')
    else:
        checks.append(check('execution_hardening', 'ok', 'Execution hardening permite o scope', reason=hardening.get('reason')))

    severity = summarize_status(checks)
    payload = {
        'kind': 'guardrail_audit',
        'at_utc': current.isoformat(timespec='seconds'),
        'ok': severity != 'error',
        'severity': severity,
        'scope': {'asset': asset, 'interval_sec': int(interval_sec), 'scope_tag': str(ctx.scope.scope_tag)},
        'checks': checks,
        'precheck': precheck,
        'execution_hardening': hardening,
        'actions': dedupe_actions(actions),
    }
    write_control_artifact(repo_root=repo, asset=asset, interval_sec=interval_sec, name='guardrail_audit', payload=payload)
    return payload


def build_guardrail_audit_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = False,
    write_artifact: bool = True,
) -> dict[str, Any]:
    repo, cfg_path, cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    results = [_scope_payload(repo=repo, cfg_path=cfg_path, asset=str(scope.asset), interval_sec=int(scope.interval_sec)) for scope in scopes]
    scope_severities = [str(item.get('severity') or 'ok') for item in results]
    severity = 'error' if 'error' in scope_severities else ('warn' if 'warn' in scope_severities else 'ok')
    payload = {
        'kind': 'guardrail_audit',
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'summary': {
            'scope_count': len(results),
            'multi_asset_enabled': bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
            'error_scopes': [item['scope']['scope_tag'] for item in results if str(item.get('severity')) == 'error'],
            'warn_scopes': [item['scope']['scope_tag'] for item in results if str(item.get('severity')) == 'warn'],
        },
        'scope_results': results,
        'actions': dedupe_actions([action for result in results for action in list(result.get('actions') or [])]),
    }
    if write_artifact:
        write_repo_control_artifact(repo_root=repo, name='guardrail_audit', payload=payload)
    return payload

