from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

REDACTED = '***REDACTED***'
REDACTED_EMAIL = '***REDACTED_EMAIL***'

SENSITIVE_KEY_RE = re.compile(
    r'(pass(word)?|secret|token|api[_-]?key|auth(orization)?|session|cookie|credential)',
    re.IGNORECASE,
)
EMAIL_KEY_RE = re.compile(r'(^|[_\-.])(email|broker_email|user_email)$', re.IGNORECASE)
GENERIC_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
JSONISH_KEY_RE = re.compile(r'(_json|json_)', re.IGNORECASE)


def _secret_value(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if hasattr(value, 'get_secret_value'):
            raw = value.get_secret_value()
        else:
            raw = value
    except Exception:
        raw = value
    try:
        s = str(raw).strip()
    except Exception:
        return None
    return s or None


def _key_text(key: Any) -> str:
    try:
        return str(key or '')
    except Exception:
        return ''


def is_sensitive_key(key: Any, *, redact_email: bool = True) -> bool:
    text = _key_text(key)
    if not text:
        return False
    if SENSITIVE_KEY_RE.search(text):
        return True
    if redact_email and EMAIL_KEY_RE.search(text):
        return True
    return False



def collect_sensitive_values(obj: Any, *, redact_email: bool = True) -> list[str]:
    """Collect concrete secret values already present in *obj*.

    The collector is conservative: values are captured only when they are
    attached to obviously sensitive keys. This avoids redacting unrelated user
    content while still catching the common credential paths used by Thalor.
    """

    seen: set[str] = set()

    def _walk(item: Any, key_hint: str | None = None) -> None:
        if item is None:
            return
        if hasattr(item, 'model_dump') and not isinstance(item, (str, bytes, bytearray)):
            try:
                _walk(item.model_dump(mode='python'), key_hint=key_hint)
                return
            except Exception:
                pass
        if isinstance(item, Mapping):
            for k, v in item.items():
                ks = _key_text(k)
                if is_sensitive_key(ks, redact_email=redact_email):
                    secret = _secret_value(v)
                    if secret and len(secret) >= 3:
                        seen.add(secret)
                _walk(v, key_hint=ks)
            return
        if isinstance(item, (list, tuple, set)):
            for v in item:
                _walk(v, key_hint=key_hint)
            return
        if key_hint is not None and is_sensitive_key(key_hint, redact_email=redact_email):
            secret = _secret_value(item)
            if secret and len(secret) >= 3:
                seen.add(secret)

    _walk(obj)
    return sorted(seen, key=len, reverse=True)



def _replace_known_secrets(text: str, secrets: Iterable[str], *, redact_email: bool = True) -> str:
    out = str(text)
    for secret in secrets:
        token = _secret_value(secret)
        if not token:
            continue
        marker = REDACTED_EMAIL if (redact_email and GENERIC_EMAIL_RE.match(token)) else REDACTED
        if token in out:
            out = out.replace(token, marker)
    return out



def _try_redact_json_string(text: str, *, secrets: Iterable[str], redact_email: bool) -> str:
    s = str(text).strip()
    if not s or (not s.startswith('{') and not s.startswith('[')):
        return _replace_known_secrets(text, secrets, redact_email=redact_email)
    try:
        parsed = json.loads(s)
    except Exception:
        return _replace_known_secrets(text, secrets, redact_email=redact_email)
    clean = sanitize_payload(parsed, sensitive_values=list(secrets), redact_email=redact_email)
    try:
        return json.dumps(clean, ensure_ascii=False)
    except Exception:
        return _replace_known_secrets(text, secrets, redact_email=redact_email)



def sanitize_payload(
    obj: Any,
    *,
    sensitive_values: Iterable[str] | None = None,
    redact_email: bool = True,
    _key_hint: str | None = None,
) -> Any:
    """Deep-copy *obj* while redacting sensitive keys and concrete values."""

    secrets = list(sensitive_values or [])
    if hasattr(obj, 'model_dump') and not isinstance(obj, (str, bytes, bytearray)):
        try:
            obj = obj.model_dump(mode='python')
        except Exception:
            pass

    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            ks = _key_text(key)
            if is_sensitive_key(ks, redact_email=redact_email):
                if redact_email and EMAIL_KEY_RE.search(ks):
                    out[ks] = REDACTED_EMAIL
                else:
                    out[ks] = REDACTED
                continue
            out[ks] = sanitize_payload(value, sensitive_values=secrets, redact_email=redact_email, _key_hint=ks)
        return out

    if isinstance(obj, list):
        return [sanitize_payload(v, sensitive_values=secrets, redact_email=redact_email, _key_hint=_key_hint) for v in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_payload(v, sensitive_values=secrets, redact_email=redact_email, _key_hint=_key_hint) for v in obj)
    if isinstance(obj, set):
        return [sanitize_payload(v, sensitive_values=secrets, redact_email=redact_email, _key_hint=_key_hint) for v in obj]

    if obj is None:
        return None

    if hasattr(obj, 'get_secret_value'):
        return REDACTED

    if isinstance(obj, (bytes, bytearray)):
        try:
            return _replace_known_secrets(obj.decode('utf-8', errors='replace'), secrets, redact_email=redact_email)
        except Exception:
            return REDACTED

    if isinstance(obj, str):
        text = obj
        if _key_hint and JSONISH_KEY_RE.search(_key_hint):
            return _try_redact_json_string(text, secrets=secrets, redact_email=redact_email)
        if _key_hint and redact_email and EMAIL_KEY_RE.search(_key_hint) and GENERIC_EMAIL_RE.match(text.strip()):
            return REDACTED_EMAIL
        return _replace_known_secrets(text, secrets, redact_email=redact_email)

    return obj
