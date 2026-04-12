from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from ..config.loader import load_resolved_config
from ..config.paths import resolve_config_path, resolve_env_path, resolve_repo_root
from ..config.sources import modern_dotenv_env_map, resolve_yaml_config_source
from ..ops.diagnostic_utils import check, dedupe_actions, load_selected_scopes, resolve_path, resolve_scope_paths, severity_from_checks
from ..security.redaction import REDACTED, REDACTED_EMAIL, collect_sensitive_values, sanitize_payload
from ..security.secrets import resolve_secret_bundle_path
from ..state.control_repo import write_control_artifact, write_repo_control_artifact
from ..runtime.scope import market_context_path

_URL_CRED_RE = re.compile(r'((?:socks5h?|socks4|socks|https?)://)([^:/\s@]+):([^@\s]+)@', re.IGNORECASE)
_GENERIC_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_TRANSPORT_ENV_KEYS = ('TRANSPORT_ENDPOINT', 'TRANSPORT_ENDPOINTS', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')
_TRANSPORT_FILE_NAMES = ('transport_endpoint', 'transport_endpoints')


@dataclass(frozen=True)
class FieldSpec:
    name: str
    path: tuple[str, ...]
    yaml_path: tuple[str, ...] | None = None
    modern_env: tuple[str, ...] = ()
    compat_env: tuple[str, ...] = ()
    bundle_paths: tuple[tuple[str, ...], ...] = ()
    normalizer: Callable[[Any], Any] = lambda value: value
    preview: Callable[[Any], Any] | None = None
    secret: bool = False
    allow_bundle_override: bool = True


def _normalize_str(value: Any) -> str | None:
    if value in (None, ''):
        return None
    return str(value)


def _normalize_upper(value: Any) -> str | None:
    if value in (None, ''):
        return None
    return str(value).strip().upper()


def _normalize_bool(value: Any) -> bool | None:
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    if text in {'0', 'false', 'no', 'n', 'off'}:
        return False
    return bool(value)


def _normalize_int(value: Any) -> int | None:
    if value in (None, ''):
        return None
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _preview_value(value: Any, *, secret: bool = False) -> Any:
    if secret:
        if value in (None, ''):
            return None
        text = str(value).strip()
        if _GENERIC_EMAIL_RE.match(text):
            return REDACTED_EMAIL
        return REDACTED
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return _sanitize_text_blob(text, sensitive_values=()) if '://' in text or '@' in text else text


def _get_in(node: Any, path: Sequence[str]) -> Any:
    cur = node
    for part in path:
        if isinstance(cur, Mapping):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            if not hasattr(cur, part):
                return None
            cur = getattr(cur, part)
    return cur


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding='utf-8').strip()
    except Exception:
        return None
    return text or None


def _read_bundle(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return {}
    if path.suffix.lower() == '.json':
        try:
            raw = json.loads(text)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}
    if path.suffix.lower() in {'.yaml', '.yml'} and yaml is not None:
        try:
            raw = yaml.safe_load(text) or {}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}
    return {}


def _bundle_get(bundle: Mapping[str, Any], *paths: Sequence[str]) -> Any | None:
    for path in paths:
        cur: Any = bundle
        ok = True
        for part in path:
            if isinstance(cur, Mapping) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            return cur
    return None


def _secret_bundle_path(repo_root: Path, resolved_cfg: Mapping[str, Any], env_path: Path | None) -> Path | None:
    security = dict(resolved_cfg.get('security') or {})
    return resolve_secret_bundle_path(repo_root=repo_root, security=security)


def _safe_env_lines(env_path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if env_path is None or not env_path.exists():
        return out
    try:
        lines = env_path.read_text(encoding='utf-8').splitlines()
    except Exception:
        return out
    for raw in lines:
        line = str(raw).strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        out[str(key).strip()] = str(value).strip().strip('"').strip("'")
    return out


def _collect_transport_sources(repo_root: Path, bundle: Mapping[str, Any], env_lines: Mapping[str, str]) -> dict[str, Any]:
    bundle_endpoint = _bundle_get(bundle, ('transport', 'endpoint'), ('transport_endpoint',), ('network', 'transport', 'endpoint'))
    bundle_endpoints = _bundle_get(bundle, ('transport', 'endpoints'), ('transport_endpoints',), ('network', 'transport', 'endpoints'))

    process_candidates = [(name, os.getenv(name)) for name in _TRANSPORT_ENV_KEYS]
    dotenv_candidates = [(name, env_lines.get(name)) for name in _TRANSPORT_ENV_KEYS]
    file_candidates = [(name, repo_root / 'secrets' / name) for name in _TRANSPORT_FILE_NAMES]

    selected_source = 'none'
    selected_value: Any = None
    candidates: list[dict[str, Any]] = []

    def _append_candidate(source: str, raw: Any) -> None:
        if raw in (None, '', []):
            return
        text = raw
        if isinstance(raw, list):
            text = [str(item) for item in raw if str(item or '').strip()]
            if not text:
                return
        candidates.append({
            'source': source,
            'value_preview': _preview_value(text if not isinstance(text, list) else ','.join(text), secret=False),
        })

    if bundle_endpoint not in (None, ''):
        selected_source = 'secret_bundle:endpoint'
        selected_value = str(bundle_endpoint).strip()
    elif bundle_endpoints not in (None, '', []):
        selected_source = 'secret_bundle:endpoints'
        selected_value = list(bundle_endpoints) if isinstance(bundle_endpoints, list) else str(bundle_endpoints)
    else:
        for name, value in process_candidates:
            if str(value or '').strip():
                selected_source = f'process_env:{name}'
                selected_value = str(value).strip()
                break
        if selected_value in (None, ''):
            for name, value in dotenv_candidates:
                if str(value or '').strip():
                    selected_source = f'dotenv:{name}'
                    selected_value = str(value).strip()
                    break
        if selected_value in (None, ''):
            for name, path in file_candidates:
                text = _read_text(path)
                if text:
                    selected_source = f'secret_file:{name}'
                    selected_value = text
                    break

    _append_candidate('secret_bundle:endpoint', bundle_endpoint)
    _append_candidate('secret_bundle:endpoints', bundle_endpoints)
    for name, value in process_candidates:
        _append_candidate(f'process_env:{name}', value)
    for name, value in dotenv_candidates:
        _append_candidate(f'dotenv:{name}', value)
    for name, path in file_candidates:
        _append_candidate(f'secret_file:{name}', _read_text(path))

    selected_preview = None
    if isinstance(selected_value, list):
        selected_preview = [_preview_value(item, secret=False) for item in selected_value]
        scheme = _detect_transport_scheme(selected_value[0] if selected_value else None)
    else:
        selected_preview = _preview_value(selected_value, secret=False)
        scheme = _detect_transport_scheme(selected_value)
    uses_socks = bool(str(scheme or '').startswith('socks'))
    pysocks_available = True
    pysocks_reason = None
    if uses_socks:
        try:
            import socks  # type: ignore  # noqa: F401
        except Exception as exc:  # pragma: no cover - environment dependent
            pysocks_available = False
            pysocks_reason = f'{type(exc).__name__}: {exc}'
    return {
        'configured': selected_source != 'none',
        'selected_source': selected_source,
        'selected_value_preview': selected_preview,
        'scheme': scheme,
        'uses_socks': uses_socks,
        'pysocks_available': pysocks_available,
        'pysocks_reason': pysocks_reason,
        'candidates': candidates,
    }


def _detect_transport_scheme(raw: Any) -> str | None:
    text = str(raw or '').strip().lower()
    if not text:
        return None
    for scheme in ('socks5h', 'socks5', 'socks4', 'socks', 'https', 'http'):
        if text.startswith(f'{scheme}://'):
            return scheme
    return None


def _sanitize_text_blob(text: str, *, sensitive_values: Iterable[str], redact_email: bool = True) -> str:
    out = str(text)
    out = _URL_CRED_RE.sub(r'\1***:***@', out)
    for secret in sensitive_values:
        token = str(secret or '').strip()
        if not token:
            continue
        marker = REDACTED_EMAIL if (redact_email and _GENERIC_EMAIL_RE.match(token)) else REDACTED
        out = out.replace(token, marker)
    return out


def collect_project_sensitive_values(
    *,
    repo_root: str | Path,
    resolved_cfg: Mapping[str, Any],
    bundle: Mapping[str, Any] | None = None,
    extra_transport_values: Iterable[str] | None = None,
) -> list[str]:
    values = set(collect_sensitive_values(resolved_cfg, redact_email=True))
    broker = dict(resolved_cfg.get('broker') or {})
    email = broker.get('email')
    password = broker.get('password')
    if email not in (None, ''):
        values.add(str(email))
    if password not in (None, ''):
        try:
            values.add(str(password.get_secret_value()))
        except Exception:
            values.add(str(password))
    bundle_obj = dict(bundle or {})
    for path in [
        ('broker', 'email'),
        ('broker', 'password'),
        ('transport', 'endpoint'),
        ('transport_endpoint',),
        ('network', 'transport', 'endpoint'),
        ('network', 'transport', 'endpoints'),
    ]:
        item = _bundle_get(bundle_obj, path)
        if item in (None, ''):
            continue
        if isinstance(item, list):
            for value in item:
                if str(value or '').strip():
                    values.add(str(value).strip())
        else:
            values.add(str(item).strip())
    repo = Path(repo_root).resolve()
    for env_name in _TRANSPORT_ENV_KEYS:
        raw = os.getenv(env_name)
        if str(raw or '').strip():
            values.add(str(raw).strip())
    for file_name in _TRANSPORT_FILE_NAMES:
        raw = _read_text(repo / 'secrets' / file_name)
        if raw:
            values.add(str(raw).strip())
    for value in list(extra_transport_values or []):
        if str(value or '').strip():
            values.add(str(value).strip())
    return sorted({item for item in values if item}, key=len, reverse=True)


_FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name='runtime.profile',
        path=('profile',),
        yaml_path=('runtime', 'profile'),
        modern_env=('THALOR__RUNTIME__PROFILE',),
        normalizer=_normalize_str,
    ),
    FieldSpec(
        name='execution.enabled',
        path=('execution', 'enabled'),
        yaml_path=('execution', 'enabled'),
        modern_env=('THALOR__EXECUTION__ENABLED',),
        normalizer=_normalize_bool,
    ),
    FieldSpec(
        name='execution.mode',
        path=('execution', 'mode'),
        yaml_path=('execution', 'mode'),
        modern_env=('THALOR__EXECUTION__MODE',),
        normalizer=_normalize_upper,
    ),
    FieldSpec(
        name='execution.provider',
        path=('execution', 'provider'),
        yaml_path=('execution', 'provider'),
        modern_env=('THALOR__EXECUTION__PROVIDER',),
        normalizer=_normalize_str,
    ),
    FieldSpec(
        name='execution.account_mode',
        path=('execution', 'account_mode'),
        yaml_path=('execution', 'account_mode'),
        modern_env=('THALOR__EXECUTION__ACCOUNT_MODE',),
        normalizer=_normalize_upper,
    ),
    FieldSpec(
        name='broker.provider',
        path=('broker', 'provider'),
        yaml_path=('broker', 'provider'),
        modern_env=('THALOR__BROKER__PROVIDER',),
        normalizer=_normalize_str,
    ),
    FieldSpec(
        name='broker.balance_mode',
        path=('broker', 'balance_mode'),
        yaml_path=('broker', 'balance_mode'),
        modern_env=('THALOR__BROKER__BALANCE_MODE',),
        compat_env=('IQ_BALANCE_MODE',),
        bundle_paths=(('broker', 'balance_mode'), ('IQ_BALANCE_MODE',), ('balance_mode',)),
        normalizer=_normalize_upper,
        allow_bundle_override=False,
    ),
    FieldSpec(
        name='security.deployment_profile',
        path=('security', 'deployment_profile'),
        yaml_path=('security', 'deployment_profile'),
        modern_env=('THALOR__SECURITY__DEPLOYMENT_PROFILE',),
        normalizer=_normalize_str,
    ),
    FieldSpec(
        name='multi_asset.enabled',
        path=('multi_asset', 'enabled'),
        yaml_path=('multi_asset', 'enabled'),
        modern_env=('THALOR__MULTI_ASSET__ENABLED',),
        normalizer=_normalize_bool,
    ),
    FieldSpec(
        name='multi_asset.max_parallel_assets',
        path=('multi_asset', 'max_parallel_assets'),
        yaml_path=('multi_asset', 'max_parallel_assets'),
        modern_env=('THALOR__MULTI_ASSET__MAX_PARALLEL_ASSETS',),
        normalizer=_normalize_int,
    ),
    FieldSpec(
        name='multi_asset.portfolio_topk_total',
        path=('multi_asset', 'portfolio_topk_total'),
        yaml_path=('multi_asset', 'portfolio_topk_total'),
        modern_env=('THALOR__MULTI_ASSET__PORTFOLIO_TOPK_TOTAL',),
        normalizer=_normalize_int,
    ),
    FieldSpec(
        name='multi_asset.portfolio_hard_max_positions',
        path=('multi_asset', 'portfolio_hard_max_positions'),
        yaml_path=('multi_asset', 'portfolio_hard_max_positions'),
        modern_env=('THALOR__MULTI_ASSET__PORTFOLIO_HARD_MAX_POSITIONS',),
        normalizer=_normalize_int,
    ),
    FieldSpec(
        name='data.db_path',
        path=('data', 'db_path'),
        yaml_path=('data', 'db_path'),
        modern_env=('THALOR__DATA__DB_PATH',),
        normalizer=_normalize_str,
    ),
    FieldSpec(
        name='data.dataset_path',
        path=('data', 'dataset_path'),
        yaml_path=('data', 'dataset_path'),
        modern_env=('THALOR__DATA__DATASET_PATH',),
        normalizer=_normalize_str,
    ),
)


def _candidate_records(
    spec: FieldSpec,
    *,
    yaml_payload: Mapping[str, Any],
    dotenv_lines: Mapping[str, str],
    dotenv_modern: Mapping[str, str],
    process_env: Mapping[str, str],
    bundle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def _append(source: str, precedence: int, raw: Any) -> None:
        if raw in (None, '', []):
            return
        candidates.append(
            {
                'source': source,
                'precedence': precedence,
                'raw': raw,
                'normalized': spec.normalizer(raw),
                'value_preview': _preview_value(raw, secret=spec.secret),
            }
        )

    if spec.yaml_path is not None:
        _append('yaml', 60, _get_in(yaml_payload, spec.yaml_path))
    for name in spec.compat_env:
        _append(f'dotenv:{name}', 70, dotenv_lines.get(name))
    for name in spec.modern_env:
        _append(f'dotenv:{name}', 50, dotenv_modern.get(name))
    for name in spec.compat_env:
        _append(f'process_env:{name}', 40, process_env.get(name))
    for name in spec.modern_env:
        _append(f'process_env:{name}', 30, process_env.get(name))
    for path in spec.bundle_paths:
        _append(f'secret_bundle:{".".join(path)}', 20, _bundle_get(bundle, path))
    return sorted(candidates, key=lambda item: int(item['precedence']))


def _field_record(
    spec: FieldSpec,
    *,
    effective: Mapping[str, Any],
    yaml_payload: Mapping[str, Any],
    dotenv_lines: Mapping[str, str],
    dotenv_modern: Mapping[str, str],
    process_env: Mapping[str, str],
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    effective_raw = _get_in(effective, spec.path)
    effective_normalized = spec.normalizer(effective_raw)
    candidates = _candidate_records(
        spec,
        yaml_payload=yaml_payload,
        dotenv_lines=dotenv_lines,
        dotenv_modern=dotenv_modern,
        process_env=process_env,
        bundle=bundle,
    )
    matching_candidates = [item for item in candidates if item['normalized'] == effective_normalized]
    if bool(spec.allow_bundle_override):
        effective_winner = matching_candidates[0] if matching_candidates else None
        effective_source = effective_winner['source'] if effective_winner is not None else 'model_default_or_derived'
        reported_winner = effective_winner
        forbidden_conflicting_bundle = None
    else:
        non_bundle_matches = [item for item in matching_candidates if not str(item.get('source') or '').startswith('secret_bundle:')]
        effective_winner = (non_bundle_matches[0] if non_bundle_matches else (matching_candidates[0] if matching_candidates else None))
        effective_source = effective_winner['source'] if effective_winner is not None else 'model_default_or_derived'
        bundle_candidates = [item for item in candidates if str(item.get('source') or '').startswith('secret_bundle:')]
        forbidden_conflicting_bundle = next((item for item in bundle_candidates if item['normalized'] not in (None, effective_normalized)), None)
        reported_winner = forbidden_conflicting_bundle if forbidden_conflicting_bundle is not None else effective_winner
    shadowed = [
        {
            'source': item['source'],
            'value_preview': item['value_preview'],
        }
        for item in candidates
        if reported_winner is not None and item is not reported_winner and item['normalized'] is not None
    ]
    bundle_candidates = [item for item in candidates if str(item.get('source') or '').startswith('secret_bundle:')]
    winner_source = reported_winner['source'] if reported_winner is not None else 'model_default_or_derived'
    return {
        'field': spec.name,
        'effective_value': _preview_value(effective_raw, secret=spec.secret),
        'effective_normalized': effective_normalized,
        'winner': winner_source,
        'effective_winner': effective_source,
        'winner_value_preview': reported_winner['value_preview'] if reported_winner is not None else _preview_value(effective_raw, secret=spec.secret),
        'candidates': [
            {
                'source': item['source'],
                'value_preview': item['value_preview'],
                'matched_effective': item['normalized'] == effective_normalized,
            }
            for item in candidates
        ],
        'shadowed_candidates': shadowed,
        'bundle_override_present': bool(bundle_candidates),
        'bundle_override_effective': forbidden_conflicting_bundle is not None if not bool(spec.allow_bundle_override) else str(winner_source).startswith('secret_bundle:'),
        'ignored_bundle_candidates': [
            {
                'source': item['source'],
                'value_preview': item['value_preview'],
            }
            for item in bundle_candidates
            if not bool(spec.allow_bundle_override)
        ],
        'bundle_override_allowed': bool(spec.allow_bundle_override),
    }


def build_config_provenance_payload(
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
    resolved = load_resolved_config(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec)
    effective = resolved.as_dict()
    env_path = resolve_env_path(repo_root=repo, config_path=cfg_path)
    yaml_result = resolve_yaml_config_source(cfg_path)
    yaml_payload = dict(yaml_result.data or {})
    dotenv_lines = _safe_env_lines(env_path)
    dotenv_modern = modern_dotenv_env_map(env_path=env_path)
    process_env = {str(k): str(v) for k, v in os.environ.items() if v is not None}
    bundle_path = _secret_bundle_path(repo, effective, env_path)
    bundle = _read_bundle(bundle_path)

    field_records = [
        _field_record(
            spec,
            effective=effective,
            yaml_payload=yaml_payload,
            dotenv_lines=dotenv_lines,
            dotenv_modern=dotenv_modern,
            process_env=process_env,
            bundle=bundle,
        )
        for spec in _FIELD_SPECS
    ]

    selected_scope_payloads: list[dict[str, Any]] = []
    for scope in scopes:
        paths = resolve_scope_paths(repo_root=repo, cfg=cfg, scope=scope)
        selected_scope_payloads.append(
            {
                'asset': str(scope.asset),
                'interval_sec': int(scope.interval_sec),
                'scope_tag': str(scope.scope_tag),
                'data_paths': {
                    'db_path': str(paths['data'].db_path),
                    'dataset_path': str(paths['data'].dataset_path),
                },
                'runtime_paths': {
                    'market_context_path': str(market_context_path(asset=str(scope.asset), interval_sec=int(scope.interval_sec), out_dir=repo / 'runs')),
                    'signals_db_path': str(paths['runtime'].signals_db_path),
                    'state_db_path': str(paths['runtime'].state_db_path),
                    'control_dir': str((repo / 'runs' / 'control' / str(scope.scope_tag)).resolve()),
                },
            }
        )

    transport = _collect_transport_sources(repo, bundle, dotenv_lines)
    security = dict(effective.get('security') or {})
    execution = dict(effective.get('execution') or {})
    broker = dict(effective.get('broker') or {})
    multi_asset = dict(effective.get('multi_asset') or {})

    checks: list[dict[str, Any]] = []
    execution_mode = str(execution.get('mode') or '').upper()
    execution_account_mode = str(execution.get('account_mode') or 'PRACTICE').upper()
    broker_balance_mode = str(broker.get('balance_mode') or execution_account_mode or 'PRACTICE').upper()

    if execution_account_mode == broker_balance_mode:
        checks.append(check('mode_alignment', 'ok', 'execution.account_mode e broker.balance_mode alinhados', execution_account_mode=execution_account_mode, broker_balance_mode=broker_balance_mode))
    else:
        checks.append(check('mode_alignment', 'error', 'execution.account_mode e broker.balance_mode divergem', execution_account_mode=execution_account_mode, broker_balance_mode=broker_balance_mode))

    balance_field = next((item for item in field_records if item['field'] == 'broker.balance_mode'), None)
    real_sensitive_mode = execution_account_mode == 'REAL' or broker_balance_mode == 'REAL'
    if balance_field and bool(balance_field.get('bundle_override_effective')):
        if not bool(balance_field['bundle_override_allowed']) and real_sensitive_mode:
            status = 'error'
            message = 'Secret bundle tenta controlar broker.balance_mode em contexto REAL; trate isso como blocker.'
        elif not bool(balance_field['bundle_override_allowed']):
            status = 'ok'
            message = 'Secret bundle contém broker.balance_mode divergente, porém o loader ignora esse campo fora do contexto REAL.'
        else:
            status = 'warn'
            message = 'Secret bundle controla broker.balance_mode de forma efetiva'
        checks.append(check('secret_bundle_balance_mode_override', status, message, winner=balance_field['winner'], effective_value=balance_field['effective_value']))
    elif balance_field and balance_field['bundle_override_present'] and not bool(balance_field['bundle_override_allowed']):
        checks.append(check('secret_bundle_balance_mode_override', 'ok', 'Secret bundle contém broker.balance_mode, mas o loader ignora esse campo operacional', ignored_candidates=balance_field.get('ignored_bundle_candidates') or []))
    else:
        checks.append(check('secret_bundle_balance_mode_override', 'ok', 'Secret bundle não controla broker.balance_mode'))

    if bool(transport.get('configured')):
        checks.append(check('transport_resolution', 'ok', 'Transporte/proxy resolvido', source=transport.get('selected_source'), scheme=transport.get('scheme')))
    else:
        status = 'warn' if execution_mode in {'LIVE', 'PRACTICE'} else 'ok'
        checks.append(check('transport_resolution', status, 'Transporte/proxy não configurado explicitamente', source=transport.get('selected_source')))

    if bool(transport.get('uses_socks')) and not bool(transport.get('pysocks_available')):
        checks.append(check('transport_dependency', 'error', 'Transporte SOCKS configurado sem PySocks disponível', reason=transport.get('pysocks_reason')))
    else:
        checks.append(check('transport_dependency', 'ok', 'Dependência de transporte satisfeita', uses_socks=transport.get('uses_socks')))

    scope_count = len(scopes)
    if bool(multi_asset.get('enabled')):
        max_parallel = int(multi_asset.get('max_parallel_assets') or 1)
        hard_max = int(multi_asset.get('portfolio_hard_max_positions') or max_parallel)
        if scope_count > max_parallel or scope_count > hard_max:
            checks.append(check('multi_asset_capacity', 'error', 'Capacidade multi-asset menor que o número de scopes selecionados', scope_count=scope_count, max_parallel_assets=max_parallel, portfolio_hard_max_positions=hard_max))
        else:
            checks.append(check('multi_asset_capacity', 'ok', 'Capacidade multi-asset cobre os scopes selecionados', scope_count=scope_count, max_parallel_assets=max_parallel, portfolio_hard_max_positions=hard_max))
    else:
        checks.append(check('multi_asset_capacity', 'ok', 'Profile single-asset ou multi-asset desabilitado', scope_count=scope_count))

    secrets_file_env = str(security.get('secrets_file_env_var') or 'THALOR_SECRETS_FILE')
    if bundle_path is not None and bundle_path.exists():
        checks.append(check('secrets_bundle_presence', 'ok', 'Secret bundle resolvido', env_var=secrets_file_env, path=str(bundle_path)))
    else:
        checks.append(check('secrets_bundle_presence', 'warn', 'Secret bundle não resolvido', env_var=secrets_file_env, path=str(bundle_path) if bundle_path is not None else None))

    actions: list[str] = []
    if execution_account_mode != broker_balance_mode:
        actions.append('Alinhe execution.account_mode e broker.balance_mode no profile canônico antes de operar live.')
    if balance_field and real_sensitive_mode and (
        bool(balance_field.get('bundle_override_effective'))
        or any(
            str(item.get('source') or '').startswith('secret_bundle:')
            and item.get('matched_effective') is False
            for item in list(balance_field.get('candidates') or [])
        )
    ):
        actions.append('Remova broker.balance_mode do secret bundle; esse campo deve permanecer no YAML canônico do profile.')
    if bool(transport.get('uses_socks')) and not bool(transport.get('pysocks_available')):
        actions.append('Instale PySocks no ambiente e na imagem Docker antes de usar transporte SOCKS.')
    if bool(multi_asset.get('enabled')) and scope_count >= 6:
        actions.append('Antes de operar os 6 assets, confirme provider-probe e production-gate com --all-scopes.')
    actions = dedupe_actions(actions)

    severity = severity_from_checks(checks)
    payload = {
        'at_utc': datetime.now(UTC).isoformat(timespec='seconds'),
        'kind': 'config_provenance_audit',
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'env_path': str(env_path) if env_path is not None else None,
        'all_scopes': bool(all_scopes),
        'scope_count': len(selected_scope_payloads),
        'source_trace': list(getattr(resolved, 'source_trace', []) or []),
        'sources': {
            'yaml_paths': [str(path) for path in list(yaml_result.source_paths or ())],
            'dotenv_exists': bool(env_path is not None and env_path.exists()),
            'process_modern_env_keys': sorted([name for name in process_env.keys() if name.startswith('THALOR__')]),
            'process_compat_env_keys': sorted([name for name in process_env.keys() if name in {'IQ_EMAIL', 'IQ_PASSWORD', 'IQ_BALANCE_MODE', 'ASSET', 'INTERVAL_SEC', 'TIMEZONE'}]),
            'dotenv_modern_env_keys': sorted(list(dotenv_modern.keys())),
            'dotenv_compat_env_keys': sorted([name for name in dotenv_lines.keys() if name in {'IQ_EMAIL', 'IQ_PASSWORD', 'IQ_BALANCE_MODE', 'ASSET', 'INTERVAL_SEC', 'TIMEZONE'}]),
            'secret_bundle_path': str(bundle_path) if bundle_path is not None else None,
            'secret_bundle_exists': bool(bundle_path is not None and bundle_path.exists()),
        },
        'resolved_summary': {
            'profile': effective.get('profile'),
            'execution': {
                'enabled': bool(execution.get('enabled')),
                'mode': execution.get('mode'),
                'provider': execution.get('provider'),
                'account_mode': execution_account_mode,
            },
            'broker': {
                'provider': broker.get('provider'),
                'balance_mode': broker_balance_mode,
                'email_present': bool(broker.get('email')),
                'password_present': bool(broker.get('password')),
            },
            'security': {
                'deployment_profile': security.get('deployment_profile'),
                'secrets_file_env_var': secrets_file_env,
                'live_require_external_credentials': bool(security.get('live_require_external_credentials', False)),
            },
            'multi_asset': {
                'enabled': bool(multi_asset.get('enabled')),
                'max_parallel_assets': multi_asset.get('max_parallel_assets'),
                'portfolio_topk_total': multi_asset.get('portfolio_topk_total'),
                'portfolio_hard_max_positions': multi_asset.get('portfolio_hard_max_positions'),
            },
            'assets_count': len(list(effective.get('assets') or [])),
        },
        'field_provenance': field_records,
        'transport': transport,
        'selected_scopes': selected_scope_payloads,
        'checks': checks,
        'actions': actions,
    }

    sensitive_values = collect_project_sensitive_values(
        repo_root=repo,
        resolved_cfg=effective,
        bundle=bundle,
        extra_transport_values=[str(transport.get('selected_value_preview') or '')],
    )
    sanitized = sanitize_payload(payload, sensitive_values=sensitive_values, redact_email=True)
    if write_artifact:
        write_repo_control_artifact(repo_root=repo, name='config_provenance', payload=sanitized)
        for scope in scopes:
            scoped_payload = dict(sanitized)
            scoped_payload['scope'] = {
                'asset': str(scope.asset),
                'interval_sec': int(scope.interval_sec),
                'scope_tag': str(scope.scope_tag),
            }
            write_control_artifact(repo_root=repo, asset=str(scope.asset), interval_sec=int(scope.interval_sec), name='config_provenance', payload=scoped_payload)
    return sanitized


__all__ = [
    'build_config_provenance_payload',
    'collect_project_sensitive_values',
    '_sanitize_text_blob',
]
