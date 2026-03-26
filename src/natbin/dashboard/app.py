from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

if __package__ in {None, ''}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from natbin.dashboard.analytics import build_dashboard_snapshot
from natbin.dashboard.report import export_dashboard_report
from natbin.dashboard.style import DASHBOARD_CSS


class DashArgs(argparse.Namespace):
    repo_root: str
    config: str
    refresh_sec: float
    max_events: int
    max_signals: int
    equity_start: float | None
    max_alerts: int | None
    report_dir: str | None


def _parse_dash_args(argv: list[str]) -> DashArgs:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument('--repo-root', default='.')
    p.add_argument('--config', default='config/multi_asset.yaml')
    p.add_argument('--refresh-sec', type=float, default=3.0)
    p.add_argument('--max-events', type=int, default=200)
    p.add_argument('--max-signals', type=int, default=200)
    p.add_argument('--equity-start', type=float, default=None)
    p.add_argument('--max-alerts', type=int, default=None)
    p.add_argument('--report-dir', default=None)
    ns, _unknown = p.parse_known_args(argv, namespace=DashArgs())
    ns.refresh_sec = max(0.0, float(getattr(ns, 'refresh_sec', 3.0) or 0.0))
    ns.max_events = max(10, int(getattr(ns, 'max_events', 200) or 200))
    ns.max_signals = max(10, int(getattr(ns, 'max_signals', 200) or 200))
    return ns


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _sqlite_connect_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    uri = f'file:{db_path.as_posix()}?mode=ro'
    con = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=1.0)
    con.row_factory = sqlite3.Row
    return con


def _sqlite_table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    cur = con.execute(f'PRAGMA table_info("{table}")')
    return [str(row[1]) for row in cur.fetchall()]


def _sqlite_fetch_recent(con: sqlite3.Connection, table: str, *, limit: int = 200) -> list[dict[str, Any]]:
    cols = _sqlite_table_columns(con, table)
    if not cols:
        return []
    order_col = None
    for cand in ('ts', 'observed_at_utc', 'created_at_utc', 'id'):
        if cand in cols:
            order_col = cand
            break
    if order_col:
        query = f'SELECT * FROM "{table}" ORDER BY "{order_col}" DESC LIMIT ?'
        rows = con.execute(query, (int(limit),)).fetchall()
    else:
        query = f'SELECT * FROM "{table}" LIMIT ?'
        rows = con.execute(query, (int(limit),)).fetchall()
    return [{cols[idx]: row[idx] for idx in range(min(len(cols), len(row)))} for row in rows]


def _severity_tone(value: Any) -> str:
    sev = str(value or 'info').strip().lower()
    if sev in {'ok', 'ready', 'healthy', 'open', 'accepted'}:
        return 'ok'
    if sev in {'warn', 'warning', 'pending', 'cooldown'}:
        return 'warn'
    if sev in {'error', 'critical', 'blocked', 'rejected', 'loss'}:
        return 'danger'
    return 'accent'


def _fmt_number(value: Any, digits: int = 2) -> str:
    if value in (None, ''):
        return '—'
    try:
        return f'{float(value):,.{digits}f}'
    except Exception:
        return str(value)


def _fmt_pct(value: Any, digits: int = 2) -> str:
    if value in (None, ''):
        return '—'
    try:
        return f'{100.0 * float(value):.{digits}f}%'
    except Exception:
        return str(value)


def _card_html(label: str, value: str, meta: str = '', tone: str = 'accent') -> str:
    safe_label = str(label)
    safe_value = str(value)
    safe_meta = str(meta or '')
    return (
        f'<div class="thalor-card {tone}">'
        f'<div class="label">{safe_label}</div>'
        f'<div class="value">{safe_value}</div>'
        f'<div class="meta">{safe_meta}</div>'
        '</div>'
    )


def _badge_html(text: str, tone: str = 'accent') -> str:
    classes = 'thalor-pill'
    if tone == 'ok':
        classes += ' thalor-badge-ok'
    elif tone == 'warn':
        classes += ' thalor-badge-warn'
    elif tone == 'danger':
        classes += ' thalor-badge-danger'
    return f'<span class="{classes}">{text}</span>'


def _normalize_jsonish(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalize_jsonish(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_jsonish(item) for item in value]
    return str(value)


def _normalize_table_cell(value: Any) -> Any:
    normalized = _normalize_jsonish(value)
    if isinstance(normalized, dict):
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    if isinstance(normalized, list):
        return json.dumps(normalized, ensure_ascii=False)
    return normalized


def _normalize_rows_for_dataframe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized_rows.append({str(key): _normalize_table_cell(value) for key, value in dict(row).items()})
    return normalized_rows


def _render_dataframe(st, pd, rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.info('Nenhum dado disponível.')
        return
    if pd is not None:
        st.dataframe(pd.DataFrame(_normalize_rows_for_dataframe(rows)), width='stretch', hide_index=True)
    else:
        st.json(rows, expanded=False)


def run() -> None:
    import streamlit as st
    import streamlit.components.v1 as components

    try:
        import pandas as pd
    except Exception:  # pragma: no cover - streamlit env should have pandas
        pd = None  # type: ignore

    from natbin.config import load_thalor_config

    args = _parse_dash_args(sys.argv[1:])

    repo = Path(args.repo_root).expanduser().resolve()
    cfg = (repo / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config).resolve()
    cfg_obj = load_thalor_config(repo_root=repo, config_path=cfg)
    dash_cfg = cfg_obj.dashboard

    st.set_page_config(page_title='Thalor Dashboard Pro', layout='wide')
    st.markdown(f'<style>{DASHBOARD_CSS}</style>', unsafe_allow_html=True)

    default_refresh = float(args.refresh_sec if args.refresh_sec is not None else dash_cfg.default_refresh_sec)
    default_equity_start = float(args.equity_start if args.equity_start is not None else dash_cfg.default_equity_start)
    default_max_alerts = int(args.max_alerts if args.max_alerts is not None else dash_cfg.max_alerts)
    default_report_dir = str(args.report_dir or dash_cfg.report.output_dir)

    st.sidebar.header('Thalor // Control deck')
    repo_root = st.sidebar.text_input('repo_root', value=str(repo))
    config_path = st.sidebar.text_input('config', value=str(cfg))
    refresh_sec = st.sidebar.number_input('auto-refresh (segundos)', min_value=0.0, max_value=120.0, value=float(default_refresh), step=1.0)
    auto_refresh = st.sidebar.checkbox('auto refresh', value=(refresh_sec > 0.0))
    equity_start = st.sidebar.number_input('equity start', min_value=0.0, value=float(default_equity_start), step=100.0)
    max_alerts = int(st.sidebar.number_input('max alerts', min_value=10, max_value=500, value=int(default_max_alerts), step=10))
    max_signals = int(st.sidebar.number_input('max sinais', min_value=10, max_value=5000, value=int(args.max_signals), step=10))
    report_dir = st.sidebar.text_input('report dir', value=str(default_report_dir))
    _ = st.sidebar.button('Refresh agora')

    repo = Path(repo_root).expanduser().resolve()
    cfg = Path(config_path).expanduser().resolve() if Path(config_path).is_absolute() else (repo / config_path).resolve()

    if auto_refresh and refresh_sec > 0.0:
        ms = int(float(refresh_sec) * 1000)
        components.html(f'<script>setTimeout(function(){{window.location.reload();}}, {ms});</script>', height=0)

    snapshot = build_dashboard_snapshot(
        repo_root=repo,
        config_path=cfg,
        equity_start=float(equity_start),
        max_alerts=int(max_alerts),
        max_equity_points=int(getattr(dash_cfg, 'max_equity_points', 500) or 500),
        trade_limit=max(200, int(max_signals) * 5),
    )

    perf = dict(snapshot.get('performance') or {})
    control = dict(snapshot.get('control') or {})
    control_display = dict(snapshot.get('control_display') or {})
    asset_status = list(snapshot.get('asset_status') or [])
    alerts_feed = list(snapshot.get('alerts_feed') or [])
    recent_trades = list(snapshot.get('recent_trades') or [])
    recent_attempts = list(snapshot.get('recent_attempts') or [])
    recent_events = list(snapshot.get('recent_events') or [])
    portfolio = dict(control.get('portfolio') or {})

    hero_html = f"""
    <div class=\"thalor-hero\">
      <div class=\"eyebrow\">CYBER DRAGON CONTROL DECK</div>
      <h1>{snapshot.get('dashboard', {}).get('title', 'Thalor')} — Professional Dashboard</h1>
      <div class=\"subtitle\">Repo: <code>{repo}</code> · Config: <code>{cfg}</code> · Generated at {snapshot.get('generated_at_utc')}</div>
    </div>
    """
    st.markdown(hero_html, unsafe_allow_html=True)

    open_positions_total = sum(int(item.get('open_positions') or 0) for item in asset_status)
    pending_unknown_total = sum(int(item.get('pending_unknown') or 0) for item in asset_status)

    card_cols = st.columns(6)
    cards = [
        ('Current equity', _fmt_number(perf.get('current_equity')), f"PnL total {_fmt_number(perf.get('pnl_total'))}", 'accent'),
        ('Win-rate', _fmt_pct(perf.get('win_rate')), f"Wins {perf.get('wins', 0)} · Losses {perf.get('losses', 0)}", 'ok' if (perf.get('win_rate') or 0) >= 0.5 else 'warn'),
        ('EV / trade', _fmt_number(perf.get('ev_brl')), f"Expectancy R {_fmt_number(perf.get('expectancy_r'), 3)}", 'accent'),
        ('Drawdown', _fmt_pct(perf.get('max_drawdown_pct')), f"{_fmt_number(perf.get('max_drawdown_brl'))} BRL", 'danger' if (perf.get('max_drawdown_pct') or 0) > 0.15 else 'warn'),
        ('Sharpe', _fmt_number(perf.get('sharpe_per_trade'), 3), f"Profit factor {_fmt_number(perf.get('profit_factor'), 3)}", 'ok' if (perf.get('sharpe_per_trade') or 0) >= 1.0 else 'accent'),
        ('Exposure', f'{open_positions_total} open / {pending_unknown_total} pending', f"{len(asset_status)} assets monitored", 'accent'),
    ]
    for col, (label, value, meta, tone) in zip(card_cols, cards):
        with col:
            st.markdown(_card_html(label, value, meta, tone=tone), unsafe_allow_html=True)

    tabs = st.tabs(['Cockpit', 'Assets', 'Orders', 'Operations', 'Signals / Raw'])

    with tabs[0]:
        left, right = st.columns([2.1, 1.1])
        with left:
            st.markdown('<div class="thalor-section-title"><h3>Equity curve</h3></div>', unsafe_allow_html=True)
            equity_curve = list(perf.get('equity_curve') or [])
            if equity_curve and pd is not None:
                df = pd.DataFrame(equity_curve)
                if 'trade_at_utc' in df.columns:
                    df['trade_at_utc'] = pd.to_datetime(df['trade_at_utc'], utc=True, errors='coerce')
                    df = df.sort_values('trade_at_utc')
                    df = df.set_index('trade_at_utc')
                st.line_chart(df[['equity']], width='stretch')
                if 'drawdown_pct' in df.columns:
                    st.area_chart(df[['drawdown_pct']], width='stretch')
            elif equity_curve:
                st.json(equity_curve[-20:], expanded=False)
            else:
                st.info('Ainda não há trades realizados para compor a equity curve.')

            st.markdown('<div class="thalor-section-title"><h3>Asset PnL</h3></div>', unsafe_allow_html=True)
            if asset_status and pd is not None:
                asset_df = pd.DataFrame(asset_status)
                chart_df = asset_df[['asset', 'pnl_total_brl']].set_index('asset')
                st.bar_chart(chart_df, width='stretch')
            else:
                st.info('Sem PnL por asset ainda.')

        with right:
            control_cards = st.columns(2)
            control_status = [
                ('Release', control_display.get('release') or {}),
                ('Practice', control_display.get('practice') or {}),
                ('Doctor', control_display.get('doctor') or {}),
                ('Security', control_display.get('security') or {}),
            ]
            for idx, (label, item) in enumerate(control_status):
                with control_cards[idx % 2]:
                    st.markdown(
                        _card_html(
                            label,
                            str(item.get('label') or 'N/A').upper(),
                            str(item.get('meta') or 'sem contexto'),
                            tone=str(item.get('tone') or 'accent'),
                        ),
                        unsafe_allow_html=True,
                    )

            st.markdown('<div class="thalor-section-title"><h3>Recent alerts</h3></div>', unsafe_allow_html=True)
            _render_dataframe(st, pd, alerts_feed[:20])

            if st.button('Export dashboard report'):
                output_dir = Path(report_dir).expanduser()
                paths = export_dashboard_report(
                    snapshot,
                    output_dir=(output_dir if output_dir.is_absolute() else (repo / output_dir)),
                    title=f"{snapshot.get('dashboard', {}).get('title', 'Thalor')} Dashboard Report",
                    export_json=True,
                )
                st.success(f"Report exported: {paths.get('html_path')}")
                st.json(paths, expanded=False)

    with tabs[1]:
        st.markdown('<div class="thalor-section-title"><h3>Unified asset status</h3></div>', unsafe_allow_html=True)
        _render_dataframe(st, pd, asset_status)
        st.markdown('<div class="thalor-section-title"><h3>Portfolio asset board</h3></div>', unsafe_allow_html=True)
        _render_dataframe(st, pd, list(portfolio.get('asset_board') or []))
        latest_cycle = portfolio.get('latest_cycle') if isinstance(portfolio, dict) else None
        latest_alloc = portfolio.get('latest_allocation') if isinstance(portfolio, dict) else None
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown('<div class="thalor-section-title"><h3>Latest cycle</h3></div>', unsafe_allow_html=True)
            if latest_cycle:
                st.json({
                    'cycle_id': latest_cycle.get('cycle_id'),
                    'started_at_utc': latest_cycle.get('started_at_utc'),
                    'finished_at_utc': latest_cycle.get('finished_at_utc'),
                    'ok': latest_cycle.get('ok'),
                    'execution_plan': latest_cycle.get('execution_plan'),
                }, expanded=False)
            else:
                st.info('Nenhum ciclo portfolio encontrado.')
        with col_b:
            st.markdown('<div class="thalor-section-title"><h3>Latest allocation</h3></div>', unsafe_allow_html=True)
            if latest_alloc:
                st.json({
                    'allocation_id': latest_alloc.get('allocation_id'),
                    'at_utc': latest_alloc.get('at_utc'),
                    'selected': latest_alloc.get('selected'),
                    'suppressed': latest_alloc.get('suppressed'),
                }, expanded=False)
            else:
                st.info('Nenhuma alocação encontrada.')

    with tabs[2]:
        top_a, top_b = st.columns(2)
        with top_a:
            st.markdown('<div class="thalor-section-title"><h3>Recent trades</h3></div>', unsafe_allow_html=True)
            _render_dataframe(st, pd, recent_trades[:200])
        with top_b:
            st.markdown('<div class="thalor-section-title"><h3>Submit attempts</h3></div>', unsafe_allow_html=True)
            _render_dataframe(st, pd, recent_attempts[:200])
        st.markdown('<div class="thalor-section-title"><h3>Execution events</h3></div>', unsafe_allow_html=True)
        _render_dataframe(st, pd, recent_events[:200])

    with tabs[3]:
        op_cols = st.columns(5)
        summaries = [
            ('Health', control_display.get('health') or {}, control.get('health') or {}),
            ('Security', control_display.get('security') or {}, control.get('security') or {}),
            ('Release', control_display.get('release') or {}, control.get('release') or {}),
            ('Practice', control_display.get('practice') or {}, control.get('practice') or {}),
            ('Doctor', control_display.get('doctor') or {}, control.get('doctor') or {}),
        ]
        for col, (label, display_item, raw_payload) in zip(op_cols, summaries):
            with col:
                st.markdown(
                    _card_html(
                        label,
                        str(display_item.get('label') or raw_payload.get('severity') or raw_payload.get('status') or 'n/a').upper(),
                        str(display_item.get('meta') or f"ok={raw_payload.get('ok')}"),
                        tone=str(display_item.get('tone') or _severity_tone(raw_payload.get('severity') or raw_payload.get('status'))),
                    ),
                    unsafe_allow_html=True,
                )
        st.markdown('<div class="thalor-section-title"><h3>Why these statuses?</h3></div>', unsafe_allow_html=True)
        for label, key in [('Release', 'release'), ('Practice', 'practice'), ('Doctor', 'doctor')]:
            item = dict(control_display.get(key) or {})
            raw_payload = dict(control.get(key) or {})
            with st.expander(f'{label}: {str(item.get("label") or raw_payload.get("severity") or "n/a").upper()}'):
                st.markdown(f'**Resumo:** {item.get("reason") or "Sem contexto adicional."}')
                if item.get('meta'):
                    st.markdown(f'**Detalhe:** `{item.get("meta")}`')
                blockers = list(raw_payload.get('blockers') or [])
                warnings = list(raw_payload.get('warnings') or [])
                if blockers:
                    st.markdown(f'**Blockers:** `{", ".join(str(v) for v in blockers[:8])}`')
                if warnings:
                    st.markdown(f'**Warnings:** `{", ".join(str(v) for v in warnings[:8])}`')
                checks = list(raw_payload.get('checks') or [])
                if checks:
                    failed = [str(it.get('name')) for it in checks if str(it.get('status')) == 'error']
                    warned = [str(it.get('name')) for it in checks if str(it.get('status')) == 'warn']
                    if failed:
                        st.markdown(f'**Checks com erro:** `{", ".join(failed[:8])}`')
                    if warned:
                        st.markdown(f'**Checks com aviso:** `{", ".join(warned[:8])}`')
        with st.expander('Control plane payloads'):
            st.json(control, expanded=False)

    with tabs[4]:
        scopes = list(portfolio.get('scopes') or []) if isinstance(portfolio, dict) else []
        scope_tags = [str((item.get('scope') or {}).get('scope_tag') or '') for item in scopes if str((item.get('scope') or {}).get('scope_tag') or '')]
        chosen_tag = st.selectbox('scope_tag', options=scope_tags if scope_tags else ['(none)'])
        if chosen_tag and chosen_tag != '(none)':
            scope_info = next((item for item in scopes if str((item.get('scope') or {}).get('scope_tag') or '') == chosen_tag), None)
            runtime_paths = dict((scope_info or {}).get('runtime_paths') or {})
            decision_path = repo / 'runs' / 'decisions' / f'decision_latest_{chosen_tag}.json'
            st.markdown('<div class="thalor-section-title"><h3>Latest decision</h3></div>', unsafe_allow_html=True)
            decision = _read_json(decision_path)
            if decision is not None:
                st.json(decision, expanded=False)
            else:
                st.info(f'No decision snapshot found at {decision_path}')

            signals_db = runtime_paths.get('signals_db_path')
            if signals_db:
                con = _sqlite_connect_ro(Path(str(signals_db)))
                if con is not None:
                    try:
                        st.markdown('<div class="thalor-section-title"><h3>Recent signals</h3></div>', unsafe_allow_html=True)
                        _render_dataframe(st, pd, _sqlite_fetch_recent(con, 'signals', limit=max_signals))
                    except Exception as exc:
                        st.warning(f'Não foi possível ler signals DB: {exc}')
                    finally:
                        con.close()
        with st.expander('Raw dashboard snapshot'):
            st.json(snapshot, expanded=False)


if __name__ == '__main__':
    run()
