from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ..config import load_thalor_config
from ..control.commands import (
    alerts_payload,
    doctor_payload,
    health_payload,
    incidents_payload,
    practice_payload,
    portfolio_status_payload,
    release_payload,
    security_payload,
)
from ..runtime.broker_surface import execution_repo_path


TERMINAL_SETTLEMENTS = {'win', 'loss', 'refund', 'cancelled'}


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _severity_tone(value: Any) -> str:
    sev = str(value or 'info').strip().lower()
    if sev in {'ok', 'ready', 'healthy', 'open', 'accepted'}:
        return 'ok'
    if sev in {'warn', 'warning', 'pending', 'cooldown'}:
        return 'warn'
    if sev in {'error', 'critical', 'blocked', 'rejected', 'loss'}:
        return 'danger'
    return 'accent'


def _parse_utc(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open('r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    payload = {'_raw': line}
                if isinstance(payload, dict):
                    rows.append(payload)
    except Exception:
        return []
    return rows[-int(limit):]


def _sqlite_connect_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    uri = f"file:{db_path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=1.0)
    con.row_factory = sqlite3.Row
    return con


def _execution_rows(repo_root: Path, *, trade_limit: int = 2000, attempt_limit: int = 500, event_limit: int = 500) -> dict[str, list[dict[str, Any]]]:
    db_path = execution_repo_path(repo_root)
    con = _sqlite_connect_ro(db_path)
    if con is None:
        return {'broker_orders': [], 'attempts': [], 'events': []}
    try:
        broker_rows = [
            dict(row)
            for row in con.execute(
                '''
                SELECT
                    b.broker_name,
                    b.account_mode,
                    b.external_order_id,
                    b.intent_id,
                    b.client_order_key,
                    b.asset,
                    b.side,
                    b.amount,
                    b.currency,
                    b.broker_status,
                    b.opened_at_utc,
                    b.expires_at_utc,
                    b.closed_at_utc,
                    b.gross_payout,
                    b.net_pnl,
                    b.settlement_status,
                    b.estimated_pnl,
                    b.last_seen_at_utc,
                    i.scope_tag,
                    i.interval_sec,
                    i.intent_state,
                    i.signal_ts
                FROM broker_orders b
                LEFT JOIN order_intents i ON i.intent_id=b.intent_id
                ORDER BY COALESCE(b.closed_at_utc, b.last_seen_at_utc, b.opened_at_utc) DESC
                LIMIT ?
                ''',
                (int(trade_limit),),
            ).fetchall()
        ]
        attempt_rows = [
            dict(row)
            for row in con.execute(
                '''
                SELECT
                    a.attempt_id,
                    a.intent_id,
                    a.attempt_no,
                    a.requested_at_utc,
                    a.finished_at_utc,
                    a.transport_status,
                    a.latency_ms,
                    a.external_order_id,
                    a.error_code,
                    a.error_message,
                    i.asset,
                    i.scope_tag,
                    i.interval_sec,
                    i.intent_state
                FROM order_submit_attempts a
                LEFT JOIN order_intents i ON i.intent_id=a.intent_id
                ORDER BY a.requested_at_utc DESC
                LIMIT ?
                ''',
                (int(attempt_limit),),
            ).fetchall()
        ]
        event_rows = [
            dict(row)
            for row in con.execute(
                '''
                SELECT event_id, intent_id, broker_name, account_mode, external_order_id, event_type, payload_json, created_at_utc
                FROM order_events
                ORDER BY created_at_utc DESC
                LIMIT ?
                ''',
                (int(event_limit),),
            ).fetchall()
        ]
    finally:
        con.close()
    return {'broker_orders': broker_rows, 'attempts': attempt_rows, 'events': event_rows}


def _normalize_trade(row: dict[str, Any]) -> dict[str, Any]:
    settlement_status = str(row.get('settlement_status') or '').strip().lower() or None
    broker_status = str(row.get('broker_status') or '').strip().lower() or None
    amount = _safe_float(row.get('amount') or 0.0)
    net_pnl = row.get('net_pnl')
    net_pnl_value = _safe_float(net_pnl) if net_pnl not in (None, '') else None
    ts_candidates = [row.get('closed_at_utc'), row.get('last_seen_at_utc'), row.get('opened_at_utc')]
    trade_ts = None
    for cand in ts_candidates:
        trade_ts = _parse_utc(cand)
        if trade_ts is not None:
            break
    if settlement_status in TERMINAL_SETTLEMENTS:
        realized = True
    else:
        realized = False
    resolved = settlement_status or broker_status or 'unknown'
    if settlement_status is None and broker_status in {'closed', 'settled', 'cancelled'}:
        resolved = broker_status
        realized = True
    if resolved in {'closed', 'settled'}:
        if net_pnl_value is not None and net_pnl_value > 0:
            resolved = 'win'
        elif net_pnl_value is not None and net_pnl_value < 0:
            resolved = 'loss'
        else:
            resolved = 'refund'
    if net_pnl_value is None:
        net_pnl_value = 0.0
    return {
        'broker_name': row.get('broker_name'),
        'account_mode': row.get('account_mode'),
        'external_order_id': row.get('external_order_id'),
        'intent_id': row.get('intent_id'),
        'client_order_key': row.get('client_order_key'),
        'scope_tag': row.get('scope_tag'),
        'asset': row.get('asset'),
        'interval_sec': row.get('interval_sec'),
        'side': row.get('side'),
        'amount': amount,
        'currency': row.get('currency'),
        'broker_status': broker_status,
        'settlement_status': settlement_status,
        'resolved_status': resolved,
        'opened_at_utc': row.get('opened_at_utc'),
        'closed_at_utc': row.get('closed_at_utc'),
        'last_seen_at_utc': row.get('last_seen_at_utc'),
        'trade_at_utc': trade_ts.isoformat(timespec='seconds') if trade_ts is not None else None,
        'gross_payout': _safe_float(row.get('gross_payout') or 0.0),
        'net_pnl': net_pnl_value,
        'estimated_pnl': bool(row.get('estimated_pnl')),
        'realized': realized,
        'intent_state': row.get('intent_state'),
        'signal_ts': row.get('signal_ts'),
    }


def _compute_performance(trades: Iterable[dict[str, Any]], *, equity_start: float, max_points: int) -> dict[str, Any]:
    items = list(trades)
    settled = [item for item in items if bool(item.get('realized'))]
    settled.sort(key=lambda item: (_parse_utc(item.get('trade_at_utc')) or datetime.fromtimestamp(0, tz=UTC)))

    wins = [item for item in settled if item.get('resolved_status') == 'win']
    losses = [item for item in settled if item.get('resolved_status') == 'loss']
    refunds = [item for item in settled if item.get('resolved_status') == 'refund']
    cancelled = [item for item in settled if item.get('resolved_status') == 'cancelled']
    decisive = wins + losses

    gross_profit = sum(_safe_float(item.get('net_pnl')) for item in wins)
    gross_loss_abs = abs(sum(_safe_float(item.get('net_pnl')) for item in losses if _safe_float(item.get('net_pnl')) < 0.0))

    equity_value = float(equity_start)
    equity_curve: list[dict[str, Any]] = []
    running_peak = float(equity_start)
    max_drawdown_abs = 0.0
    max_drawdown_pct = 0.0
    returns: list[float] = []
    for item in settled:
        pnl = _safe_float(item.get('net_pnl'))
        equity_value += pnl
        running_peak = max(running_peak, equity_value)
        drawdown_abs = max(0.0, running_peak - equity_value)
        peak_base = running_peak if running_peak != 0 else 1.0
        drawdown_pct = drawdown_abs / peak_base
        max_drawdown_abs = max(max_drawdown_abs, drawdown_abs)
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
        amount = max(1e-9, _safe_float(item.get('amount') or 0.0, default=1.0))
        returns.append(pnl / amount)
        equity_curve.append(
            {
                'trade_at_utc': item.get('trade_at_utc'),
                'asset': item.get('asset'),
                'status': item.get('resolved_status'),
                'net_pnl': round(pnl, 6),
                'equity': round(equity_value, 6),
                'drawdown_abs': round(drawdown_abs, 6),
                'drawdown_pct': round(drawdown_pct, 6),
            }
        )

    if len(equity_curve) > max_points:
        step = max(1, len(equity_curve) // max_points)
        sampled = [equity_curve[idx] for idx in range(0, len(equity_curve), step)]
        if sampled[-1] != equity_curve[-1]:
            sampled.append(equity_curve[-1])
        equity_curve = sampled

    win_rate = (len(wins) / len(decisive)) if decisive else None
    avg_win = (gross_profit / len(wins)) if wins else None
    avg_loss_abs = (gross_loss_abs / len(losses)) if losses else None
    pnl_values = [_safe_float(item.get('net_pnl')) for item in settled]
    ev_brl = (sum(pnl_values) / len(pnl_values)) if pnl_values else None
    expectancy_r = None
    if settled:
        r_values = []
        for item in settled:
            amount = max(1e-9, _safe_float(item.get('amount') or 0.0, default=1.0))
            r_values.append(_safe_float(item.get('net_pnl')) / amount)
        expectancy_r = sum(r_values) / len(r_values)
    profit_factor = (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else None

    sharpe = None
    if len(returns) >= 2:
        avg_r = sum(returns) / len(returns)
        variance = sum((item - avg_r) ** 2 for item in returns) / max(1, len(returns) - 1)
        std_r = math.sqrt(max(0.0, variance))
        if std_r > 1e-12:
            sharpe = avg_r / std_r * math.sqrt(len(returns))

    return {
        'equity_start': round(float(equity_start), 6),
        'current_equity': round(float(equity_value), 6),
        'pnl_total': round(float(equity_value - equity_start), 6),
        'trade_count_total': len(items),
        'trade_count_realized': len(settled),
        'trade_count_decisive': len(decisive),
        'wins': len(wins),
        'losses': len(losses),
        'refunds': len(refunds),
        'cancelled': len(cancelled),
        'win_rate': round(float(win_rate), 6) if win_rate is not None else None,
        'avg_win_brl': round(float(avg_win), 6) if avg_win is not None else None,
        'avg_loss_brl_abs': round(float(avg_loss_abs), 6) if avg_loss_abs is not None else None,
        'profit_factor': round(float(profit_factor), 6) if profit_factor is not None else None,
        'ev_brl': round(float(ev_brl), 6) if ev_brl is not None else None,
        'expectancy_r': round(float(expectancy_r), 6) if expectancy_r is not None else None,
        'max_drawdown_brl': round(float(max_drawdown_abs), 6),
        'max_drawdown_pct': round(float(max_drawdown_pct), 6),
        'sharpe_per_trade': round(float(sharpe), 6) if sharpe is not None else None,
        'gross_profit_brl': round(float(gross_profit), 6),
        'gross_loss_brl_abs': round(float(gross_loss_abs), 6),
        'equity_curve': equity_curve,
    }


def _asset_metrics(trades: Iterable[dict[str, Any]], *, asset_board: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    board_by_asset = {str(item.get('asset') or ''): dict(item) for item in list(asset_board or []) if str(item.get('asset') or '')}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in trades:
        asset = str(item.get('asset') or '')
        if not asset:
            continue
        grouped.setdefault(asset, []).append(item)
    for asset in board_by_asset:
        grouped.setdefault(asset, [])

    rows: list[dict[str, Any]] = []
    for asset, items in grouped.items():
        settled = [item for item in items if bool(item.get('realized'))]
        wins = [item for item in settled if item.get('resolved_status') == 'win']
        losses = [item for item in settled if item.get('resolved_status') == 'loss']
        pnl_total = sum(_safe_float(item.get('net_pnl')) for item in settled)
        decisive = len(wins) + len(losses)
        win_rate = (len(wins) / decisive) if decisive else None
        latest = None
        if items:
            latest = max(items, key=lambda item: (_parse_utc(item.get('trade_at_utc')) or datetime.fromtimestamp(0, tz=UTC)))
        base = dict(board_by_asset.get(asset) or {})
        open_positions = int(base.get('open_positions') or 0)
        pending_unknown = int(base.get('pending_unknown') or 0)
        if open_positions > 0:
            status = 'open'
        elif pending_unknown > 0:
            status = 'pending'
        elif latest and latest.get('resolved_status') in {'win', 'loss'}:
            status = str(latest.get('resolved_status'))
        else:
            status = 'idle'
        rows.append(
            {
                'asset': asset,
                'scope_tag': base.get('scope_tag'),
                'interval_sec': base.get('interval_sec'),
                'status': status,
                'correlation_group': base.get('correlation_group'),
                'selected': base.get('selected'),
                'quota_kind': base.get('quota_kind'),
                'open_positions': open_positions,
                'pending_unknown': pending_unknown,
                'trade_count_realized': len(settled),
                'wins': len(wins),
                'losses': len(losses),
                'win_rate': round(float(win_rate), 6) if win_rate is not None else None,
                'pnl_total_brl': round(float(pnl_total), 6),
                'latest_status': latest.get('resolved_status') if latest else None,
                'latest_trade_at_utc': latest.get('trade_at_utc') if latest else None,
                'execution_stagger_delay_sec': base.get('execution_stagger_delay_sec'),
                'latest_action': base.get('latest_action'),
                'selected_reason': base.get('selected_reason'),
            }
        )
    rows.sort(key=lambda item: (-float(item.get('pnl_total_brl') or 0.0), str(item.get('asset') or '')))
    return rows


def _attempt_metrics(attempts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(attempts)
    acked = [item for item in items if str(item.get('transport_status') or '') == 'acked']
    latencies = [int(item.get('latency_ms')) for item in acked if item.get('latency_ms') not in (None, '')]
    errors = [item for item in items if str(item.get('transport_status') or '') not in {'acked', 'requested'}]
    return {
        'attempt_count': len(items),
        'acked_count': len(acked),
        'error_count': len(errors),
        'avg_latency_ms': round(sum(latencies) / len(latencies), 2) if latencies else None,
        'max_latency_ms': max(latencies) if latencies else None,
    }


def _event_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get('payload_json')
    if isinstance(payload, dict):
        return payload
    if payload in (None, ''):
        return {}
    try:
        parsed = json.loads(str(payload))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _alert_timestamp(item: dict[str, Any]) -> datetime:
    for key in ('at_utc', 'created_at_utc', 'checked_at_utc', 'requested_at_utc', 'trade_at_utc'):
        dt = _parse_utc(item.get(key))
        if dt is not None:
            return dt
    return datetime.fromtimestamp(0, tz=UTC)


def _build_alert_feed(repo_root: Path, *, limit: int, recent_events: list[dict[str, Any]], alerts_state: dict[str, Any] | None, incidents_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for row in list(recent_events or []):
        items.append(
            {
                'source': 'execution',
                'severity': 'warn' if 'reject' in str(row.get('event_type') or '') or 'timeout' in str(row.get('event_type') or '') else 'info',
                'message': str(row.get('event_type') or 'execution_event'),
                'asset': (_event_payload(row).get('intent') or {}).get('asset') if isinstance(_event_payload(row).get('intent'), dict) else None,
                'created_at_utc': row.get('created_at_utc'),
                'payload': _event_payload(row),
            }
        )

    for row in _tail_jsonl(repo_root / 'runs' / 'logs' / 'account_protection.jsonl', limit):
        items.append(
            {
                'source': 'protection',
                'severity': 'warn' if row.get('allowed') is False else 'info',
                'message': str(row.get('reason') or row.get('kind') or 'account_protection'),
                'asset': ((row.get('details') or {}).get('asset') if isinstance(row.get('details'), dict) else None),
                'created_at_utc': row.get('checked_at_utc') or row.get('created_at_utc'),
                'payload': row,
            }
        )

    telegram = dict((alerts_state or {}).get('telegram') or {}) if isinstance(alerts_state, dict) else {}
    for row in list(telegram.get('recent') or []):
        items.append(
            {
                'source': 'telegram',
                'severity': 'info',
                'message': str(row.get('kind') or row.get('message') or 'telegram_alert'),
                'asset': row.get('asset'),
                'created_at_utc': row.get('at_utc') or row.get('created_at_utc'),
                'payload': row,
            }
        )

    if isinstance(incidents_state, dict):
        for row in list(incidents_state.get('open_issues') or []):
            items.append(
                {
                    'source': 'incident',
                    'severity': str(row.get('severity') or 'warn'),
                    'message': str(row.get('message') or row.get('name') or 'open_issue'),
                    'asset': row.get('asset'),
                    'created_at_utc': row.get('at_utc') or row.get('created_at_utc'),
                    'payload': row,
                }
            )
        for row in list(incidents_state.get('recommended_actions') or []):
            items.append(
                {
                    'source': 'incident_action',
                    'severity': 'info',
                    'message': str(row),
                    'asset': None,
                    'created_at_utc': _utc_now_iso(),
                    'payload': {'action': row},
                }
            )

    items.sort(key=_alert_timestamp, reverse=True)
    return items[: int(limit)]


def _status_list(payload: dict[str, Any] | None, key: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    return [str(item) for item in list(payload.get(key) or []) if item not in (None, '')]


def _practice_profile_mismatch(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    exec_cfg = dict(payload.get('execution') or {}) if isinstance(payload, dict) else {}
    scope_cfg = dict(payload.get('controlled_scope') or {}) if isinstance(payload, dict) else {}
    reasons: list[str] = []
    mode = str(exec_cfg.get('mode') or 'disabled').lower()
    if not bool(exec_cfg.get('enabled')) or mode in {'disabled', 'off', 'none'}:
        reasons.append('execution disabled')
    if bool(scope_cfg.get('multi_asset_enabled')):
        reasons.append('multi-asset profile')
    assets = int(scope_cfg.get('assets_configured') or 0)
    if assets > 1:
        reasons.append(f'{assets} assets')
    topk = int(scope_cfg.get('portfolio_topk_total') or 0)
    if topk > 1:
        reasons.append(f'portfolio_topk_total={topk}')
    return bool(reasons), reasons


def build_control_display(control: dict[str, Any]) -> dict[str, dict[str, Any]]:
    wait_data_blockers = {'dataset_ready', 'market_context', 'effective_config_artifacts', 'control_freshness'}
    display: dict[str, dict[str, Any]] = {}
    for name, raw in dict(control or {}).items():
        payload = dict(raw or {}) if isinstance(raw, dict) else {}
        severity = str(payload.get('severity') or payload.get('status') or 'n/a').lower()
        label = str(payload.get('severity') or payload.get('status') or 'n/a').upper()
        tone = 'accent' if label == 'N/A' else _severity_tone(severity)
        reason = ''
        meta = ''
        blockers = _status_list(payload, 'blockers')
        warnings = _status_list(payload, 'warnings')

        if name == 'practice':
            mismatch, parts = _practice_profile_mismatch(payload)
            if mismatch:
                label = 'N/A'
                tone = 'accent'
                reason = 'Controlled practice não se aplica a este profile.'
                meta = ' · '.join(parts[:4])
            else:
                practice_doctor = dict(payload.get('doctor') or {}) if isinstance(payload, dict) else {}
                practice_blockers = _status_list(practice_doctor, 'blockers')
                practice_warnings = _status_list(practice_doctor, 'warnings')
                if practice_blockers:
                    reason = 'Blockers de practice ativos.'
                    meta = ', '.join(practice_blockers[:3])
                elif practice_warnings:
                    reason = 'Practice com avisos operacionais.'
                    meta = ', '.join(practice_warnings[:3])
                elif bool(payload.get('ready_for_practice')):
                    reason = 'Scope pronto para controlled practice.'
                    meta = 'ready_for_practice=true'
        elif name == 'doctor':
            if blockers and set(blockers).issubset(wait_data_blockers):
                label = 'WAIT DATA'
                tone = 'warn'
                reason = 'Faltam artefatos frescos do scope antes do doctor ficar verde.'
                meta = ', '.join(blockers[:3])
            elif blockers:
                reason = 'Doctor encontrou blockers ativos.'
                meta = ', '.join(blockers[:3])
            elif warnings:
                reason = 'Doctor com avisos operacionais.'
                meta = ', '.join(warnings[:3])
            elif bool(payload.get('ready_for_practice')):
                reason = 'Doctor liberou controlled practice.'
                meta = 'ready_for_practice=true'
        elif name == 'release':
            if blockers:
                reason = 'Release checklist com blockers.'
                meta = ', '.join(blockers[:3])
            elif warnings:
                reason = 'Release checklist com avisos.'
                meta = ', '.join(warnings[:3])
            elif bool(payload.get('ready_for_practice')) or bool(payload.get('ready_for_release')):
                reason = 'Release checklist sem blockers.'
        elif name == 'security':
            if bool(payload.get('blocked')):
                reason = 'Security posture bloqueia operação.'
                meta = ', '.join(_status_list(payload, 'blockers')[:3]) or str(payload.get('credential_source') or 'blocked')
            else:
                reason = 'Security posture OK.'
                meta = str(payload.get('credential_source') or 'ok')
        elif name == 'health':
            health_checks = list(payload.get('checks') or []) if isinstance(payload, dict) else []
            failed = [str(item.get('name')) for item in health_checks if str(item.get('status')) == 'error']
            warns = [str(item.get('name')) for item in health_checks if str(item.get('status')) == 'warn']
            if failed:
                reason = 'Health com blockers.'
                meta = ', '.join(failed[:3])
            elif warns:
                reason = 'Health com avisos.'
                meta = ', '.join(warns[:3])
            else:
                reason = 'Health OK.'

        display[name] = {
            'label': label,
            'tone': tone,
            'meta': meta or f'raw={str(payload.get("severity") or payload.get("status") or "n/a")}',
            'reason': reason or 'Sem contexto adicional.',
            'raw_severity': str(payload.get('severity') or payload.get('status') or 'n/a').upper(),
            'ok': payload.get('ok'),
            'blockers': blockers,
            'warnings': warnings,
        }
    return display


def _safe_payload(fn, *, repo_root: Path, config_path: Path) -> dict[str, Any]:
    try:
        payload = fn(repo_root=repo_root, config_path=config_path)
        return dict(payload) if isinstance(payload, dict) else {'ok': False, 'error': 'invalid_payload'}
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}:{exc}'}


def build_dashboard_snapshot(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    equity_start: float | None = None,
    max_alerts: int | None = None,
    max_equity_points: int | None = None,
    trade_limit: int = 2000,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    cfg_path = Path(config_path).expanduser().resolve() if config_path and Path(config_path).is_absolute() else (root / str(config_path or 'config/multi_asset.yaml')).resolve()
    cfg = load_thalor_config(repo_root=root, config_path=cfg_path)
    dashboard_cfg = cfg.dashboard
    effective_equity_start = float(equity_start if equity_start is not None else dashboard_cfg.default_equity_start)
    effective_alerts = int(max_alerts if max_alerts is not None else dashboard_cfg.max_alerts)
    effective_points = int(max_equity_points if max_equity_points is not None else dashboard_cfg.max_equity_points)

    control = {
        'health': _safe_payload(health_payload, repo_root=root, config_path=cfg_path),
        'security': _safe_payload(security_payload, repo_root=root, config_path=cfg_path),
        'release': _safe_payload(release_payload, repo_root=root, config_path=cfg_path),
        'practice': _safe_payload(practice_payload, repo_root=root, config_path=cfg_path),
        'doctor': _safe_payload(doctor_payload, repo_root=root, config_path=cfg_path),
        'portfolio': _safe_payload(portfolio_status_payload, repo_root=root, config_path=cfg_path),
        'alerts': _safe_payload(alerts_payload, repo_root=root, config_path=cfg_path),
        'incidents': _safe_payload(incidents_payload, repo_root=root, config_path=cfg_path),
    }

    control_display = build_control_display(control)

    execution_data = _execution_rows(root, trade_limit=trade_limit, attempt_limit=500, event_limit=500)
    trades = [_normalize_trade(row) for row in execution_data['broker_orders']]
    performance = _compute_performance(trades, equity_start=effective_equity_start, max_points=effective_points)
    asset_board = list((control['portfolio'].get('asset_board') or []) if isinstance(control['portfolio'], dict) else [])
    asset_status = _asset_metrics(trades, asset_board=asset_board)
    attempt_metrics = _attempt_metrics(execution_data['attempts'])
    alerts_feed = _build_alert_feed(
        root,
        limit=effective_alerts,
        recent_events=execution_data['events'],
        alerts_state=control['alerts'],
        incidents_state=control['incidents'],
    )

    return {
        'generated_at_utc': _utc_now_iso(),
        'repo_root': str(root),
        'config_path': str(cfg_path),
        'profile': str(cfg.runtime.profile),
        'dashboard': {
            'title': str(dashboard_cfg.title),
            'theme': str(dashboard_cfg.theme),
            'report_output_dir': str((root / dashboard_cfg.report.output_dir).resolve() if not dashboard_cfg.report.output_dir.is_absolute() else dashboard_cfg.report.output_dir.resolve()),
            'export_json': bool(dashboard_cfg.report.export_json),
        },
        'control': control,
        'control_display': control_display,
        'performance': performance,
        'asset_status': asset_status,
        'attempt_metrics': attempt_metrics,
        'alerts_feed': alerts_feed,
        'recent_trades': sorted(trades, key=lambda item: (_parse_utc(item.get('trade_at_utc')) or datetime.fromtimestamp(0, tz=UTC)), reverse=True)[:200],
        'recent_attempts': execution_data['attempts'][:200],
        'recent_events': [
            {
                'event_id': row.get('event_id'),
                'event_type': row.get('event_type'),
                'created_at_utc': row.get('created_at_utc'),
                'payload': _event_payload(row),
            }
            for row in execution_data['events'][:200]
        ],
    }


__all__ = ['build_dashboard_snapshot', 'build_control_display']
