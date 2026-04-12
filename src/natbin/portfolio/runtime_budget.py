from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence, TypeVar

T = TypeVar('T')


def budget_cursor_path(repo_root: str | Path = '.') -> Path:
    return Path(repo_root).resolve() / 'runs' / 'control' / '_repo' / 'provider_scope_budget_cursor.json'


def read_budget_cursor(repo_root: str | Path = '.') -> int:
    path = budget_cursor_path(repo_root)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return 0
    try:
        return max(0, int(data.get('cursor') or 0))
    except Exception:
        return 0


def write_budget_cursor(repo_root: str | Path, cursor: int) -> None:
    path = budget_cursor_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'cursor': int(max(0, cursor))}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def select_governed_items(
    items: Sequence[T],
    *,
    repo_root: str | Path,
    budget: int,
    scope_order: str = 'best_first_round_robin',
) -> tuple[list[T], dict[str, Any]]:
    ordered = list(items)
    total = len(ordered)
    if total <= 0:
        return [], {
            'budget_scope_count': 0,
            'scanned_scope_count': 0,
            'skipped_scope_count': 0,
            'scope_cursor_before': 0,
            'scope_cursor_after': 0,
        }
    budget = max(1, min(total, int(budget or total)))
    if budget >= total or total <= 1:
        return ordered, {
            'budget_scope_count': budget,
            'scanned_scope_count': total,
            'skipped_scope_count': 0,
            'scope_cursor_before': 0,
            'scope_cursor_after': 0,
        }
    cursor_before = read_budget_cursor(repo_root)
    if str(scope_order or '').strip().lower() != 'best_first_round_robin':
        return ordered[:budget], {
            'budget_scope_count': budget,
            'scanned_scope_count': budget,
            'skipped_scope_count': total - budget,
            'scope_cursor_before': cursor_before,
            'scope_cursor_after': cursor_before,
        }

    best = ordered[:1]
    rest = ordered[1:]
    if not rest:
        return best, {
            'budget_scope_count': budget,
            'scanned_scope_count': len(best),
            'skipped_scope_count': total - len(best),
            'scope_cursor_before': cursor_before,
            'scope_cursor_after': cursor_before,
        }

    span = max(0, budget - 1)
    cursor = cursor_before % len(rest)
    selected_rest: list[T] = []
    if span > 0:
        for idx in range(span):
            selected_rest.append(rest[(cursor + idx) % len(rest)])
    cursor_after = (cursor + max(1, span)) % len(rest)
    write_budget_cursor(repo_root, cursor_after)
    selected = best + selected_rest
    return selected, {
        'budget_scope_count': budget,
        'scanned_scope_count': len(selected),
        'skipped_scope_count': total - len(selected),
        'scope_cursor_before': cursor_before,
        'scope_cursor_after': cursor_after,
    }


def decide_prepare_strategy(
    *,
    adaptive_prepare_enable: bool,
    db_exists: bool,
    db_rows: int,
    db_fresh: bool,
    market_context_exists: bool,
    market_context_fresh: bool,
    market_context_dependency_available: Any,
    full_lookback_candles: int,
    incremental_lookback_candles: int | None,
) -> dict[str, Any]:
    full_lookback = max(32, int(full_lookback_candles or 0))
    incremental = incremental_lookback_candles
    if incremental in (None, 0):
        incremental = min(full_lookback, 256)
    incremental = max(32, min(full_lookback, int(incremental)))

    if not bool(adaptive_prepare_enable):
        return {
            'strategy': 'full_prepare',
            'effective_lookback_candles': full_lookback,
            'skip_prepare': False,
            'refresh_only': False,
            'uses_incremental_lookback': False,
        }

    has_local_db = bool(db_exists and int(db_rows or 0) > 0)
    market_context_usable = bool(market_context_exists and market_context_fresh and market_context_dependency_available is not False)

    if bool(db_fresh) and market_context_usable:
        return {
            'strategy': 'skip_fresh',
            'effective_lookback_candles': None,
            'skip_prepare': True,
            'refresh_only': False,
            'uses_incremental_lookback': False,
        }
    if bool(db_fresh) and has_local_db:
        return {
            'strategy': 'refresh_only',
            'effective_lookback_candles': None,
            'skip_prepare': False,
            'refresh_only': True,
            'uses_incremental_lookback': False,
        }
    if has_local_db:
        return {
            'strategy': 'incremental_prepare',
            'effective_lookback_candles': incremental,
            'skip_prepare': False,
            'refresh_only': False,
            'uses_incremental_lookback': True,
        }
    return {
        'strategy': 'full_prepare',
        'effective_lookback_candles': full_lookback,
        'skip_prepare': False,
        'refresh_only': False,
        'uses_incremental_lookback': False,
    }
