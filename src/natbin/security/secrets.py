from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from pydantic import SecretStr

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from ..config.models import ThalorConfig


EMAIL_FILE_FALLBACK_ENV = 'THALOR__BROKER__EMAIL_FILE'
PASSWORD_FILE_FALLBACK_ENV = 'THALOR__BROKER__PASSWORD_FILE'

DEFAULT_SECRET_BUNDLE_CANDIDATES = (
    Path('config/broker_secrets.yaml'),
    Path('config/broker_secrets.yml'),
    Path('secrets/broker.yaml'),
    Path('secrets/broker.yml'),
)


def _resolve_path(repo_root: str | Path, raw: str | Path | None) -> Path | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path.resolve()
    return (Path(repo_root).resolve() / path).resolve()



def _read_text_secret(path: Path) -> str | None:
    try:
        data = path.read_text(encoding='utf-8').strip()
    except Exception:
        return None
    return data or None


def _read_env_map(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if path is None or not path.exists():
        return out
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except Exception:
        return out
    for raw_line in lines:
        line = str(raw_line).strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        out[str(key).strip()] = str(value).strip().strip('"').strip("'")
    return out


def resolve_secret_bundle_path(*, repo_root: str | Path = '.', security: Any | None = None) -> Path | None:
    root = Path(repo_root).resolve()
    env_name = str(getattr(security, 'secrets_file_env_var', None) or (security.get('secrets_file_env_var') if hasattr(security, 'get') else None) or 'THALOR_SECRETS_FILE')
    security_secrets_file = getattr(security, 'secrets_file', None) if security is not None else None
    if security_secrets_file in (None, '') and hasattr(security, 'get'):
        security_secrets_file = security.get('secrets_file')

    process_raw = os.getenv(env_name)
    if process_raw not in (None, ''):
        resolved = _resolve_path(root, process_raw)
        if resolved is not None and resolved.exists():
            return resolved

    if security_secrets_file not in (None, ''):
        resolved = _resolve_path(root, security_secrets_file)
        if resolved is not None and resolved.exists():
            return resolved

    env_map = _read_env_map(root / '.env')
    dotenv_raw = env_map.get(env_name)
    if dotenv_raw not in (None, ''):
        resolved = _resolve_path(root, dotenv_raw)
        if resolved is not None and resolved.exists():
            return resolved

    for candidate_rel in DEFAULT_SECRET_BUNDLE_CANDIDATES:
        candidate = (root / candidate_rel).resolve()
        if candidate.exists():
            return candidate
    return None


def _validated_update(model: Any, updates: dict[str, Any]) -> Any:
    payload: dict[str, Any]
    if hasattr(model, 'model_dump'):
        payload = model.model_dump(mode='python')
    else:
        payload = dict(model)
    payload.update(dict(updates))
    model_type = type(model)
    if hasattr(model_type, 'model_validate'):
        return model_type.model_validate(payload)
    return model_type(**payload)


def _validated_cfg_update(cfg: ThalorConfig, *, broker_updates: dict[str, Any] | None = None, transport_updates: dict[str, Any] | None = None, request_metrics_updates: dict[str, Any] | None = None) -> ThalorConfig:
    payload = cfg.model_dump(mode='python')
    if broker_updates:
        broker_payload = dict(payload.get('broker') or {})
        broker_payload.update(dict(broker_updates))
        payload['broker'] = broker_payload
    if transport_updates:
        network_payload = dict(payload.get('network') or {})
        transport_payload = dict(network_payload.get('transport') or {})
        transport_payload.update(dict(transport_updates))
        network_payload['transport'] = transport_payload
        payload['network'] = network_payload
    if request_metrics_updates:
        observability_payload = dict(payload.get('observability') or {})
        request_payload = dict(observability_payload.get('request_metrics') or {})
        request_payload.update(dict(request_metrics_updates))
        observability_payload['request_metrics'] = request_payload
        payload['observability'] = observability_payload
    return ThalorConfig.model_validate(payload)


def _repo_relative(repo_root: str | Path, path: Path) -> str:
    root = Path(repo_root).resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except Exception:
        return path.as_posix()


def _transport_secret_file(repo_root: str | Path, name: str) -> Path:
    return (Path(repo_root).resolve() / 'secrets' / name).resolve()


def _transport_configured(cfg: ThalorConfig) -> bool:
    transport = getattr(getattr(cfg, 'network', None), 'transport', None)
    if transport is None:
        return False
    return any(
        getattr(transport, field, None) not in (None, '', [], ())
        for field in ('endpoint', 'endpoints', 'endpoint_file', 'endpoints_file')
    )


def _maybe_apply_transport_secret_file(cfg: ThalorConfig, *, repo_root: str | Path, trace: list[str]) -> ThalorConfig:
    if _transport_configured(cfg):
        return cfg
    for name, field_name in (('transport_endpoint', 'endpoint_file'), ('transport_endpoints', 'endpoints_file')):
        candidate = _transport_secret_file(repo_root, name)
        if not candidate.exists():
            continue
        updates = {
            'enabled': True,
            field_name: Path('secrets') / name,
        }
        network_cfg = _validated_update(cfg.network, {'transport': _validated_update(cfg.network.transport, updates)})
        trace.append(f'secret_file:{name}:{_repo_relative(repo_root, candidate)}')
        return _validated_cfg_update(cfg, transport_updates=network_cfg.transport.model_dump(mode='python'))
    return cfg



def _parse_env_like(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        out[str(key).strip()] = str(value).strip().strip('"').strip("'")
    return out



def _read_bundle(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return {}
    suffix = path.suffix.lower()
    if suffix == '.json':
        try:
            raw = json.loads(text)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}
    if suffix in {'.yaml', '.yml'} and yaml is not None:
        try:
            raw = yaml.safe_load(text) or {}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}
    # Generic fallback: treat it like KEY=VALUE lines.
    return _parse_env_like(text)



def _bundle_get(bundle: dict[str, Any], *paths: Iterable[str]) -> Any | None:
    candidates = list(paths)
    for path in candidates:
        node: Any = bundle
        ok = True
        for part in path:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if ok:
            return node
    return None



def apply_external_secret_overrides(cfg: ThalorConfig, *, repo_root: str | Path = '.') -> tuple[ThalorConfig, list[str]]:
    """Apply external credential sources on top of the loaded config.

    Supported sources:
    - THALOR_BROKER_EMAIL_FILE / THALOR__BROKER__EMAIL_FILE
    - THALOR_BROKER_PASSWORD_FILE / THALOR__BROKER__PASSWORD_FILE
    - THALOR_SECRETS_FILE (or security.secrets_file) with JSON/YAML/KEY=VALUE payload
    - repo-local autodiscovery of ``secrets/transport_endpoint`` and ``secrets/transport_endpoints``

    Precedence is intentional:
    direct file paths > secrets bundle > discovered transport files > existing loaded config values.
    """

    security = cfg.security
    broker_updates: dict[str, Any] = {}
    transport_updates: dict[str, Any] = {}
    request_metrics_updates: dict[str, Any] = {}
    trace: list[str] = []

    email_file_path = _resolve_path(
        repo_root,
        os.getenv(str(security.broker_email_file_env_var or 'THALOR_BROKER_EMAIL_FILE'))
        or os.getenv(EMAIL_FILE_FALLBACK_ENV),
    )
    password_file_path = _resolve_path(
        repo_root,
        os.getenv(str(security.broker_password_file_env_var or 'THALOR_BROKER_PASSWORD_FILE'))
        or os.getenv(PASSWORD_FILE_FALLBACK_ENV),
    )
    bundle_path = resolve_secret_bundle_path(repo_root=repo_root, security=security)

    if bundle_path is not None and bundle_path.exists():
        bundle = _read_bundle(bundle_path)
        email = _bundle_get(bundle, ('broker', 'email'), ('IQ_EMAIL',), ('email',))
        password = _bundle_get(bundle, ('broker', 'password'), ('IQ_PASSWORD',), ('password',))
        if email not in (None, ''):
            broker_updates['email'] = str(email).strip()
        if password not in (None, ''):
            broker_updates['password'] = SecretStr(str(password).strip())
        if broker_updates:
            trace.append(f'secret_file:bundle:{bundle_path.name}')

        bundle_endpoint = _bundle_get(bundle, ('network', 'transport', 'endpoint'), ('transport', 'endpoint'), ('transport_endpoint',))
        bundle_endpoints = _bundle_get(bundle, ('network', 'transport', 'endpoints'), ('transport', 'endpoints'), ('transport_endpoints',))
        bundle_endpoint_file = _bundle_get(bundle, ('network', 'transport', 'endpoint_file'), ('transport', 'endpoint_file'), ('transport_endpoint_file',))
        bundle_endpoints_file = _bundle_get(bundle, ('network', 'transport', 'endpoints_file'), ('transport', 'endpoints_file'), ('transport_endpoints_file',))
        bundle_no_proxy = _bundle_get(bundle, ('network', 'transport', 'no_proxy'), ('transport', 'no_proxy'))
        bundle_transport_enabled = _bundle_get(bundle, ('network', 'transport', 'enabled'), ('transport', 'enabled'))
        bundle_transport_log_path = _bundle_get(bundle, ('network', 'transport', 'structured_log_path'), ('transport', 'structured_log_path'))
        if bundle_endpoint not in (None, ''):
            transport_updates['endpoint'] = str(bundle_endpoint).strip()
        if bundle_endpoints not in (None, ''):
            transport_updates['endpoints'] = bundle_endpoints
        if bundle_endpoint_file not in (None, ''):
            transport_updates['endpoint_file'] = Path(str(bundle_endpoint_file))
        if bundle_endpoints_file not in (None, ''):
            transport_updates['endpoints_file'] = Path(str(bundle_endpoints_file))
        if bundle_no_proxy not in (None, ''):
            transport_updates['no_proxy'] = bundle_no_proxy
        if bundle_transport_log_path not in (None, ''):
            transport_updates['structured_log_path'] = Path(str(bundle_transport_log_path))
        if transport_updates:
            transport_updates['enabled'] = bool(bundle_transport_enabled if bundle_transport_enabled is not None else True)
            trace.append(f'secret_file:transport_bundle:{bundle_path.name}')

        bundle_request_metrics = _bundle_get(bundle, ('observability', 'request_metrics'), ('request_metrics',), ('network', 'request_metrics'))
        if isinstance(bundle_request_metrics, dict):
            for field_name in (
                'enabled',
                'timezone',
                'structured_log_path',
                'summary_log_level',
                'emit_summary_on_rollover',
                'emit_summary_on_close',
                'emit_request_events',
                'emit_summary_every_requests',
            ):
                if bundle_request_metrics.get(field_name) in (None, ''):
                    continue
                value = bundle_request_metrics.get(field_name)
                if field_name == 'structured_log_path':
                    value = Path(str(value))
                request_metrics_updates[field_name] = value
        if request_metrics_updates:
            trace.append(f'secret_file:request_metrics:{bundle_path.name}')

    if email_file_path is not None and email_file_path.exists():
        email = _read_text_secret(email_file_path)
        if email:
            broker_updates['email'] = str(email)
            trace.append(f'secret_file:broker.email:{email_file_path.name}')
    if password_file_path is not None and password_file_path.exists():
        password = _read_text_secret(password_file_path)
        if password:
            broker_updates['password'] = SecretStr(str(password))
            trace.append(f'secret_file:broker.password:{password_file_path.name}')

    if broker_updates or transport_updates or request_metrics_updates:
        cfg = _validated_cfg_update(
            cfg,
            broker_updates=broker_updates or None,
            transport_updates=transport_updates or None,
            request_metrics_updates=request_metrics_updates or None,
        )
    cfg = _maybe_apply_transport_secret_file(cfg, repo_root=repo_root, trace=trace)
    return cfg, trace
