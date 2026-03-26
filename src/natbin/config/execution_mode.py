from __future__ import annotations

from typing import Any

MODE_DISABLED = 'disabled'
MODE_PAPER = 'paper'
MODE_LIVE = 'live'
MODE_PRACTICE = 'practice'

BROKER_SUBMIT_MODES = frozenset({MODE_LIVE, MODE_PRACTICE})


_ALIAS_MAP = {
    '': MODE_DISABLED,
    '0': MODE_DISABLED,
    'false': MODE_DISABLED,
    'off': MODE_DISABLED,
    'disable': MODE_DISABLED,
    'disabled': MODE_DISABLED,
    'none': MODE_DISABLED,
    'paper': MODE_PAPER,
    'fake': MODE_PAPER,
    'sim': MODE_PAPER,
    'simulation': MODE_PAPER,
    'sandbox': MODE_PAPER,
    'live': MODE_LIVE,
    'real': MODE_LIVE,
    'practice': MODE_PRACTICE,
    'demo': MODE_PRACTICE,
}


def normalize_execution_mode(value: Any, *, default: str = MODE_DISABLED) -> str:
    raw = '' if value is None else str(value).strip().lower()
    if raw in _ALIAS_MAP:
        return _ALIAS_MAP[raw]
    if not raw:
        return str(default)
    return raw



def execution_mode_enabled(value: Any) -> bool:
    return normalize_execution_mode(value) != MODE_DISABLED



def execution_mode_uses_broker_submit(value: Any) -> bool:
    return normalize_execution_mode(value) in BROKER_SUBMIT_MODES



def execution_mode_is_practice(value: Any) -> bool:
    return normalize_execution_mode(value) == MODE_PRACTICE


__all__ = [
    'BROKER_SUBMIT_MODES',
    'MODE_DISABLED',
    'MODE_LIVE',
    'MODE_PAPER',
    'MODE_PRACTICE',
    'execution_mode_enabled',
    'execution_mode_is_practice',
    'execution_mode_uses_broker_submit',
    'normalize_execution_mode',
]
