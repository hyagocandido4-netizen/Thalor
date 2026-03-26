from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ''}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from natbin.monte_carlo.engine import build_monte_carlo_payload


def _require_plotting_deps() -> tuple[Any, Any, Any, Any]:
    try:
        import matplotlib

        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak
    except Exception as exc:  # pragma: no cover - operator env guard
        raise RuntimeError(
            'Monte Carlo reporting requires reportlab and matplotlib. '
            'Reinstall project dependencies with `pip install -r requirements.txt`.'
        ) from exc
    return plt, np, (colors, A4, getSampleStyleSheet, ParagraphStyle, cm), (Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak)


def _fmt_number(value: Any, *, digits: int = 2) -> str:
    if value in (None, ''):
        return '—'
    try:
        return f'{float(value):,.{digits}f}'
    except Exception:
        return str(value)


def _fmt_pct(value: Any, *, digits: int = 1) -> str:
    if value in (None, ''):
        return '—'
    try:
        return f'{100.0 * float(value):.{digits}f}%'
    except Exception:
        return str(value)


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _chart_colors(name: str) -> tuple[str, str, str]:
    key = str(name or '').strip().lower()
    mapping = {
        'conservative': ('#5de4ff', '#1d8faf', '#0b3f52'),
        'medium': ('#8dffa4', '#34b36b', '#17452b'),
        'aggressive': ('#ffb35d', '#d67b2d', '#5a3210'),
    }
    return mapping.get(key, ('#5de4ff', '#1d8faf', '#0b3f52'))


def _fan_chart_png(scenario: dict[str, Any]) -> bytes:
    plt, np, _rl, _platypus = _require_plotting_deps()
    fan = list(scenario.get('fan_points') or [])
    if not fan:
        raise RuntimeError('scenario fan_points missing')
    x = np.asarray([row['day'] for row in fan], dtype=float)
    p05 = np.asarray([row['p05'] for row in fan], dtype=float)
    p25 = np.asarray([row['p25'] for row in fan], dtype=float)
    p50 = np.asarray([row['p50'] for row in fan], dtype=float)
    p75 = np.asarray([row['p75'] for row in fan], dtype=float)
    p95 = np.asarray([row['p95'] for row in fan], dtype=float)
    accent, line_mid, line_dark = _chart_colors(str(scenario.get('name') or ''))

    fig = plt.figure(figsize=(7.2, 3.2), dpi=160)
    ax = fig.add_subplot(111)
    fig.patch.set_facecolor('#08131f')
    ax.set_facecolor('#08131f')
    ax.fill_between(x, p05, p95, color=line_dark, alpha=0.25)
    ax.fill_between(x, p25, p75, color=line_mid, alpha=0.30)
    ax.plot(x, p50, color=accent, linewidth=2.0)
    ax.set_title(f"{scenario.get('label', scenario.get('name', 'Scenario'))} — Equity fan chart", color='#e7f2ff', fontsize=12)
    ax.set_xlabel('Dias', color='#9fb5cc')
    ax.set_ylabel('Equity (BRL)', color='#9fb5cc')
    ax.tick_params(colors='#9fb5cc', labelsize=8)
    for spine in ax.spines.values():
        spine.set_color('#1b3551')
    ax.grid(True, color='#143149', alpha=0.35, linewidth=0.6)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    return buf.getvalue()


def _hist_chart_png(scenario: dict[str, Any]) -> bytes:
    plt, np, _rl, _platypus = _require_plotting_deps()
    rows = list(scenario.get('ending_histogram') or [])
    if not rows:
        raise RuntimeError('scenario ending_histogram missing')
    centers = np.asarray([(float(row['left']) + float(row['right'])) / 2.0 for row in rows], dtype=float)
    counts = np.asarray([int(row['count']) for row in rows], dtype=float)
    widths = np.asarray([max(1.0, float(row['right']) - float(row['left'])) for row in rows], dtype=float)
    accent, line_mid, _line_dark = _chart_colors(str(scenario.get('name') or ''))

    fig = plt.figure(figsize=(7.2, 3.2), dpi=160)
    ax = fig.add_subplot(111)
    fig.patch.set_facecolor('#08131f')
    ax.set_facecolor('#08131f')
    ax.bar(centers, counts, width=widths * 0.92, color=line_mid, edgecolor=accent, linewidth=0.8)
    ax.set_title(f"{scenario.get('label', scenario.get('name', 'Scenario'))} — Distribuição da equity final", color='#e7f2ff', fontsize=12)
    ax.set_xlabel('Equity final (BRL)', color='#9fb5cc')
    ax.set_ylabel('Trials', color='#9fb5cc')
    ax.tick_params(colors='#9fb5cc', labelsize=8)
    for spine in ax.spines.values():
        spine.set_color('#1b3551')
    ax.grid(True, axis='y', color='#143149', alpha=0.35, linewidth=0.6)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    return buf.getvalue()


def _b64_png(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def _scenario_summary_row(scenario: dict[str, Any]) -> list[str]:
    ending = dict(scenario.get('ending_equity_brl') or {})
    drawdown = dict(scenario.get('max_drawdown_pct') or {})
    return [
        str(scenario.get('label') or scenario.get('name') or 'Scenario'),
        _fmt_number(ending.get('p50')),
        _fmt_number(ending.get('p05')),
        _fmt_number(ending.get('p95')),
        _fmt_pct(scenario.get('profit_probability')),
        _fmt_pct(drawdown.get('p50'), digits=2),
    ]


def _render_html(payload: dict[str, Any], *, out_dir: Path, html_path: Path, image_paths: dict[str, dict[str, Path]]) -> None:
    title = 'Thalor — Monte Carlo Report'
    settings = dict(payload.get('settings') or {})
    history = dict(payload.get('history') or {})
    scenarios = list(payload.get('scenarios') or [])

    rows = ''.join(
        '<tr>' + ''.join(f'<td>{cell}</td>' for cell in _scenario_summary_row(scenario)) + '</tr>'
        for scenario in scenarios
    )

    scenario_sections: list[str] = []
    for scenario in scenarios:
        name = str(scenario.get('name') or '')
        ending = dict(scenario.get('ending_equity_brl') or {})
        drawdown = dict(scenario.get('max_drawdown_pct') or {})
        fan_png = _b64_png(image_paths[name]['fan'].read_bytes())
        hist_png = _b64_png(image_paths[name]['hist'].read_bytes())
        scenario_sections.append(
            f'''
            <section class="scenario">
              <div class="scenario-head">
                <div>
                  <div class="eyebrow">SCENARIO</div>
                  <h2>{scenario.get('label')}</h2>
                </div>
                <div class="pill-row">
                  <span class="pill">P50 final {_fmt_number(ending.get('p50'))}</span>
                  <span class="pill">Profit {_fmt_pct(scenario.get('profit_probability'))}</span>
                  <span class="pill">DD p50 {_fmt_pct(drawdown.get('p50'), digits=2)}</span>
                </div>
              </div>
              <div class="chart-grid">
                <img src="data:image/png;base64,{fan_png}" alt="fan chart {name}" />
                <img src="data:image/png;base64,{hist_png}" alt="hist chart {name}" />
              </div>
            </section>
            '''
        )

    html_payload = f'''
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ background: linear-gradient(180deg, #06101a, #02060c 80%); color: #e7f2ff; font-family: Inter, Segoe UI, Arial, sans-serif; margin: 0; padding: 24px; }}
    .hero {{ border: 1px solid rgba(93, 228, 255, 0.18); border-radius: 20px; padding: 22px 24px; background: linear-gradient(135deg, rgba(11,24,39,.96), rgba(6,11,20,.96)); box-shadow: 0 16px 34px rgba(0,0,0,.28); }}
    .eyebrow {{ color: #5de4ff; letter-spacing: .25em; font-size: 12px; font-weight: 700; margin-bottom: 8px; }}
    .muted {{ color: #98abc3; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 14px; margin-top: 18px; }}
    .card {{ border: 1px solid rgba(93,228,255,.16); border-radius: 16px; padding: 14px; background: rgba(10,20,34,.88); }}
    .card .label {{ color: #98abc3; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; margin-bottom: 8px; }}
    .card .value {{ font-size: 26px; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; border-radius: 14px; overflow: hidden; margin-top: 18px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid rgba(93,228,255,.12); text-align: left; font-size: 14px; }}
    th {{ background: rgba(11,24,39,.95); color: #b8c8db; }}
    tbody tr:nth-child(odd) {{ background: rgba(8,16,28,.64); }}
    tbody tr:nth-child(even) {{ background: rgba(6,12,22,.92); }}
    .scenario {{ margin-top: 30px; border: 1px solid rgba(93,228,255,.12); border-radius: 18px; padding: 18px; background: rgba(9,18,32,.8); }}
    .scenario-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }}
    .scenario-head h2 {{ margin: 0; }}
    .pill-row {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .pill {{ border: 1px solid rgba(93,228,255,.18); border-radius: 999px; padding: 8px 12px; color: #bfefff; font-size: 12px; }}
    .chart-grid {{ display: grid; grid-template-columns: 1fr; gap: 18px; margin-top: 16px; }}
    .chart-grid img {{ width: 100%; border-radius: 14px; border: 1px solid rgba(93,228,255,.12); background: #08131f; }}
    h2.section {{ margin-top: 30px; }}
    ul {{ color: #b8c8db; line-height: 1.5; }}
    @media (min-width: 1200px) {{ .chart-grid {{ grid-template-columns: 1fr 1fr; }} }}
  </style>
</head>
<body>
  <section class="hero">
    <div class="eyebrow">THALOR // MONTE CARLO REALISTA</div>
    <h1>{title}</h1>
    <p class="muted">Baseado nos trades históricos realizados do projeto · Gerado em {payload.get('generated_at_utc')} · Perfil {payload.get('profile')}</p>
  </section>
  <div class="cards">
    <div class="card"><div class="label">Capital inicial</div><div class="value">R$ {_fmt_number(settings.get('initial_capital_brl'))}</div></div>
    <div class="card"><div class="label">Horizonte</div><div class="value">{int(settings.get('horizon_days') or 0)} dias</div></div>
    <div class="card"><div class="label">Trials</div><div class="value">{int(settings.get('trials') or 0):,}</div></div>
    <div class="card"><div class="label">Trades históricos</div><div class="value">{int(history.get('realized_trades') or 0):,}</div></div>
  </div>

  <h2 class="section">Resumo dos cenários</h2>
  <table>
    <thead><tr><th>Cenário</th><th>P50 final</th><th>P05 final</th><th>P95 final</th><th>Prob. lucro</th><th>DD p50</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <h2 class="section">Metodologia</h2>
  <ul>
    <li>Trades reais são lidos do ledger de execução do projeto.</li>
    <li>A simulação usa bootstrap dos retornos por trade, stakes observados e frequência diária histórica.</li>
    <li>Os cenários Conservador / Médio / Agressivo aplicam escalas diferentes de frequência, retorno e stake, mantendo a base empírica do projeto.</li>
  </ul>

  {''.join(scenario_sections)}
</body>
</html>
    '''
    html_path.write_text(html_payload, encoding='utf-8')


def _render_pdf(payload: dict[str, Any], *, pdf_path: Path, image_paths: dict[str, dict[str, Path]]) -> None:
    _plt, _np, rl, platypus = _require_plotting_deps()
    colors, A4, getSampleStyleSheet, ParagraphStyle, cm = rl
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak = platypus

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'MC_Title',
        parent=styles['Heading1'],
        fontSize=24,
        leading=28,
        textColor=colors.HexColor('#0d2743'),
        spaceAfter=12,
    )
    subtitle_style = ParagraphStyle(
        'MC_Subtitle',
        parent=styles['BodyText'],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#556b85'),
        spaceAfter=8,
    )
    section_style = ParagraphStyle(
        'MC_Section',
        parent=styles['Heading2'],
        fontSize=16,
        leading=20,
        textColor=colors.HexColor('#0d2743'),
        spaceAfter=8,
        spaceBefore=12,
    )
    body_style = ParagraphStyle(
        'MC_Body',
        parent=styles['BodyText'],
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor('#22384d'),
        spaceAfter=6,
    )

    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4, rightMargin=1.4 * cm, leftMargin=1.4 * cm, topMargin=1.2 * cm, bottomMargin=1.2 * cm)
    story: list[Any] = []
    story.append(Paragraph('Thalor — Monte Carlo Realista', title_style))
    story.append(Paragraph(f"Gerado em {payload.get('generated_at_utc')} · Perfil {payload.get('profile')}", subtitle_style))

    settings = dict(payload.get('settings') or {})
    history = dict(payload.get('history') or {})
    summary_table = Table(
        [
            ['Capital inicial', f"R$ {_fmt_number(settings.get('initial_capital_brl'))}", 'Horizonte', f"{int(settings.get('horizon_days') or 0)} dias"],
            ['Trials', f"{int(settings.get('trials') or 0):,}", 'Trades históricos', f"{int(history.get('realized_trades') or 0):,}"],
            ['Amostra diária', f"{int(history.get('sample_days') or 0):,} dias", 'Timezone', str(settings.get('timezone') or 'UTC')],
        ],
        colWidths=[3.2 * cm, 4.0 * cm, 3.2 * cm, 4.8 * cm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f5f8fc')),
                ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#c7d7ea')),
                ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d9e5f3')),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#22384d')),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([summary_table, Spacer(1, 0.25 * cm)])
    story.append(Paragraph('Metodologia', section_style))
    story.append(Paragraph(
        'A simulação usa os trades realizados do ledger de execução do projeto. Os cenários Conservador, Médio e Agressivo aplicam escalas diferentes de frequência diária, retorno por trade e stake, preservando a base empírica da estratégia.',
        body_style,
    ))

    header = ['Cenário', 'P50 final', 'P05 final', 'P95 final', 'Prob. lucro', 'DD p50']
    rows = [header] + [_scenario_summary_row(scenario) for scenario in list(payload.get('scenarios') or [])]
    table = Table(rows, colWidths=[3.0 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm, 2.4 * cm])
    table.setStyle(
        TableStyle(
            [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d2743')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f9fbfe')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f9fbfe'), colors.HexColor('#eef4fb')]),
                ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#c7d7ea')),
                ('INNERGRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#d9e5f3')),
                ('FONTSIZE', (0, 0), (-1, -1), 8.5),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 7),
                ('RIGHTPADDING', (0, 0), (-1, -1), 7),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([Paragraph('Resumo dos cenários', section_style), table])

    for idx, scenario in enumerate(list(payload.get('scenarios') or [])):
        name = str(scenario.get('name') or '')
        story.append(PageBreak())
        story.append(Paragraph(f"{scenario.get('label')} — visão detalhada", section_style))
        ending = dict(scenario.get('ending_equity_brl') or {})
        drawdown = dict(scenario.get('max_drawdown_pct') or {})
        story.append(Paragraph(
            f"P50 final: <b>R$ {_fmt_number(ending.get('p50'))}</b> · P05: R$ {_fmt_number(ending.get('p05'))} · P95: R$ {_fmt_number(ending.get('p95'))} · Prob. lucro: {_fmt_pct(scenario.get('profit_probability'))} · DD p50: {_fmt_pct(drawdown.get('p50'), digits=2)}",
            body_style,
        ))
        story.append(Image(str(image_paths[name]['fan']), width=17.5 * cm, height=7.6 * cm))
        story.append(Spacer(1, 0.2 * cm))
        story.append(Image(str(image_paths[name]['hist']), width=17.5 * cm, height=7.6 * cm))

    doc.build(story)


def export_monte_carlo_report(
    payload: dict[str, Any],
    *,
    output_dir: str | Path,
    export_html: bool = True,
    export_pdf: bool = True,
    export_json: bool = True,
) -> dict[str, str]:
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')
    base = f'monte_carlo_report_{ts}'
    image_paths: dict[str, dict[str, Path]] = {}
    for scenario in list(payload.get('scenarios') or []):
        name = str(scenario.get('name') or 'scenario')
        fan_path = out_dir / f'{base}_{name}_fan.png'
        hist_path = out_dir / f'{base}_{name}_hist.png'
        _write_bytes(fan_path, _fan_chart_png(scenario))
        _write_bytes(hist_path, _hist_chart_png(scenario))
        image_paths[name] = {'fan': fan_path, 'hist': hist_path}

    paths: dict[str, str] = {}
    if export_json:
        json_path = out_dir / f'{base}.json'
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        latest_json = out_dir / 'monte_carlo_latest.json'
        latest_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        paths['json_path'] = str(json_path)
        paths['latest_json_path'] = str(latest_json)
    if export_html:
        html_path = out_dir / f'{base}.html'
        _render_html(payload, out_dir=out_dir, html_path=html_path, image_paths=image_paths)
        latest_html = out_dir / 'monte_carlo_latest.html'
        latest_html.write_text(html_path.read_text(encoding='utf-8'), encoding='utf-8')
        paths['html_path'] = str(html_path)
        paths['latest_html_path'] = str(latest_html)
    if export_pdf:
        pdf_path = out_dir / f'{base}.pdf'
        _render_pdf(payload, pdf_path=pdf_path, image_paths=image_paths)
        latest_pdf = out_dir / 'monte_carlo_latest.pdf'
        latest_pdf.write_bytes(pdf_path.read_bytes())
        paths['pdf_path'] = str(pdf_path)
        paths['latest_pdf_path'] = str(latest_pdf)
    return paths


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog='python -m natbin.monte_carlo.report', description='Export a realistic Monte Carlo report from Thalor historical trades.')
    p.add_argument('--repo-root', default='.')
    p.add_argument('--config', default='config/multi_asset.yaml')
    p.add_argument('--output-dir', default='runs/reports/monte_carlo')
    p.add_argument('--initial-capital-brl', type=float, default=None)
    p.add_argument('--horizon-days', type=int, default=None)
    p.add_argument('--trials', type=int, default=None)
    p.add_argument('--rng-seed', type=int, default=None)
    p.add_argument('--json', action='store_true')
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import sys

    ns = _parse_args(list(sys.argv[1:] if argv is None else argv))
    payload = build_monte_carlo_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        initial_capital_brl=ns.initial_capital_brl,
        horizon_days=ns.horizon_days,
        trials=ns.trials,
        rng_seed=ns.rng_seed,
        write_report=False,
    )
    if bool(payload.get('ok')):
        output_dir = Path(str(ns.output_dir)).expanduser()
        if not output_dir.is_absolute():
            output_dir = Path(str(ns.repo_root)).resolve() / output_dir
        payload['report_paths'] = export_monte_carlo_report(
            payload,
            output_dir=output_dir,
            export_html=True,
            export_pdf=True,
            export_json=True,
        )
    if ns.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        for key, value in payload.items():
            print(f'{key}: {value}')
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
