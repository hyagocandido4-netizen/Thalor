from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..ops.audit_common import sqlite_count, sqlite_open, sqlite_quick_check, sqlite_tables, summarize_status
from ..ops.diagnostic_utils import check, dedupe_actions, load_selected_scopes, resolve_scope_paths
from ..state.control_repo import write_control_artifact, write_repo_control_artifact


def _inspect_db(*, name: str, path: Path, required: bool, expected_tables: list[str], extra_counts: dict[str, str] | None = None) -> dict[str, Any]:
    if not path.exists():
        status = 'error' if required else 'warn'
        return {
            'name': name,
            'path': str(path),
            'exists': False,
            'status': status,
            'message': 'DB ausente' if required else 'DB opcional ausente',
            'expected_tables': expected_tables,
            'tables': [],
            'quick_check': None,
            'counts': {},
        }
    try:
        quick = sqlite_quick_check(path)
        tables = sqlite_tables(path)
    except Exception as exc:
        return {
            'name': name,
            'path': str(path),
            'exists': True,
            'status': 'error' if required else 'warn',
            'message': f'SQLite ilegível: {type(exc).__name__}: {exc}',
            'expected_tables': expected_tables,
            'tables': [],
            'quick_check': None,
            'counts': {},
        }
    missing = [table for table in expected_tables if table not in tables]
    counts: dict[str, Any] = {}
    if extra_counts:
        for label, table in extra_counts.items():
            counts[label] = sqlite_count(path, table)
    status = 'ok'
    message = 'DB íntegro'
    if quick != 'ok':
        status = 'error' if required else 'warn'
        message = f'PRAGMA quick_check={quick}'
    elif missing:
        status = 'error' if required else 'warn'
        message = f'Tabelas ausentes: {missing}'
    return {
        'name': name,
        'path': str(path),
        'exists': True,
        'status': status,
        'message': message,
        'expected_tables': expected_tables,
        'tables': tables,
        'missing_tables': missing,
        'quick_check': quick,
        'counts': counts,
    }


def _candles_scope_count(path: Path, *, asset: str, interval_sec: int) -> int | None:
    if not path.exists():
        return None
    con = sqlite_open(path)
    try:
        row = con.execute('SELECT COUNT(*) FROM candles WHERE asset=? AND interval_sec=?', (asset, int(interval_sec))).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None
    finally:
        con.close()


def _scope_payload(*, repo: Path, cfg_path: Path, asset: str, interval_sec: int) -> dict[str, Any]:
    ctx = build_context(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)
    _, _, cfg, scopes = load_selected_scopes(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec, all_scopes=False)
    scope_obj = scopes[0] if scopes else None
    scope_paths = resolve_scope_paths(repo_root=repo, cfg=cfg, scope=scope_obj) if scope_obj is not None else None
    checks: list[dict[str, Any]] = []
    actions: list[str] = []

    runtime_control = _inspect_db(
        name='runtime_control',
        path=repo / 'runs' / 'runtime_control.sqlite3',
        required=True,
        expected_tables=['circuit_breakers', 'cycle_health'],
        extra_counts={'circuit_breakers': 'circuit_breakers', 'cycle_health': 'cycle_health'},
    )
    checks.append(check('runtime_control_db', runtime_control['status'], runtime_control['message'], path=runtime_control['path'], quick_check=runtime_control['quick_check']))

    runtime_execution = _inspect_db(
        name='runtime_execution',
        path=repo / 'runs' / 'runtime_execution.sqlite3',
        required=False,
        expected_tables=['order_intents', 'order_submit_attempts', 'broker_orders', 'order_events', 'reconcile_cursors'],
        extra_counts={'order_intents': 'order_intents', 'broker_orders': 'broker_orders', 'order_events': 'order_events'},
    )
    checks.append(check('runtime_execution_db', runtime_execution['status'], runtime_execution['message'], path=runtime_execution['path'], quick_check=runtime_execution['quick_check']))

    market_db_path = scope_paths['data'].db_path if scope_paths is not None else repo / 'data' / 'market_otc.sqlite3'
    market_db = _inspect_db(
        name='market_data',
        path=Path(str(market_db_path)),
        required=True,
        expected_tables=['candles'],
        extra_counts={'candles_total': 'candles'},
    )
    market_db['candles_scope'] = _candles_scope_count(Path(str(market_db_path)), asset=asset, interval_sec=interval_sec)
    checks.append(check('market_db', market_db['status'], market_db['message'], path=market_db['path'], candles_scope=market_db.get('candles_scope')))
    if market_db.get('candles_scope') in (None, 0):
        actions.append('Repopule o market DB do scope com collect_recent antes de operar.')

    signals_db_path = scope_paths['runtime'].signals_db_path if scope_paths is not None else repo / 'runs' / 'live_signals.sqlite3'
    signals_db = _inspect_db(
        name='signals_db',
        path=Path(str(signals_db_path)),
        required=False,
        expected_tables=['signals_v2'],
        extra_counts={'signals_v2': 'signals_v2'},
    )
    checks.append(check('signals_db', signals_db['status'], signals_db['message'], path=signals_db['path']))

    state_db_path = scope_paths['runtime'].state_db_path if scope_paths is not None else repo / 'runs' / 'live_topk_state.sqlite3'
    state_db = _inspect_db(
        name='state_db',
        path=Path(str(state_db_path)),
        required=False,
        expected_tables=['executed'],
        extra_counts={'executed': 'executed'},
    )
    checks.append(check('state_db', state_db['status'], state_db['message'], path=state_db['path']))

    severity = summarize_status(checks)
    payload = {
        'kind': 'state_db_audit',
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'ok': severity != 'error',
        'severity': severity,
        'scope': {'asset': asset, 'interval_sec': int(interval_sec), 'scope_tag': str(ctx.scope.scope_tag)},
        'databases': {
            'runtime_control': runtime_control,
            'runtime_execution': runtime_execution,
            'market_data': market_db,
            'signals_db': signals_db,
            'state_db': state_db,
        },
        'checks': checks,
        'actions': dedupe_actions(actions),
    }
    write_control_artifact(repo_root=repo, asset=asset, interval_sec=interval_sec, name='state_db_audit', payload=payload)
    return payload


def build_state_db_audit_payload(
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
    results = [_scope_payload(repo=repo, cfg_path=cfg_path, asset=str(scope.asset), interval_sec=int(scope.interval_sec)) for scope in scopes]
    scope_severities = [str(item.get('severity') or 'ok') for item in results]
    severity = 'error' if 'error' in scope_severities else ('warn' if 'warn' in scope_severities else 'ok')
    payload = {
        'kind': 'state_db_audit',
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'summary': {
            'scope_count': len(results),
            'multi_asset_enabled': bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
            'error_scopes': [item['scope']['scope_tag'] for item in results if str(item.get('severity')) == 'error'],
            'warn_scopes': [item['scope']['scope_tag'] for item in results if str(item.get('severity')) == 'warn'],
        },
        'scope_results': results,
        'actions': dedupe_actions([action for result in results for action in list(result.get('actions') or [])]),
    }
    if write_artifact:
        write_repo_control_artifact(repo_root=repo, name='state_db_audit', payload=payload)
    return payload

