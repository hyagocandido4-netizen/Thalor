from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from natbin.monte_carlo.engine import build_monte_carlo_payload
from natbin.state.execution_migrations import ensure_execution_db


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'monte.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'runtime:',
                '  profile: monte_test',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: America/Sao_Paulo',
                'execution:',
                '  enabled: false',
                '  mode: disabled',
                'monte_carlo:',
                '  initial_capital_brl: 1000.0',
                '  horizon_days: 20',
                '  trials: 200',
                '  rng_seed: 123',
                '  min_realized_trades: 10',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def _seed_execution_db(repo_root: Path, *, trades: int = 36) -> None:
    db_path = repo_root / 'runs' / 'runtime_execution.sqlite3'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    ensure_execution_db(con)
    start = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    for idx in range(trades):
        trade_at = start + timedelta(hours=12 * idx)
        amount = 5.0 + float(idx % 4)
        if idx % 5 == 0:
            settlement = 'refund'
            pnl = 0.0
        elif idx % 2 == 0:
            settlement = 'win'
            pnl = round(amount * 0.82, 2)
        else:
            settlement = 'loss'
            pnl = round(-amount, 2)
        external_order_id = f'order-{idx:03d}'
        con.execute(
            '''
            INSERT INTO broker_orders (
                broker_name, account_mode, external_order_id, intent_id, client_order_key,
                asset, side, amount, currency, broker_status, opened_at_utc, expires_at_utc,
                closed_at_utc, gross_payout, net_pnl, settlement_status, estimated_pnl,
                raw_snapshot_json, last_seen_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                'iqoption',
                'PRACTICE',
                external_order_id,
                f'intent-{idx:03d}',
                f'client-{idx:03d}',
                'EURUSD-OTC',
                'call' if idx % 2 == 0 else 'put',
                amount,
                'BRL',
                'closed',
                trade_at.isoformat(),
                (trade_at + timedelta(minutes=5)).isoformat(),
                (trade_at + timedelta(minutes=6)).isoformat(),
                max(0.0, amount + pnl),
                pnl,
                settlement,
                0,
                json.dumps({'order': external_order_id}),
                (trade_at + timedelta(minutes=6)).isoformat(),
            ),
        )
    con.commit()
    con.close()


def test_monte_carlo_payload_builds_reports(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _seed_execution_db(tmp_path, trades=36)

    payload = build_monte_carlo_payload(repo_root=tmp_path, config_path=cfg, write_report=True)

    assert payload['ok'] is True
    assert payload['history']['realized_trades'] == 36
    assert len(payload['scenarios']) == 3
    scenario_names = {item['name'] for item in payload['scenarios']}
    assert scenario_names == {'conservative', 'medium', 'aggressive'}
    report_paths = payload['report_paths']
    assert Path(report_paths['html_path']).exists()
    assert Path(report_paths['pdf_path']).exists()
    assert Path(report_paths['json_path']).exists()


def test_monte_carlo_payload_flags_insufficient_history(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _seed_execution_db(tmp_path, trades=6)

    payload = build_monte_carlo_payload(repo_root=tmp_path, config_path=cfg, write_report=False)

    assert payload['ok'] is False
    assert payload['reason'] == 'insufficient_history'
