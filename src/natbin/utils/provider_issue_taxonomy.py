from __future__ import annotations

import re
from typing import Any, Iterable

_CATEGORY_ORDER = [
    'transport_proxy',
    'auth_credentials',
    'session_parse',
    'websocket_lifecycle',
    'upstream_digital_metadata',
    'timeout',
    'intelligence_cp_meta',
    'local_artifact',
    'strategy_no_trade',
    'unknown',
]

_PATTERNS: list[tuple[str, str, tuple[re.Pattern[str], ...]]] = [
    (
        'transport_proxy',
        'error',
        (
            re.compile(r'ProxyError', re.I),
            re.compile(r'socks(?:5h?)?://', re.I),
            re.compile(r'HTTP 407', re.I),
            re.compile(r'proxy.*auth', re.I),
            re.compile(r'connection refused', re.I),
            re.compile(r'no route to host', re.I),
            re.compile(r'name or service not known', re.I),
            re.compile(r'failed to establish a new connection', re.I),
        ),
    ),
    (
        'auth_credentials',
        'error',
        (
            re.compile(r'missing_credentials', re.I),
            re.compile(r'invalid credentials', re.I),
            re.compile(r'wrong password', re.I),
            re.compile(r'authentication', re.I),
            re.compile(r'login .*failed', re.I),
            re.compile(r'credential', re.I),
        ),
    ),
    (
        'session_parse',
        'warn',
        (
            re.compile(r'JSONDecodeError', re.I),
            re.compile(r'Expecting value', re.I),
            re.compile(r'Extra data', re.I),
            re.compile(r'Unterminated string', re.I),
        ),
    ),
    (
        'websocket_lifecycle',
        'warn',
        (
            re.compile(r'Connection is already closed', re.I),
            re.compile(r'need reconnect', re.I),
            re.compile(r'get_all_init late', re.I),
            re.compile(r'websocket', re.I),
            re.compile(r'ws\.client', re.I),
            re.compile(r'broken pipe', re.I),
        ),
    ),
    (
        'upstream_digital_metadata',
        'warn',
        (
            re.compile(r"KeyError: ['\"]underlying['\"]", re.I),
            re.compile(r'get_digital_underlying_list_data', re.I),
            re.compile(r'__get_digital_open', re.I),
            re.compile(r'underlying payload', re.I),
            re.compile(r'missing_underlying_list', re.I),
            re.compile(r'digital_underlying_payload', re.I),
        ),
    ),
    (
        'timeout',
        'warn',
        (
            re.compile(r'timed out', re.I),
            re.compile(r'\breturncode\b[^\n]*124', re.I),
            re.compile(r'\btimeout\b', re.I),
            re.compile(r'late 30 sec', re.I),
        ),
    ),
    (
        'intelligence_cp_meta',
        'warn',
        (
            re.compile(r'cp_fail_closed_missing_cp_meta', re.I),
            re.compile(r'missing[_ ]cp_meta', re.I),
            re.compile(r'cp[_-]?meta.*missing', re.I),
        ),
    ),
    (
        'local_artifact',
        'warn',
        (
            re.compile(r'market_context stale', re.I),
            re.compile(r'control_freshness', re.I),
            re.compile(r'db_stale', re.I),
            re.compile(r'candle_db_local', re.I),
            re.compile(r'loop_status', re.I),
            re.compile(r'health\.json', re.I),
        ),
    ),
    (
        'strategy_no_trade',
        'ok',
        (
            re.compile(r'\bHOLD\b', re.I),
            re.compile(r'regime_block', re.I),
            re.compile(r'wait_signal', re.I),
            re.compile(r'wait_regime_rescan', re.I),
            re.compile(r'rescan_next_candle', re.I),
            re.compile(r'below_ev_threshold', re.I),
            re.compile(r'not_in_topk_today', re.I),
            re.compile(r'cp_reject', re.I),
            re.compile(r'portfolio_feedback', re.I),
            re.compile(r'no-trade', re.I),
        ),
    ),
]


def _ignore_benign_text(raw: str) -> bool:
    text = str(raw or '').strip()
    if not text:
        return True
    lowered = text.lower()
    if lowered in {'returncode=0', 'timed_out=false', 'watch', 'hold', 'ready', 'actionable'}:
        return True
    if lowered in {'provider_ready', 'ready_for_cycle', 'ready_for_practice', 'ready_for_live', 'ready_for_real', 'market_context_fresh', 'market_open', 'quota_available'}:
        return True
    if lowered in {'wait_signal_and_rescan', 'wait_regime_rescan', 'wait_regime_rescan_backfill_cp_meta', 'rescan_next_candle', 'hold_regime_block', 'refresh_artifacts_and_rescan', 'capture_practice_evidence'}:
        return True
    if lowered in {'cp_meta_iso', 'cp', 'iso'}:
        return True
    if lowered.startswith('window_state=') or lowered.startswith('recommended_action='):
        return True
    if lowered.startswith('strategy=') and lowered.endswith('skip_fresh'):
        return True
    if text.startswith('{') and text.endswith('}'):
        try:
            import json
            payload = json.loads(text)
        except Exception:
            payload = None
        if isinstance(payload, dict) and {'asset', 'interval_sec', 'market_open', 'open_source'}.issubset(set(payload.keys())):
            return True
    return False


def classify_provider_issue(text: Any) -> dict[str, Any]:
    raw = '' if text is None else str(text)
    if _ignore_benign_text(raw):
        return {
            'category': 'unknown',
            'severity_hint': 'ok',
            'normalized_reason': '',
        }
    for category, severity_hint, patterns in _PATTERNS:
        for pattern in patterns:
            if pattern.search(raw):
                return {
                    'category': category,
                    'severity_hint': severity_hint,
                    'normalized_reason': raw.strip() or category,
                }
    return {
        'category': 'unknown',
        'severity_hint': 'warn' if raw.strip() else 'ok',
        'normalized_reason': raw.strip() or 'no_issue',
    }


def aggregate_provider_issue_texts(texts: Iterable[Any], *, max_examples: int = 3) -> dict[str, Any]:
    out: dict[str, Any] = {}
    total = 0
    for item in texts:
        if item in (None, ''):
            continue
        info = classify_provider_issue(item)
        if not str(info.get('normalized_reason') or '').strip() and str(info.get('severity_hint') or 'ok') == 'ok':
            continue
        category = str(info.get('category') or 'unknown')
        bucket = out.setdefault(category, {
            'category': category,
            'count': 0,
            'severity_hint': str(info.get('severity_hint') or 'warn'),
            'examples': [],
        })
        bucket['count'] = int(bucket.get('count') or 0) + 1
        total += 1
        example = str(info.get('normalized_reason') or '').strip()
        if example and example not in bucket['examples'] and len(bucket['examples']) < max(1, int(max_examples)):
            bucket['examples'].append(example)
        sev = str(info.get('severity_hint') or 'warn')
        if sev == 'error' or str(bucket.get('severity_hint')) == 'ok':
            bucket['severity_hint'] = sev
    ordered: list[dict[str, Any]] = []
    for category in _CATEGORY_ORDER:
        if category in out:
            ordered.append(out[category])
    for category, payload in out.items():
        if category not in _CATEGORY_ORDER:
            ordered.append(payload)
    return {
        'total_events': total,
        'categories': ordered,
    }


def issue_categories_to_map(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in list(payload.get('categories') or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get('category') or '').strip()
        if key:
            out[key] = dict(item)
    return out


__all__ = ['aggregate_provider_issue_texts', 'classify_provider_issue', 'issue_categories_to_map']
