from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ''}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from natbin.dashboard.analytics import build_dashboard_snapshot


def _fmt_number(value: Any, *, digits: int = 2) -> str:
    if value in (None, ''):
        return '—'
    try:
        return f'{float(value):,.{digits}f}'
    except Exception:
        return str(value)


def _fmt_pct(value: Any, *, digits: int = 2) -> str:
    if value in (None, ''):
        return '—'
    try:
        return f'{100.0 * float(value):.{digits}f}%'
    except Exception:
        return str(value)


def _fmt_text(value: Any) -> str:
    if value in (None, ''):
        return '—'
    return html.escape(str(value))


def _html_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return '<p class="muted">Nenhum dado disponível.</p>'
    head = ''.join(f'<th>{html.escape(label)}</th>' for _key, label in columns)
    body_rows: list[str] = []
    for row in rows:
        cells: list[str] = []
        for key, _label in columns:
            value = row.get(key)
            if key.endswith('_pct') or key == 'win_rate':
                cell = _fmt_pct(value)
            elif key.endswith('_brl') or key in {'ev_brl', 'current_equity', 'pnl_total'}:
                cell = _fmt_number(value)
            else:
                cell = _fmt_text(value)
            cells.append(f'<td>{cell}</td>')
        body_rows.append('<tr>' + ''.join(cells) + '</tr>')
    return '<table><thead><tr>' + head + '</tr></thead><tbody>' + ''.join(body_rows) + '</tbody></table>'


def export_dashboard_report(
    snapshot: dict[str, Any],
    *,
    output_dir: str | Path,
    title: str | None = None,
    export_json: bool = True,
) -> dict[str, str]:
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')
    base = f'dashboard_report_{ts}'
    html_path = out_dir / f'{base}.html'
    json_path = out_dir / f'{base}.json'

    dashboard_info = dict(snapshot.get('dashboard') or {})
    perf = dict(snapshot.get('performance') or {})
    asset_status = list(snapshot.get('asset_status') or [])
    alerts_feed = list(snapshot.get('alerts_feed') or [])[:20]

    report_title = str(title or dashboard_info.get('title') or 'Thalor Dashboard Report')
    hero = f"""
    <section class=\"hero\">
      <div class=\"eyebrow\">THALOR // PROFESSIONAL DASHBOARD</div>
      <h1>{html.escape(report_title)}</h1>
      <p class=\"muted\">Gerado em {_fmt_text(snapshot.get('generated_at_utc'))} · Perfil {_fmt_text(snapshot.get('profile'))}</p>
    </section>
    """

    kpis = [
        ('Current equity', _fmt_number(perf.get('current_equity'))),
        ('PnL total', _fmt_number(perf.get('pnl_total'))),
        ('Win-rate', _fmt_pct(perf.get('win_rate'))),
        ('EV / trade', _fmt_number(perf.get('ev_brl'))),
        ('Drawdown', _fmt_pct(perf.get('max_drawdown_pct'))),
        ('Sharpe', _fmt_number(perf.get('sharpe_per_trade'))),
    ]
    cards = ''.join(
        f'<div class="card"><div class="label">{html.escape(label)}</div><div class="value">{html.escape(value)}</div></div>'
        for label, value in kpis
    )

    equity_rows = list(perf.get('equity_curve') or [])[-20:]
    asset_columns = [
        ('asset', 'Asset'),
        ('status', 'Status'),
        ('trade_count_realized', 'Realized'),
        ('win_rate', 'Win-rate'),
        ('pnl_total_brl', 'PnL BRL'),
        ('latest_status', 'Latest'),
        ('latest_trade_at_utc', 'Last trade'),
    ]
    alert_columns = [
        ('source', 'Source'),
        ('severity', 'Severity'),
        ('message', 'Message'),
        ('asset', 'Asset'),
        ('created_at_utc', 'At UTC'),
    ]
    equity_columns = [
        ('trade_at_utc', 'Trade UTC'),
        ('asset', 'Asset'),
        ('status', 'Status'),
        ('net_pnl', 'PnL'),
        ('equity', 'Equity'),
        ('drawdown_pct', 'Drawdown'),
    ]

    html_payload = f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(report_title)}</title>
  <style>
    body {{
      background: linear-gradient(180deg, #06101a, #02060c 80%);
      color: #e7f2ff;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      margin: 0;
      padding: 24px;
    }}
    .hero {{
      border: 1px solid rgba(93, 228, 255, 0.18);
      border-radius: 20px;
      padding: 20px 24px;
      background: linear-gradient(135deg, rgba(11, 24, 39, 0.95), rgba(6, 11, 20, 0.95));
      box-shadow: 0 16px 34px rgba(0,0,0,.28);
    }}
    .eyebrow {{ color: #5de4ff; letter-spacing: .25em; font-size: 12px; font-weight: 700; margin-bottom: 6px; }}
    .muted {{ color: #98abc3; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-top: 18px; }}
    .card {{ border: 1px solid rgba(93, 228, 255, 0.16); border-radius: 16px; padding: 14px; background: rgba(10, 20, 34, 0.88); }}
    .label {{ color: #98abc3; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; margin-bottom: 8px; }}
    .value {{ font-size: 28px; font-weight: 700; }}
    h2 {{ margin-top: 30px; font-size: 20px; }}
    table {{ width: 100%; border-collapse: collapse; border-radius: 14px; overflow: hidden; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid rgba(93, 228, 255, 0.12); text-align: left; font-size: 14px; }}
    th {{ background: rgba(11, 24, 39, 0.95); color: #b8c8db; }}
    tbody tr:nth-child(odd) {{ background: rgba(8, 16, 28, 0.64); }}
    tbody tr:nth-child(even) {{ background: rgba(6, 12, 22, 0.92); }}
  </style>
</head>
<body>
  {hero}
  <div class=\"cards\">{cards}</div>
  <h2>Asset status</h2>
  {_html_table(asset_status, asset_columns)}
  <h2>Recent alerts</h2>
  {_html_table(alerts_feed, alert_columns)}
  <h2>Recent equity points</h2>
  {_html_table(equity_rows, equity_columns)}
</body>
</html>
    """
    html_path.write_text(html_payload, encoding='utf-8')

    paths = {'html_path': str(html_path)}
    if export_json:
        json_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding='utf-8')
        paths['json_path'] = str(json_path)
    return paths


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog='python -m natbin.dashboard.report', description='Export a professional dashboard snapshot report.')
    p.add_argument('--repo-root', default='.')
    p.add_argument('--config', default='config/multi_asset.yaml')
    p.add_argument('--output-dir', default='runs/reports/dashboard')
    p.add_argument('--equity-start', type=float, default=None)
    p.add_argument('--max-alerts', type=int, default=None)
    p.add_argument('--max-equity-points', type=int, default=None)
    p.add_argument('--title', default=None)
    p.add_argument('--json', action='store_true')
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import sys

    ns = _parse_args(list(sys.argv[1:] if argv is None else argv))
    snapshot = build_dashboard_snapshot(
        repo_root=ns.repo_root,
        config_path=ns.config,
        equity_start=ns.equity_start,
        max_alerts=ns.max_alerts,
        max_equity_points=ns.max_equity_points,
    )
    paths = export_dashboard_report(
        snapshot,
        output_dir=ns.output_dir,
        title=ns.title,
        export_json=True,
    )
    payload = {'ok': True, **paths}
    if ns.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for key, value in payload.items():
            print(f'{key}: {value}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
