from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable

from ..control.plan import build_context
from ..security.audit import audit_security_posture
from ..security.redaction import collect_sensitive_values, sanitize_payload
from .config_provenance import build_config_provenance_payload
from .dependency_audit import build_dependency_audit_payload
from .diag_cli_common import (
    add_output_args,
    add_repo_config_args,
    add_scope_args,
    build_logger,
    exception_payload,
    exit_code_from_payload,
    log_event,
    maybe_append_log,
    print_payload,
    utc_now_iso,
    write_json,
    write_repo_artifact,
)
from .guardrail_audit import build_guardrail_audit_payload
from .practice_readiness import build_practice_readiness_payload
from .production_doctor import build_production_doctor_payload
from .provider_probe import build_provider_probe_payload
from .runtime_artifact_audit import build_runtime_artifact_audit_payload
from .state_db_audit import build_state_db_audit_payload
from .safe_refresh import maybe_heal_breaker, maybe_heal_control_freshness, maybe_heal_market_context
from .support_bundle import build_support_bundle_payload


def _first_text(values: Iterable[Any]) -> str | None:
    for value in values:
        if value in (None, ''):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _tool_severity(payload: dict[str, Any]) -> str:
    raw = payload.get('severity')
    if raw not in (None, ''):
        return str(raw)
    return 'ok' if bool(payload.get('ok', True)) else 'error'


def _tool_status(payload: dict[str, Any]) -> str:
    severity = _tool_severity(payload)
    if severity == 'error':
        return 'error'
    if severity in {'warn', 'warning'}:
        return 'warn'
    return 'ok'


def _tool_message(name: str, payload: dict[str, Any]) -> str:
    candidates = [
        payload.get('message'),
        (payload.get('summary') or {}).get('message') if isinstance(payload.get('summary'), dict) else None,
        payload.get('kind'),
    ]
    text = _first_text(candidates)
    if text:
        return text
    blockers = list(payload.get('blockers') or [])
    warnings = list(payload.get('warnings') or [])
    if blockers:
        return f'{name} com blockers: {", ".join(str(item) for item in blockers[:8])}'
    if warnings:
        return f'{name} com warnings: {", ".join(str(item) for item in warnings[:8])}'
    return f'{name} executado'


def _collect_actions(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def _push_many(values: Any) -> None:
        if isinstance(values, list):
            for value in values:
                text = str(value or '').strip()
                if text and text not in out:
                    out.append(text)

    _push_many(payload.get('actions'))
    _push_many(payload.get('manual_checks'))

    validation = payload.get('validation')
    if isinstance(validation, dict):
        _push_many(validation.get('manual_checks'))

    release = payload.get('release')
    if isinstance(release, dict):
        _push_many(release.get('actions'))

    return out


def build_diag_suite_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = False,
    include_provider_probe: bool = False,
    active_provider_probe: bool = False,
    include_practice: bool = False,
    include_support_bundle: bool = False,
    probe_broker: bool = False,
    sample_candles: int = 3,
    market_context_max_age_sec: int | None = None,
    min_dataset_rows: int = 100,
    heal_breaker: bool = True,
    breaker_stale_after_sec: int | None = None,
    heal_market_context: bool = False,
    heal_control_freshness: bool = False,
    max_stake_amount: float = 5.0,
    soak_stale_after_sec: int | None = None,
    support_bundle_output_dir: str | Path | None = None,
    max_log_bytes: int = 500_000,
    dry_run: bool = False,
    logger=None,
) -> dict[str, Any]:
    ctx = build_context(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        dump_snapshot=False,
    )
    repo = Path(ctx.repo_root).resolve()
    cfg_path = Path(ctx.config.config_path).resolve()
    sensitive_values = collect_sensitive_values(ctx.resolved_config)

    log_event(
        logger,
        'diag_suite_start',
        repo_root=str(repo),
        config_path=str(cfg_path),
        all_scopes=bool(all_scopes),
        include_provider_probe=bool(include_provider_probe),
        include_practice=bool(include_practice),
        include_support_bundle=bool(include_support_bundle),
        dry_run=bool(dry_run),
        heal_breaker=bool(heal_breaker),
    )

    breaker_repair = maybe_heal_breaker(
        repo_root=repo,
        config_path=cfg_path,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        enabled=bool(heal_breaker),
        dry_run=bool(dry_run),
        stale_after_sec=breaker_stale_after_sec,
    )

    max_age = int(market_context_max_age_sec or max(int(ctx.config.interval_sec) * 3, 900))
    freshness_limit = max(int(ctx.config.interval_sec) * 4, 900)
    market_context_repair = maybe_heal_market_context(
        repo_root=repo,
        config_path=cfg_path,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        max_age_sec=max_age,
        enabled=bool(heal_market_context),
        dry_run=bool(dry_run),
    )
    control_freshness_repair = maybe_heal_control_freshness(
        repo_root=repo,
        config_path=cfg_path,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        freshness_limit_sec=int(freshness_limit),
        enabled=bool(heal_control_freshness),
        dry_run=bool(dry_run),
    )

    security = audit_security_posture(
        repo_root=repo,
        config_path=cfg_path,
        resolved_config=ctx.resolved_config,
        source_trace=list(ctx.source_trace),
    )

    results: dict[str, dict[str, Any]] = {
        'security': security,
        'config_provenance_audit': build_config_provenance_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            write_artifact=not dry_run,
        ),
        'dependency_audit': build_dependency_audit_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            write_artifact=not dry_run,
        ),
        'runtime_artifact_audit': build_runtime_artifact_audit_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            write_artifact=not dry_run,
        ),
        'guardrail_audit': build_guardrail_audit_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            write_artifact=not dry_run,
        ),
        'state_db_audit': build_state_db_audit_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            write_artifact=not dry_run,
        ),
        'doctor': build_production_doctor_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            probe_broker=bool(probe_broker) and not dry_run,
            strict_runtime_artifacts=True,
            enforce_live_broker_prereqs=True,
            market_context_max_age_sec=market_context_max_age_sec,
            min_dataset_rows=int(min_dataset_rows),
            heal_market_context=bool(heal_market_context),
            heal_control_freshness=bool(heal_control_freshness),
            write_artifact=not dry_run,
        ),
    }

    if include_provider_probe:
        results['provider_probe'] = build_provider_probe_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            active=bool(active_provider_probe) and not dry_run,
            sample_candles=max(0, int(sample_candles)),
            probe_market_context=bool(active_provider_probe) and not dry_run,
            market_context_max_age_sec=market_context_max_age_sec,
            write_artifact=not dry_run,
        )

    if include_practice:
        results['practice'] = build_practice_readiness_payload(
            repo_root=repo,
            config_path=cfg_path,
            max_stake_amount=float(max_stake_amount),
            soak_stale_after_sec=soak_stale_after_sec,
            heal_market_context=bool(heal_market_context),
            heal_control_freshness=bool(heal_control_freshness),
            write_artifact=not dry_run,
        )

    if include_support_bundle:
        results['support_bundle'] = build_support_bundle_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            probe_provider=bool(active_provider_probe) and not dry_run,
            sample_candles=max(0, int(sample_candles)),
            market_context_max_age_sec=market_context_max_age_sec,
            min_dataset_rows=int(min_dataset_rows),
            include_logs=True,
            max_log_bytes=int(max_log_bytes),
            output_dir=support_bundle_output_dir,
            bundle_prefix='diag_suite_bundle',
            write_artifact=not dry_run,
        )

    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    actions: list[str] = []

    for name, payload in results.items():
        status = _tool_status(payload)
        message = _tool_message(name, payload)
        item = {
            'name': name,
            'status': status,
            'message': message,
            'severity': _tool_severity(payload),
            'kind': payload.get('kind'),
            'ok': bool(payload.get('ok', status != 'error')),
        }
        if payload.get('blockers'):
            item['blockers'] = list(payload.get('blockers') or [])
        if payload.get('warnings'):
            item['warnings'] = list(payload.get('warnings') or [])
        if name == 'practice':
            item['ready_for_practice'] = bool(payload.get('ready_for_practice'))
        checks.append(item)
        if status == 'error':
            blockers.append(name)
        elif status == 'warn':
            warnings.append(name)
        for action in _collect_actions(payload):
            if action not in actions:
                actions.append(action)

    severity = 'error' if blockers else ('warn' if warnings else 'ok')
    practice_ready = None
    if 'practice' in results:
        practice_ready = bool(results['practice'].get('ready_for_practice'))

    payload = {
        'kind': 'diag_suite',
        'at_utc': utc_now_iso(),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': str(ctx.scope.scope_tag),
        },
        'source_trace': list(ctx.source_trace),
        'dry_run': bool(dry_run),
        'checks': checks,
        'blockers': blockers,
        'warnings': warnings,
        'actions': actions,
        'repairs': {
            'circuit_breaker': breaker_repair,
            'market_context': market_context_repair,
            'control_freshness': control_freshness_repair,
        },
        'ready_for_cycle': severity != 'error',
        'ready_for_practice': practice_ready,
        'results': sanitize_payload(results, sensitive_values=sensitive_values),
    }

    payload = sanitize_payload(payload, sensitive_values=sensitive_values)
    if not dry_run:
        try:
            write_repo_artifact(repo, 'diag_suite', payload)
        except Exception:
            pass
    log_event(
        logger,
        'diag_suite_complete',
        severity=payload.get('severity'),
        blockers=payload.get('blockers'),
        warnings=payload.get('warnings'),
    )
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Consolida os audits operacionais do Thalor em um único payload.')
    parser.set_defaults(heal_breaker=True, heal_market_context=True, heal_control_freshness=True)
    add_repo_config_args(parser)
    add_scope_args(parser, all_scopes=True)
    add_output_args(parser)
    parser.add_argument('--include-provider-probe', action='store_true', help='Inclui runtime provider-probe na suíte')
    parser.add_argument('--active-provider-probe', action='store_true', help='Permite probes remotos do provider (nunca envia ordens)')
    parser.add_argument('--include-practice', action='store_true', help='Inclui readiness específica de PRACTICE')
    parser.add_argument('--include-support-bundle', action='store_true', help='Inclui geração de support-bundle sanitizado')
    parser.add_argument('--probe-broker', action='store_true', help='Permite broker preflight ativo dentro do doctor')
    parser.add_argument('--sample-candles', type=int, default=3, help='Qtde de candles no provider-probe')
    parser.add_argument('--market-context-max-age-sec', type=int, default=None)
    parser.add_argument('--min-dataset-rows', type=int, default=100)
    parser.add_argument('--heal-breaker', dest='heal_breaker', action='store_true', help='Reseta com segurança breaker stale em open/half-open antes da suíte (padrão).')
    parser.add_argument('--no-heal-breaker', dest='heal_breaker', action='store_false', help='Não tenta reparar automaticamente breaker stale.')
    parser.add_argument('--breaker-stale-after-sec', type=int, default=None)
    parser.add_argument('--heal-market-context', dest='heal_market_context', action='store_true', help='Tenta regenerar o market_context antes de executar a suíte (safe self-heal).')
    parser.add_argument('--no-heal-market-context', dest='heal_market_context', action='store_false', help='Não tenta regenerar automaticamente o market_context.')
    parser.add_argument('--heal-control-freshness', dest='heal_control_freshness', action='store_true', help='Tenta materializar loop_status/health com observe seguro em drain mode antes da suíte.')
    parser.add_argument('--no-heal-control-freshness', dest='heal_control_freshness', action='store_false', help='Não tenta reparar automaticamente loop_status/health.')
    parser.add_argument('--max-stake-amount', type=float, default=5.0)
    parser.add_argument('--soak-stale-after-sec', type=int, default=None)
    parser.add_argument('--support-bundle-output-dir', default=None)
    parser.add_argument('--max-log-bytes', type=int, default=500_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    logger = build_logger('natbin.diag_suite', verbose=bool(ns.verbose))
    try:
        payload = build_diag_suite_payload(
            repo_root=ns.repo_root,
            config_path=ns.config,
            asset=getattr(ns, 'asset', None),
            interval_sec=getattr(ns, 'interval_sec', None),
            all_scopes=bool(getattr(ns, 'all_scopes', False)),
            include_provider_probe=bool(getattr(ns, 'include_provider_probe', False)),
            active_provider_probe=bool(getattr(ns, 'active_provider_probe', False)),
            include_practice=bool(getattr(ns, 'include_practice', False)),
            include_support_bundle=bool(getattr(ns, 'include_support_bundle', False)),
            probe_broker=bool(getattr(ns, 'probe_broker', False)),
            sample_candles=int(getattr(ns, 'sample_candles', 3) or 0),
            market_context_max_age_sec=getattr(ns, 'market_context_max_age_sec', None),
            min_dataset_rows=int(getattr(ns, 'min_dataset_rows', 100) or 100),
            heal_breaker=bool(getattr(ns, 'heal_breaker', True)),
            breaker_stale_after_sec=getattr(ns, 'breaker_stale_after_sec', None),
            heal_market_context=bool(getattr(ns, 'heal_market_context', False)),
            heal_control_freshness=bool(getattr(ns, 'heal_control_freshness', False)),
            max_stake_amount=float(getattr(ns, 'max_stake_amount', 5.0) or 5.0),
            soak_stale_after_sec=getattr(ns, 'soak_stale_after_sec', None),
            support_bundle_output_dir=getattr(ns, 'support_bundle_output_dir', None),
            max_log_bytes=int(getattr(ns, 'max_log_bytes', 500_000) or 500_000),
            dry_run=bool(getattr(ns, 'dry_run', False)),
            logger=logger,
        )
    except Exception as exc:  # pragma: no cover - defensive CLI envelope
        payload = exception_payload('diag_suite', exc)
        print_payload(payload, as_json=True)
        return 2

    if ns.output:
        write_json(ns.output, payload)
    maybe_append_log(getattr(ns, 'log_jsonl_path', None), payload)
    print_payload(payload, as_json=bool(ns.json))
    return exit_code_from_payload(payload)


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
