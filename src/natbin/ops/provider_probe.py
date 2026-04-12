from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from ..config.execution_mode import execution_mode_uses_broker_submit
from ..control.plan import build_context
from ..runtime.broker_dependency import candle_db_snapshot, read_cached_json
from ..runtime.broker_surface import adapter_from_context, broker_cfg, execution_cfg
from ..runtime.scope import market_context_path
from ..security.audit import audit_security_posture
from ..security.secrets import resolve_secret_bundle_path
from ..state.control_repo import write_control_artifact, write_repo_control_artifact
from .diagnostic_utils import (
    age_sec_from_iso,
    check,
    dedupe_actions,
    load_selected_scopes,
    now_utc,
    resolve_path,
    resolve_scope_paths,
    severity_from_checks,
)


_TRANSPORT_SCHEMES = ('socks://', 'socks4://', 'socks5://', 'socks5h://', 'http://', 'https://')


def _secret_bundle_path(ctx, repo: Path) -> Path | None:
    security = dict(ctx.resolved_config.get('security') or {})
    return resolve_secret_bundle_path(repo_root=repo, security=security)


def _read_bundle(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return {}
    if path.suffix.lower() in {'.yaml', '.yml'} and yaml is not None:
        try:
            raw = yaml.safe_load(text) or {}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}
    if path.suffix.lower() == '.json':
        try:
            raw = json.loads(text)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}
    return {}


def _bundle_get(bundle: Mapping[str, Any], *paths: tuple[str, ...]) -> Any | None:
    for path in paths:
        node: Any = bundle
        ok = True
        for part in path:
            if isinstance(node, Mapping) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if ok:
            return node
    return None


def _bundle_balance_mode_hint(ctx, repo: Path) -> dict[str, Any] | None:
    path = _secret_bundle_path(ctx, repo)
    bundle = _read_bundle(path)
    if not bundle:
        return None
    value = _bundle_get(bundle, ('broker', 'balance_mode'), ('IQ_BALANCE_MODE',), ('balance_mode',))
    if value in (None, ''):
        return None
    return {
        'path': str(path) if path is not None else None,
        'value': str(value).strip().upper(),
        'present': True,
    }


def _transport_hint(ctx, repo: Path) -> dict[str, Any] | None:
    bundle = _read_bundle(_secret_bundle_path(ctx, repo))
    candidates = [
        _bundle_get(bundle, ('transport', 'endpoint')),
        _bundle_get(bundle, ('transport_endpoint',)),
        _bundle_get(bundle, ('network', 'transport', 'endpoint')),
    ]
    env_candidates = [
        os.getenv('TRANSPORT_ENDPOINT'),
        os.getenv('HTTP_PROXY'),
        os.getenv('HTTPS_PROXY'),
        os.getenv('ALL_PROXY'),
    ]
    file_candidates = [
        repo / 'secrets' / 'transport_endpoint',
        repo / 'secrets' / 'transport_endpoints',
    ]
    endpoint = None
    source = None
    for value in candidates + env_candidates:
        text = str(value or '').strip()
        if text:
            endpoint = text
            source = 'env_or_bundle'
            break
    if endpoint is None:
        for path in file_candidates:
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding='utf-8').strip()
            except Exception:
                continue
            if text:
                endpoint = text
                source = f'file:{path.name}'
                break
    if endpoint is None:
        return None
    lowered = endpoint.lower()
    scheme = next((item[:-3] if item.endswith('://') else item for item in _TRANSPORT_SCHEMES if lowered.startswith(item)), 'unknown')
    uses_socks = scheme.startswith('socks')
    pysocks_available = True
    pysocks_reason = None
    if uses_socks:
        try:
            import socks  # type: ignore  # noqa: F401
        except Exception as exc:  # pragma: no cover - env dependent
            pysocks_available = False
            pysocks_reason = f'{type(exc).__name__}: {exc}'
    return {
        'configured': True,
        'source': source,
        'scheme': scheme,
        'uses_socks': uses_socks,
        'pysocks_available': pysocks_available,
        'pysocks_reason': pysocks_reason,
    }


def _market_context_summary(*, path: Path, interval_sec: int, now_ts: float, max_age_sec: int) -> dict[str, Any]:
    payload = read_cached_json(path)
    age_sec = age_sec_from_iso((payload or {}).get('at_utc'), now=now_utc()) if isinstance(payload, dict) else None
    out = {
        'path': str(path),
        'exists': path.exists(),
        'fresh': False,
        'age_sec': age_sec,
        'max_age_sec': int(max_age_sec),
        'dependency_available': None,
        'dependency_reason': None,
        'market_open': None,
        'open_source': None,
        'payout': None,
        'payout_source': None,
        'last_candle_ts': None,
    }
    if not isinstance(payload, dict):
        return out
    out.update(
        {
            'dependency_available': payload.get('dependency_available'),
            'dependency_reason': payload.get('dependency_reason'),
            'market_open': payload.get('market_open'),
            'open_source': payload.get('open_source'),
            'payout': payload.get('payout'),
            'payout_source': payload.get('payout_source'),
            'last_candle_ts': payload.get('last_candle_ts'),
            'fresh': bool(age_sec is not None and age_sec <= max_age_sec),
        }
    )
    return out


def _db_snapshot_summary(*, db_path: Path, asset: str, interval_sec: int, now_ts: float) -> dict[str, Any]:
    out = {
        'path': str(db_path),
        'exists': db_path.exists(),
        'db_rows': 0,
        'last_candle_ts': None,
        'last_candle_age_sec': None,
        'fresh': False,
    }
    if not db_path.exists():
        return out
    try:
        snap = candle_db_snapshot(str(db_path), asset, interval_sec)
    except Exception as exc:
        out['error'] = f'{type(exc).__name__}: {exc}'
        return out
    last_ts = snap.get('last_candle_ts')
    age_sec = max(0.0, float(now_ts) - float(last_ts)) if last_ts not in (None, '') else None
    max_age = max(int(interval_sec) * 2 + 90, int(interval_sec) + 90)
    out.update(
        {
            'db_rows': int(snap.get('db_rows') or 0),
            'last_candle_ts': int(last_ts) if last_ts is not None else None,
            'last_candle_age_sec': age_sec,
            'fresh': bool(age_sec is not None and age_sec <= max_age),
            'max_age_sec': max_age,
        }
    )
    return out


def _scope_probe_from_context(
    *,
    ctx,
    cfg: Any,
    repo: Path,
    shared_client: Any | None,
    shared_session: dict[str, Any],
    sample_candles: int,
    probe_market_context: bool,
    market_context_max_age_sec: int,
) -> dict[str, Any]:
    now = now_utc()
    now_ts = now.timestamp()
    scope_checks: list[dict[str, Any]] = []
    exec_cfg = dict(execution_cfg(ctx) or {})
    broker = dict(broker_cfg(ctx) or {})
    execution_live = bool(exec_cfg.get('enabled')) and execution_mode_uses_broker_submit(exec_cfg.get('mode')) and str(exec_cfg.get('provider') or 'fake') == 'iqoption'
    execution_account_mode = str(exec_cfg.get('account_mode') or 'PRACTICE').upper()
    broker_balance_mode = str(broker.get('balance_mode') or execution_account_mode or 'PRACTICE').upper()

    scope_paths = resolve_scope_paths(repo_root=repo, cfg=cfg, scope=ctx.scope)
    data_paths = scope_paths['data']
    market_path = Path(market_context_path(asset=ctx.config.asset, interval_sec=int(ctx.config.interval_sec), out_dir=repo / 'runs'))
    local_market_context = _market_context_summary(path=market_path, interval_sec=int(ctx.config.interval_sec), now_ts=now_ts, max_age_sec=market_context_max_age_sec)
    db_snapshot = _db_snapshot_summary(db_path=Path(str(data_paths.db_path)), asset=str(ctx.config.asset), interval_sec=int(ctx.config.interval_sec), now_ts=now_ts)

    if execution_account_mode != broker_balance_mode:
        hint = _bundle_balance_mode_hint(ctx, repo)
        message = 'execution.account_mode e broker.balance_mode divergem'
        extra = {'execution_account_mode': execution_account_mode, 'broker_balance_mode': broker_balance_mode}
        if hint is not None:
            extra['suspected_secret_bundle_override'] = hint
            message += '; o secret bundle provavelmente está sobrescrevendo balance_mode'
        scope_checks.append(check('mode_alignment', 'error', message, **extra))
    else:
        scope_checks.append(check('mode_alignment', 'ok', 'execution.account_mode e broker.balance_mode alinhados', execution_account_mode=execution_account_mode, broker_balance_mode=broker_balance_mode))

    if local_market_context.get('exists') and local_market_context.get('fresh'):
        scope_checks.append(check('market_context_local', 'ok', 'market_context local fresco', age_sec=local_market_context.get('age_sec'), dependency_available=local_market_context.get('dependency_available'), open_source=local_market_context.get('open_source')))
    elif local_market_context.get('exists'):
        scope_checks.append(check('market_context_local', 'warn', 'market_context local stale', age_sec=local_market_context.get('age_sec'), max_age_sec=local_market_context.get('max_age_sec'), dependency_available=local_market_context.get('dependency_available'), open_source=local_market_context.get('open_source')))
    else:
        scope_checks.append(check('market_context_local', 'warn', 'market_context local ausente', path=local_market_context.get('path')))

    if db_snapshot.get('exists') and db_snapshot.get('fresh'):
        scope_checks.append(check('candle_db_local', 'ok', 'DB de candles com snapshot fresco', rows=db_snapshot.get('db_rows'), last_candle_age_sec=db_snapshot.get('last_candle_age_sec')))
    elif db_snapshot.get('exists'):
        scope_checks.append(check('candle_db_local', 'warn', 'DB de candles stale ou vazio', rows=db_snapshot.get('db_rows'), last_candle_age_sec=db_snapshot.get('last_candle_age_sec')))
    else:
        scope_checks.append(check('candle_db_local', 'warn', 'DB de candles ausente', path=db_snapshot.get('path')))

    remote_candles: dict[str, Any] = {'attempted': False, 'ok': False, 'reason': None}
    remote_market: dict[str, Any] = {'attempted': False, 'ok': False, 'reason': None}

    if execution_live and shared_session.get('ok') and shared_client is not None:
        if int(sample_candles) > 0:
            remote_candles['attempted'] = True
            started = time.perf_counter()
            try:
                candles = shared_client.get_candles(str(ctx.config.asset), int(ctx.config.interval_sec), int(sample_candles), int(now_ts))
                remote_candles['latency_ms'] = round((time.perf_counter() - started) * 1000.0, 3)
                remote_candles['ok'] = bool(candles)
                remote_candles['count'] = len(list(candles or []))
                if candles:
                    first_row = list(candles)[0]
                    last_row = list(candles)[-1]
                    remote_candles['first_ts'] = first_row.get('from') or first_row.get('time')
                    remote_candles['last_ts'] = last_row.get('from') or last_row.get('time')
                    remote_candles['reason'] = None
                    scope_checks.append(check('remote_candles', 'ok', 'Provider retornou amostra de candles', count=remote_candles['count'], latency_ms=remote_candles.get('latency_ms')))
                else:
                    remote_candles['reason'] = 'empty_candles'
                    scope_checks.append(check('remote_candles', 'error', 'Provider retornou lista vazia de candles', latency_ms=remote_candles.get('latency_ms')))
            except Exception as exc:
                remote_candles['latency_ms'] = round((time.perf_counter() - started) * 1000.0, 3)
                remote_candles['reason'] = f'{type(exc).__name__}: {exc}'
                scope_checks.append(check('remote_candles', 'error', 'Falha ao obter candles do provider', reason=remote_candles['reason'], latency_ms=remote_candles.get('latency_ms')))
        if bool(probe_market_context):
            remote_market['attempted'] = True
            started = time.perf_counter()
            try:
                payload = shared_client.get_market_context(str(ctx.config.asset), int(ctx.config.interval_sec), payout_fallback=0.8)
                remote_market['latency_ms'] = round((time.perf_counter() - started) * 1000.0, 3)
                remote_market['ok'] = True
                remote_market['payload'] = {
                    'asset_requested': payload.get('asset_requested'),
                    'asset_resolved': payload.get('asset_resolved'),
                    'market_open': payload.get('market_open'),
                    'open_source': payload.get('open_source'),
                    'payout': payload.get('payout'),
                    'payout_source': payload.get('payout_source'),
                }
                scope_checks.append(check('remote_market_context', 'ok', 'Provider retornou market context', latency_ms=remote_market.get('latency_ms'), open_source=payload.get('open_source'), payout_source=payload.get('payout_source')))
            except Exception as exc:
                remote_market['latency_ms'] = round((time.perf_counter() - started) * 1000.0, 3)
                remote_market['reason'] = f'{type(exc).__name__}: {exc}'
                scope_checks.append(check('remote_market_context', 'error', 'Falha ao obter market context do provider', reason=remote_market['reason'], latency_ms=remote_market.get('latency_ms')))
    elif execution_live and shared_session.get('attempted'):
        scope_checks.append(check('remote_provider_session', 'error', 'Sessão do provider indisponível para probes remotos', reason=shared_session.get('reason')))
    elif execution_live:
        scope_checks.append(check('remote_provider_session', 'warn', 'Probes remotos do provider desabilitados nesta execução'))
    else:
        scope_checks.append(check('remote_provider_session', 'ok', 'Provider remoto não requerido para este profile', execution_mode=exec_cfg.get('mode'), provider=exec_cfg.get('provider')))

    remote_candles_ok = bool(remote_candles.get('attempted')) and bool(remote_candles.get('ok'))
    remote_market_ok = bool(remote_market.get('attempted')) and bool(remote_market.get('ok'))
    if remote_candles_ok:
        for item in scope_checks:
            if str(item.get('name')) == 'candle_db_local' and str(item.get('status')) == 'warn':
                item['status'] = 'ok'
                item['message'] = 'DB local stale, porém o provider remoto está saudável; execute collect_recent para convergência local'
                item['advisory_only'] = True
                item['local_cache_stale'] = True
                break
    if remote_market_ok:
        for item in scope_checks:
            if str(item.get('name')) == 'market_context_local' and str(item.get('status')) == 'warn':
                item['status'] = 'ok'
                item['message'] = 'market_context local desatualizado, porém o provider remoto está saudável; execute refresh_market_context para convergência local'
                item['advisory_only'] = True
                item['local_cache_stale'] = True
                break

    effective_scope_checks = [item for item in scope_checks if not bool(item.get('advisory_only'))]
    severity = severity_from_checks(effective_scope_checks or scope_checks)
    actions: list[str] = []
    if any(str(item.get('name')) == 'mode_alignment' and str(item.get('status')) == 'error' for item in scope_checks):
        actions.append('Alinhe execution.account_mode e broker.balance_mode no profile ativo; não deixe o secret bundle sobrescrever balance_mode.')
    if any(str(item.get('name')) == 'remote_candles' and str(item.get('status')) == 'error' for item in scope_checks):
        actions.append('Investigue o provider login/asset resolution e repita runtime_app provider-probe após corrigir o caminho broker-facing.')
    if any(str(item.get('name')) == 'market_context_local' and str(item.get('status')) == 'warn' for item in scope_checks):
        actions.append('Rode natbin.refresh_market_context ou runtime_app observe --once para regenerar o market_context do scope.')
    if any(str(item.get('name')) == 'candle_db_local' and str(item.get('status')) == 'warn' for item in scope_checks):
        actions.append('Rode natbin.collect_recent para renovar a base local de candles do scope.')

    return {
        'scope': {
            'asset': str(ctx.config.asset),
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': str(ctx.scope.scope_tag),
        },
        'execution': {
            'enabled': bool(exec_cfg.get('enabled')),
            'mode': str(exec_cfg.get('mode') or 'disabled'),
            'provider': str(exec_cfg.get('provider') or 'fake'),
            'account_mode': execution_account_mode,
        },
        'broker': {
            'provider': str(broker.get('provider') or exec_cfg.get('provider') or 'unknown'),
            'balance_mode': broker_balance_mode,
        },
        'data_paths': {
            'db_path': str(data_paths.db_path),
            'dataset_path': str(data_paths.dataset_path),
        },
        'local_market_context': local_market_context,
        'local_candle_db': db_snapshot,
        'shared_provider_session': shared_session,
        'remote_candles': remote_candles,
        'remote_market_context': remote_market,
        'checks': scope_checks,
        'severity': severity,
        'ok': severity != 'error',
        'actions': dedupe_actions(actions),
        'source_trace': list(ctx.source_trace),
    }


def build_provider_probe_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = False,
    active: bool = True,
    sample_candles: int = 3,
    probe_market_context: bool = True,
    market_context_max_age_sec: int | None = None,
    write_artifact: bool = True,
) -> dict[str, Any]:
    repo, cfg_path, cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    if not scopes:
        payload = {
            'at_utc': now_utc().isoformat(timespec='seconds'),
            'kind': 'provider_probe',
            'ok': False,
            'severity': 'error',
            'repo_root': str(repo),
            'config_path': str(cfg_path),
            'message': 'no_scopes_selected',
            'scope_results': [],
        }
        return payload

    primary_scope = scopes[0]
    primary_ctx = build_context(repo_root=repo, config_path=cfg_path, asset=str(primary_scope.asset), interval_sec=int(primary_scope.interval_sec), dump_snapshot=False)
    security = audit_security_posture(
        repo_root=repo,
        config_path=cfg_path,
        resolved_config=primary_ctx.resolved_config,
        source_trace=list(primary_ctx.source_trace),
    )
    checks: list[dict[str, Any]] = []
    security_sev = str(security.get('severity') or 'ok')
    if bool(security.get('blocked')):
        checks.append(check('security_posture', 'error', 'Security posture bloqueia o provider probe', severity=security_sev, credential_source=security.get('credential_source')))
    elif security_sev == 'warn':
        checks.append(check('security_posture', 'warn', 'Security posture com avisos', severity=security_sev, credential_source=security.get('credential_source')))
    else:
        checks.append(check('security_posture', 'ok', 'Security posture pronta para o provider probe', severity=security_sev, credential_source=security.get('credential_source')))

    exec_cfg = dict(execution_cfg(primary_ctx) or {})
    broker = dict(broker_cfg(primary_ctx) or {})
    execution_live = bool(exec_cfg.get('enabled')) and execution_mode_uses_broker_submit(exec_cfg.get('mode')) and str(exec_cfg.get('provider') or 'fake') == 'iqoption'
    transport_hint = _transport_hint(primary_ctx, repo)
    if transport_hint is None:
        checks.append(check('transport_hint', 'ok', 'Nenhuma configuração explícita de transporte/proxy detectada'))
    elif transport_hint.get('uses_socks') and not transport_hint.get('pysocks_available'):
        checks.append(check('transport_hint', 'error', 'Proxy SOCKS configurado sem PySocks instalado', scheme=transport_hint.get('scheme'), source=transport_hint.get('source'), reason=transport_hint.get('pysocks_reason')))
    else:
        checks.append(check('transport_hint', 'ok', 'Transporte/proxy configurado e dependência compatível detectada', scheme=transport_hint.get('scheme'), source=transport_hint.get('source'), uses_socks=transport_hint.get('uses_socks')))

    shared_session: dict[str, Any] = {
        'attempted': False,
        'ok': False,
        'reason': None,
        'checked_at_utc': now_utc().isoformat(timespec='seconds'),
    }
    shared_client = None

    if execution_live:
        adapter = adapter_from_context(primary_ctx, repo_root=repo)
        dep = adapter._dependency_status() if hasattr(adapter, '_dependency_status') else {'available': True, 'reason': None}
        email, password = adapter._credentials() if hasattr(adapter, '_credentials') else (None, None)
        if not bool(dep.get('available', True)):
            checks.append(check('provider_dependency', 'error', 'Dependência do broker ausente', reason=dep.get('reason')))
            shared_session['reason'] = str(dep.get('reason') or 'iqoption_dependency_missing')
        else:
            checks.append(check('provider_dependency', 'ok', 'Dependência do broker disponível'))
        if email and password:
            checks.append(check('provider_credentials', 'ok', 'Credenciais do broker presentes', credential_source=security.get('credential_source')))
        else:
            checks.append(check('provider_credentials', 'error', 'Credenciais do broker ausentes ou incompletas', credential_source=security.get('credential_source')))
            if shared_session.get('reason') in (None, ''):
                shared_session['reason'] = 'iqoption_missing_credentials'
        if active and bool(dep.get('available', True)) and email and password:
            shared_session['attempted'] = True
            started = time.perf_counter()
            try:
                maker = getattr(adapter, '_make_client', None)
                if maker is None:
                    raise RuntimeError('adapter_missing_make_client')
                shared_client = maker()
                connect_kwargs = getattr(adapter, '_connect_kwargs', lambda: {})()
                probe_retry_cap = max(1, int(os.getenv('THALOR_PROVIDER_PROBE_CONNECT_RETRIES') or 3))
                probe_sleep_cap = max(0.0, float(os.getenv('THALOR_PROVIDER_PROBE_CONNECT_SLEEP_S') or 1.0))
                connect_kwargs = dict(connect_kwargs) if isinstance(connect_kwargs, Mapping) else {}
                connect_kwargs['retries'] = min(probe_retry_cap, max(1, int(connect_kwargs.get('retries') or probe_retry_cap)))
                connect_kwargs['sleep_s'] = min(probe_sleep_cap, float(connect_kwargs.get('sleep_s') or probe_sleep_cap))
                if os.getenv('THALOR_PROVIDER_PROBE_CONNECT_TIMEOUT_S') not in (None, ''):
                    try:
                        connect_kwargs['connect_timeout_s'] = float(os.getenv('THALOR_PROVIDER_PROBE_CONNECT_TIMEOUT_S') or 0.0)
                    except Exception:
                        pass
                temp_env = getattr(adapter, '_temporary_client_env', None)
                if callable(temp_env):
                    with temp_env():
                        shared_client.connect(**connect_kwargs)
                        shared_client.ensure_connection()
                else:
                    shared_client.connect(**connect_kwargs)
                    shared_client.ensure_connection()
                shared_session.update(
                    {
                        'ok': True,
                        'checked_at_utc': now_utc().isoformat(timespec='seconds'),
                        'latency_ms': round((time.perf_counter() - started) * 1000.0, 3),
                        'reason': None,
                        'balance_mode': str(broker.get('balance_mode') or exec_cfg.get('account_mode') or 'PRACTICE').upper(),
                    }
                )
                try:
                    primer = getattr(shared_client, 'prime_provider_metadata', None)
                    if callable(primer):
                        shared_session['metadata_prime'] = primer()
                except Exception as exc:  # pragma: no cover - defensive
                    shared_session['metadata_prime'] = {'attempted': True, 'ok': False, 'reason': f'{type(exc).__name__}: {exc}'}
                checks.append(check('provider_session', 'ok', 'Login no provider concluído com sucesso', latency_ms=shared_session.get('latency_ms')))
            except Exception as exc:
                shared_session.update(
                    {
                        'ok': False,
                        'checked_at_utc': now_utc().isoformat(timespec='seconds'),
                        'latency_ms': round((time.perf_counter() - started) * 1000.0, 3),
                        'reason': f'{type(exc).__name__}: {exc}',
                    }
                )
                checks.append(check('provider_session', 'error', 'Falha ao abrir sessão no provider', reason=shared_session.get('reason'), latency_ms=shared_session.get('latency_ms')))
        elif execution_live:
            checks.append(check('provider_session', 'warn', 'Provider probe em modo passivo; sessão remota não foi aberta'))
    else:
        checks.append(check('provider_dependency', 'ok', 'Provider remoto não é obrigatório para o profile atual', execution_mode=exec_cfg.get('mode'), provider=exec_cfg.get('provider')))
        checks.append(check('provider_credentials', 'ok', 'Credenciais do broker não são exigidas para este profile', credential_source=security.get('credential_source')))
        checks.append(check('provider_session', 'ok', 'Sessão remota não requerida para este profile', execution_mode=exec_cfg.get('mode'), provider=exec_cfg.get('provider')))

    scope_results: list[dict[str, Any]] = []
    mc_max_age = int(market_context_max_age_sec or max(int(getattr(primary_scope, 'interval_sec', 300)) * 3, 900))
    for scope in scopes:
        ctx = build_context(repo_root=repo, config_path=cfg_path, asset=str(scope.asset), interval_sec=int(scope.interval_sec), dump_snapshot=False)
        result = _scope_probe_from_context(
            ctx=ctx,
            cfg=cfg,
            repo=repo,
            shared_client=shared_client,
            shared_session=dict(shared_session),
            sample_candles=sample_candles,
            probe_market_context=probe_market_context,
            market_context_max_age_sec=mc_max_age,
        )
        scope_results.append(result)
        if write_artifact:
            write_control_artifact(repo_root=repo, asset=str(scope.asset), interval_sec=int(scope.interval_sec), name='provider_probe', payload=result)

    scope_errors = sum(1 for item in scope_results if str(item.get('severity')) == 'error')
    scope_warns = sum(1 for item in scope_results if str(item.get('severity')) == 'warn')
    provider_ready_scopes = sum(1 for item in scope_results if bool(((item.get('shared_provider_session') or {}).get('ok'))) and bool((item.get('remote_candles') or {}).get('ok', True) if (item.get('remote_candles') or {}).get('attempted') else True))
    stale_market_context_scopes = sum(1 for item in scope_results if not bool(((item.get('local_market_context') or {}).get('fresh'))))

    severity = severity_from_checks(checks + [check(f"scope:{(item.get('scope') or {}).get('scope_tag')}", item.get('severity') or 'ok', 'scope_summary') for item in scope_results])
    top_warn_names = {str(item.get('name')) for item in checks if str(item.get('status')) == 'warn'}
    scope_warn_names = {
        str(check_item.get('name'))
        for result in scope_results
        for check_item in list(result.get('checks') or [])
        if str(check_item.get('status')) == 'warn'
    }
    if severity == 'warn' and not scope_errors:
        informational_top_warns = {'provider_session'}
        informational_scope_warns = {'remote_provider_session'}
        if (top_warn_names or scope_warn_names) and top_warn_names <= informational_top_warns and scope_warn_names <= informational_scope_warns:
            severity = 'ok'
    actions = dedupe_actions(
        [
            *[action for item in scope_results for action in list(item.get('actions') or [])],
            'Use runtime_app production-gate --probe-provider para consolidar provider, doctor e readiness em um único parecer operacional.' if severity != 'error' else 'Corrija os blockers do provider-probe antes de avançar para o production-gate.',
        ]
    )

    payload = {
        'at_utc': now_utc().isoformat(timespec='seconds'),
        'kind': 'provider_probe',
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'all_scopes': bool(all_scopes),
        'execution': {
            'enabled': bool(exec_cfg.get('enabled')),
            'mode': str(exec_cfg.get('mode') or 'disabled'),
            'provider': str(exec_cfg.get('provider') or 'fake'),
            'account_mode': str(exec_cfg.get('account_mode') or 'PRACTICE').upper(),
        },
        'broker': {
            'provider': str(broker.get('provider') or exec_cfg.get('provider') or 'unknown'),
            'balance_mode': str(broker.get('balance_mode') or exec_cfg.get('account_mode') or 'PRACTICE').upper(),
        },
        'credential_source': security.get('credential_source'),
        'source_trace': list(primary_ctx.source_trace),
        'transport_hint': transport_hint,
        'checks': checks,
        'shared_provider_session': shared_session,
        'scope_results': scope_results,
        'summary': {
            'scope_count': len(scope_results),
            'scope_errors': scope_errors,
            'scope_warnings': scope_warns,
            'provider_ready_scopes': provider_ready_scopes,
            'stale_market_context_scopes': stale_market_context_scopes,
            'multi_asset_enabled': bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
            'max_parallel_assets': int(getattr(getattr(cfg, 'multi_asset', None), 'max_parallel_assets', 1) or 1),
        },
        'actions': actions,
    }

    if write_artifact:
        if all_scopes or len(scope_results) > 1:
            write_repo_control_artifact(repo_root=repo, name='provider_probe', payload=payload)
        elif scope_results:
            scope = scope_results[0].get('scope') or {}
            write_control_artifact(repo_root=repo, asset=str(scope.get('asset') or primary_scope.asset), interval_sec=int(scope.get('interval_sec') or primary_scope.interval_sec), name='provider_probe', payload=payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Probe the broker-facing/provider path with passive + active diagnostics')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--asset', default=None)
    ap.add_argument('--interval-sec', type=int, default=None)
    ap.add_argument('--all-scopes', action='store_true')
    ap.add_argument('--passive', action='store_true', help='Skip live login/candles probes and only inspect local/provider prerequisites')
    ap.add_argument('--sample-candles', type=int, default=3)
    ap.add_argument('--skip-market-context-remote', action='store_true')
    ap.add_argument('--market-context-max-age-sec', type=int, default=None)
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_provider_probe_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        all_scopes=bool(ns.all_scopes),
        active=not bool(ns.passive),
        sample_candles=int(ns.sample_candles or 0),
        probe_market_context=not bool(ns.skip_market_context_remote),
        market_context_max_age_sec=ns.market_context_max_age_sec,
        write_artifact=True,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
