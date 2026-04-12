from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEDUPE_STATE_FILE = 'provider_issue_dedupe.json'

from ..utils.provider_issue_taxonomy import classify_provider_issue


def _repo_root(repo_root: str | Path | None = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    env_root = os.getenv('THALOR_REPO_ROOT') or os.getenv('REPO_ROOT')
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd().resolve()


def provider_issue_log_path(repo_root: str | Path | None = None) -> Path:
    return _repo_root(repo_root) / 'runs' / 'logs' / 'provider_issues.jsonl'


def _provider_issue_dedupe_path(repo_root: str | Path | None = None) -> Path:
    return _repo_root(repo_root) / 'runs' / 'logs' / _DEDUPE_STATE_FILE


def _read_dedupe_state(repo_root: str | Path | None = None) -> dict[str, float]:
    path = _provider_issue_dedupe_path(repo_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in data.items():
        try:
            out[str(key)] = float(value)
        except Exception:
            continue
    return out


def _write_dedupe_state(state: dict[str, float], repo_root: str | Path | None = None) -> None:
    path = _provider_issue_dedupe_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _dedupe_key(*, source: str, operation: str, normalized_reason: str, override: str | None = None) -> str:
    if override and str(override).strip():
        return str(override).strip()
    return f"{source}|{operation}|{normalized_reason.strip()}"


def record_provider_issue_event(
    *,
    operation: str,
    reason: Any,
    repo_root: str | Path | None = None,
    source: str = 'iq_client',
    scope_tag: str | None = None,
    extra: dict[str, Any] | None = None,
    dedupe_window_sec: float = 0.0,
    dedupe_key: str | None = None,
) -> dict[str, Any] | None:
    try:
        info = classify_provider_issue(reason)
        normalized_reason = str(info.get('normalized_reason') or '').strip() or str(reason)
        if float(dedupe_window_sec or 0.0) > 0.0:
            state = _read_dedupe_state(repo_root)
            key = _dedupe_key(source=str(source), operation=str(operation), normalized_reason=normalized_reason, override=dedupe_key)
            now_ts = datetime.now(tz=UTC).timestamp()
            last_ts = float(state.get(key, 0.0) or 0.0)
            if last_ts > 0.0 and (now_ts - last_ts) < float(dedupe_window_sec):
                return None
            state[key] = now_ts
            # keep the file bounded and recent only
            cutoff = now_ts - max(float(dedupe_window_sec) * 10.0, 86400.0)
            state = {k: v for k, v in state.items() if float(v) >= cutoff}
            _write_dedupe_state(state, repo_root)
        payload: dict[str, Any] = {
            'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
            'source': str(source),
            'operation': str(operation),
            'reason': str(reason),
            'category': str(info.get('category') or 'unknown'),
            'severity_hint': str(info.get('severity_hint') or 'warn'),
            'normalized_reason': normalized_reason,
        }
        if scope_tag:
            payload['scope_tag'] = str(scope_tag)
        if isinstance(extra, dict) and extra:
            payload['extra'] = dict(extra)
        path = provider_issue_log_path(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + '\n')
        return payload
    except Exception:
        return None


def read_provider_issue_events(repo_root: str | Path | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    path = provider_issue_log_path(repo_root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines()[-max(1, int(limit)):]:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
    except Exception:
        return []
    return rows


__all__ = ['provider_issue_log_path', 'read_provider_issue_events', 'record_provider_issue_event']
