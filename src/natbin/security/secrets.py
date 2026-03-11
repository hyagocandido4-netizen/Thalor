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

    Precedence is intentional:
    direct file paths > secrets bundle > existing loaded config values.
    """

    security = cfg.security
    updates: dict[str, Any] = {}
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
    bundle_path = _resolve_path(
        repo_root,
        os.getenv(str(security.secrets_file_env_var or 'THALOR_SECRETS_FILE')) or security.secrets_file,
    )

    if bundle_path is not None and bundle_path.exists():
        bundle = _read_bundle(bundle_path)
        email = _bundle_get(bundle, ('broker', 'email'), ('IQ_EMAIL',), ('email',))
        password = _bundle_get(bundle, ('broker', 'password'), ('IQ_PASSWORD',), ('password',))
        balance_mode = _bundle_get(bundle, ('broker', 'balance_mode'), ('IQ_BALANCE_MODE',), ('balance_mode',))
        if email not in (None, ''):
            updates['email'] = str(email).strip()
        if password not in (None, ''):
            updates['password'] = SecretStr(str(password).strip())
        if balance_mode not in (None, ''):
            updates['balance_mode'] = str(balance_mode).strip().upper()
        trace.append(f'secret_file:bundle:{bundle_path.name}')

    if email_file_path is not None and email_file_path.exists():
        email = _read_text_secret(email_file_path)
        if email:
            updates['email'] = str(email)
            trace.append(f'secret_file:broker.email:{email_file_path.name}')
    if password_file_path is not None and password_file_path.exists():
        password = _read_text_secret(password_file_path)
        if password:
            updates['password'] = SecretStr(str(password))
            trace.append(f'secret_file:broker.password:{password_file_path.name}')

    if updates:
        cfg = cfg.model_copy(update={'broker': cfg.broker.model_copy(update=updates)})
    return cfg, trace
