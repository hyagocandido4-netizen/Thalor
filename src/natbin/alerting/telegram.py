from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib import parse, request

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from ..security.redaction import collect_sensitive_values, sanitize_payload


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


@dataclass(frozen=True)
class TelegramCredentials:
    bot_token: str | None
    chat_id: str | None
    trace: list[str]

    @property
    def present(self) -> bool:
        return bool(self.bot_token and self.chat_id)


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


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding='utf-8').strip()
    except Exception:
        return None
    return value or None


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
    out: dict[str, Any] = {}
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        out[str(key).strip()] = str(value).strip().strip('"').strip("'")
    return out


def _bundle_get(bundle: dict[str, Any], *paths: tuple[str, ...]) -> Any | None:
    for path in paths:
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


def _cfg_section(resolved_config: Any, name: str) -> dict[str, Any]:
    if hasattr(resolved_config, 'model_dump'):
        resolved_config = resolved_config.model_dump(mode='python')
    cfg = dict(resolved_config or {})
    sec = cfg.get(name)
    if hasattr(sec, 'model_dump'):
        sec = sec.model_dump(mode='python')
    return dict(sec or {})


def _nested_cfg(resolved_config: Any, *parts: str) -> dict[str, Any]:
    if hasattr(resolved_config, 'model_dump'):
        resolved_config = resolved_config.model_dump(mode='python')
    node: Any = dict(resolved_config or {})
    for part in parts:
        if hasattr(node, 'model_dump'):
            node = node.model_dump(mode='python')
        if not isinstance(node, dict):
            return {}
        node = node.get(part)
    if hasattr(node, 'model_dump'):
        node = node.model_dump(mode='python')
    return dict(node or {}) if isinstance(node, dict) else {}


def telegram_outbox_path(*, repo_root: str | Path, telegram_cfg: dict[str, Any] | None = None) -> Path:
    cfg = dict(telegram_cfg or {})
    raw = cfg.get('outbox_path') or 'runs/alerts/telegram_outbox.jsonl'
    path = _resolve_path(repo_root, raw)
    assert path is not None  # pragma: no cover
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def telegram_state_path(*, repo_root: str | Path, telegram_cfg: dict[str, Any] | None = None) -> Path:
    cfg = dict(telegram_cfg or {})
    raw = cfg.get('state_path') or 'runs/alerts/telegram_state.json'
    path = _resolve_path(repo_root, raw)
    assert path is not None  # pragma: no cover
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + '\n')


def _tail_jsonl(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    obj = {'_raw': line}
                out.append(obj)
    except Exception:
        return []
    return out[-int(limit):]


def _load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'sent_ids': [], 'updated_at_utc': None}


def _save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding='utf-8')


def resolve_telegram_credentials(*, repo_root: str | Path, resolved_config: Any) -> TelegramCredentials:
    notifications = _cfg_section(resolved_config, 'notifications')
    telegram = _nested_cfg(resolved_config, 'notifications', 'telegram')
    security = _cfg_section(resolved_config, 'security')
    trace: list[str] = []
    updates: dict[str, str] = {}

    token_cfg = telegram.get('bot_token')
    if token_cfg not in (None, ''):
        try:
            if hasattr(token_cfg, 'get_secret_value'):
                updates['bot_token'] = token_cfg.get_secret_value()
            else:
                updates['bot_token'] = str(token_cfg).strip()
            trace.append('telegram:config:bot_token')
        except Exception:
            pass
    chat_cfg = telegram.get('chat_id')
    if chat_cfg not in (None, ''):
        updates['chat_id'] = str(chat_cfg).strip()
        trace.append('telegram:config:chat_id')

    bundle_path = _resolve_path(repo_root, os.getenv(str(security.get('secrets_file_env_var') or 'THALOR_SECRETS_FILE')) or security.get('secrets_file'))
    if bundle_path is not None and bundle_path.exists():
        bundle = _read_bundle(bundle_path)
        token = _bundle_get(bundle, ('telegram', 'bot_token'), ('TELEGRAM_BOT_TOKEN',), ('bot_token',))
        chat_id = _bundle_get(bundle, ('telegram', 'chat_id'), ('TELEGRAM_CHAT_ID',), ('chat_id',))
        if token not in (None, ''):
            updates['bot_token'] = str(token).strip()
            trace.append(f'telegram:bundle:bot_token:{bundle_path.name}')
        if chat_id not in (None, ''):
            updates['chat_id'] = str(chat_id).strip()
            trace.append(f'telegram:bundle:chat_id:{bundle_path.name}')

    token_env = os.getenv(str(telegram.get('bot_token_env_var') or 'THALOR_TELEGRAM_BOT_TOKEN'))
    if str(token_env or '').strip():
        updates['bot_token'] = str(token_env).strip()
        trace.append('telegram:env:bot_token')
    chat_env = os.getenv(str(telegram.get('chat_id_env_var') or 'THALOR_TELEGRAM_CHAT_ID'))
    if str(chat_env or '').strip():
        updates['chat_id'] = str(chat_env).strip()
        trace.append('telegram:env:chat_id')

    token_file = _resolve_path(repo_root, os.getenv(str(telegram.get('bot_token_file_env_var') or 'THALOR_TELEGRAM_BOT_TOKEN_FILE')))
    if token_file is not None and token_file.exists():
        token = _read_text(token_file)
        if token:
            updates['bot_token'] = token
            trace.append(f'telegram:file:bot_token:{token_file.name}')
    chat_file = _resolve_path(repo_root, os.getenv(str(telegram.get('chat_id_file_env_var') or 'THALOR_TELEGRAM_CHAT_ID_FILE')))
    if chat_file is not None and chat_file.exists():
        chat_id = _read_text(chat_file)
        if chat_id:
            updates['chat_id'] = chat_id
            trace.append(f'telegram:file:chat_id:{chat_file.name}')

    return TelegramCredentials(bot_token=updates.get('bot_token'), chat_id=updates.get('chat_id'), trace=trace)


def build_message_text(*, title: str, lines: list[str] | tuple[str, ...], severity: str = 'info', parse_mode: str = 'HTML') -> str:
    sev = str(severity or 'info').upper()
    items = [str(x).strip() for x in list(lines or []) if str(x).strip()]
    if str(parse_mode or 'HTML').lower() == 'none':
        head = f'[{sev}] {title}'
        return '\n'.join([head, *items])
    safe_title = html.escape(str(title))
    safe_items = [html.escape(x) for x in items]
    head = f'<b>[{sev}] {safe_title}</b>'
    return '\n'.join([head, *safe_items])


def _telegram_parse_mode(raw: str | None) -> str | None:
    mode = str(raw or 'HTML').strip()
    if mode.lower() == 'none':
        return None
    return mode or None


def _send_telegram_message(*, token: str, chat_id: str, text: str, parse_mode: str | None, timeout_sec: int) -> dict[str, Any]:
    data = {
        'chat_id': str(chat_id),
        'text': str(text),
        'disable_web_page_preview': 'true',
    }
    if parse_mode:
        data['parse_mode'] = str(parse_mode)
    body = parse.urlencode(data).encode('utf-8')
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    req = request.Request(url, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with request.urlopen(req, timeout=max(1, int(timeout_sec))) as resp:  # nosec B310 - controlled API endpoint
        raw = resp.read().decode('utf-8', errors='replace')
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {'raw': raw}
        return {
            'http_status': getattr(resp, 'status', None),
            'response': payload,
        }


def _alert_id(source: str, title: str, text: str) -> str:
    seed = f'{source}|{title}|{text}'
    return sha1(seed.encode('utf-8')).hexdigest()[:20]


def _sanitize_alert_payload(payload: dict[str, Any], *, credentials: TelegramCredentials) -> dict[str, Any]:
    sensitive_values = collect_sensitive_values(
        {
            'notifications': {
                'telegram': {
                    'bot_token': credentials.bot_token,
                    'chat_id': credentials.chat_id,
                }
            }
        },
        redact_email=True,
    )
    out = sanitize_payload(payload, sensitive_values=sensitive_values, redact_email=True)
    if isinstance(out, dict):
        out['credentials_present'] = bool(payload.get('credentials_present'))
        out['credential_trace'] = list(payload.get('credential_trace') or [])
    return out


def dispatch_telegram_alert(
    *,
    repo_root: str | Path = '.',
    resolved_config: Any,
    title: str,
    lines: list[str] | tuple[str, ...],
    severity: str = 'info',
    source: str = 'manual',
    force_send: bool | None = None,
) -> dict[str, Any]:
    notifications = _cfg_section(resolved_config, 'notifications')
    telegram = _nested_cfg(resolved_config, 'notifications', 'telegram')
    enabled = bool(notifications.get('enabled', True)) and bool(telegram.get('enabled', False))
    creds = resolve_telegram_credentials(repo_root=repo_root, resolved_config=resolved_config)
    parse_mode = _telegram_parse_mode(str(telegram.get('parse_mode') or 'HTML'))
    text = build_message_text(title=title, lines=list(lines), severity=severity, parse_mode=str(parse_mode or 'none'))
    send_enabled = bool(telegram.get('send_enabled', False)) if force_send is None else bool(force_send)
    outbox = telegram_outbox_path(repo_root=repo_root, telegram_cfg=telegram)
    state_path = telegram_state_path(repo_root=repo_root, telegram_cfg=telegram)

    payload: dict[str, Any] = {
        'kind': 'telegram_alert',
        'alert_id': _alert_id(str(source), str(title), str(text)),
        'at_utc': _now_utc(),
        'source': str(source),
        'severity': str(severity or 'info'),
        'title': str(title),
        'text': str(text),
        'parse_mode': parse_mode,
        'enabled': enabled,
        'send_enabled': send_enabled,
        'credentials_present': creds.present,
        'credential_trace': list(creds.trace),
        'delivery': {'status': 'disabled' if not enabled else 'queued'},
    }

    if enabled and send_enabled and creds.present:
        try:
            sent = _send_telegram_message(
                token=str(creds.bot_token),
                chat_id=str(creds.chat_id),
                text=str(text),
                parse_mode=parse_mode,
                timeout_sec=int(telegram.get('timeout_sec') or 10),
            )
            payload['delivery'] = {
                'status': 'sent',
                'attempted_at_utc': _now_utc(),
                'http_status': sent.get('http_status'),
                'response_ok': bool((sent.get('response') or {}).get('ok', True)),
            }
            state = _load_state(state_path)
            sent_ids = [str(x) for x in list(state.get('sent_ids') or []) if str(x).strip()]
            if payload['alert_id'] not in sent_ids:
                sent_ids.append(str(payload['alert_id']))
            state.update(
                {
                    'updated_at_utc': _now_utc(),
                    'sent_ids': sent_ids[-500:],
                    'last_alert': {'alert_id': payload['alert_id'], 'severity': payload['severity'], 'title': payload['title']},
                    'last_delivery': payload['delivery'],
                }
            )
            _save_state(state_path, state)
        except Exception as exc:
            payload['delivery'] = {
                'status': 'failed',
                'attempted_at_utc': _now_utc(),
                'error_type': type(exc).__name__,
                'error_message': str(exc),
            }
            state = _load_state(state_path)
            state.update({'updated_at_utc': _now_utc(), 'last_delivery': payload['delivery']})
            _save_state(state_path, state)
    elif enabled:
        state = _load_state(state_path)
        state.update({'updated_at_utc': _now_utc(), 'last_delivery': payload['delivery']})
        _save_state(state_path, state)

    safe = _sanitize_alert_payload(payload, credentials=creds)
    _append_jsonl(outbox, safe)
    return safe


def load_recent_alerts(*, repo_root: str | Path = '.', resolved_config: Any, limit: int = 20) -> list[dict[str, Any]]:
    telegram = _nested_cfg(resolved_config, 'notifications', 'telegram')
    return _tail_jsonl(telegram_outbox_path(repo_root=repo_root, telegram_cfg=telegram), limit=max(1, int(limit)))


def alerts_status_payload(*, repo_root: str | Path = '.', resolved_config: Any, limit: int = 20) -> dict[str, Any]:
    notifications = _cfg_section(resolved_config, 'notifications')
    telegram = _nested_cfg(resolved_config, 'notifications', 'telegram')
    creds = resolve_telegram_credentials(repo_root=repo_root, resolved_config=resolved_config)
    outbox = telegram_outbox_path(repo_root=repo_root, telegram_cfg=telegram)
    state_path = telegram_state_path(repo_root=repo_root, telegram_cfg=telegram)
    recent = _tail_jsonl(outbox, max(1, int(limit)))
    counts = {'queued': 0, 'sent': 0, 'failed': 0, 'disabled': 0}
    for item in recent:
        status = str(((item.get('delivery') or {}).get('status') or 'queued'))
        counts[status] = counts.get(status, 0) + 1
    state = _load_state(state_path)
    return {
        'at_utc': _now_utc(),
        'kind': 'alerts_status',
        'enabled': bool(notifications.get('enabled', True)),
        'history_limit': int(notifications.get('history_limit') or 200),
        'telegram': {
            'enabled': bool(telegram.get('enabled', False)),
            'send_enabled': bool(telegram.get('send_enabled', False)),
            'credentials_present': creds.present,
            'credential_trace': list(creds.trace),
            'outbox_path': str(outbox),
            'state_path': str(state_path),
            'recent_counts': counts,
            'recent': recent,
            'state': state,
        },
    }


def flush_pending_alerts(*, repo_root: str | Path = '.', resolved_config: Any, limit: int = 20) -> dict[str, Any]:
    telegram = _nested_cfg(resolved_config, 'notifications', 'telegram')
    creds = resolve_telegram_credentials(repo_root=repo_root, resolved_config=resolved_config)
    outbox = telegram_outbox_path(repo_root=repo_root, telegram_cfg=telegram)
    state_path = telegram_state_path(repo_root=repo_root, telegram_cfg=telegram)
    state = _load_state(state_path)
    sent_ids = {str(x) for x in list(state.get('sent_ids') or []) if str(x).strip()}
    recent = _tail_jsonl(outbox, max(1, int(limit) * 5))
    send_enabled = bool(telegram.get('send_enabled', False))
    attempts: list[dict[str, Any]] = []

    if not (send_enabled and creds.present):
        return {
            'at_utc': _now_utc(),
            'kind': 'alerts_flush',
            'ok': False,
            'message': 'telegram_send_not_ready',
            'send_enabled': send_enabled,
            'credentials_present': creds.present,
            'attempted': attempts,
        }

    pending = [
        item for item in recent
        if str(item.get('alert_id') or '').strip()
        and str(item.get('alert_id')) not in sent_ids
        and str(((item.get('delivery') or {}).get('status') or 'queued')) in {'queued', 'failed'}
    ]

    for item in pending[: max(1, int(limit))]:
        title = str(item.get('title') or 'Thalor alert')
        lines = [str(item.get('text') or '')]
        resent = dispatch_telegram_alert(
            repo_root=repo_root,
            resolved_config=resolved_config,
            title=title,
            lines=lines,
            severity=str(item.get('severity') or 'info'),
            source=f"flush:{item.get('source') or 'unknown'}",
            force_send=True,
        )
        attempts.append(resent)

    return {
        'at_utc': _now_utc(),
        'kind': 'alerts_flush',
        'ok': True,
        'message': 'flush_complete',
        'attempted_count': len(attempts),
        'attempted': attempts,
    }
