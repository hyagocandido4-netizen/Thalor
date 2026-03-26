from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from natbin.control.app import main as runtime_main
from natbin.state.execution_migrations import ensure_execution_db


def _write_config(repo_root: Path) -> Path:
    path = repo_root / 'config' / 'monte.yaml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '\n'.join(
            [
                'runtime:',
                '  profile: monte_smoke',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: America/Sao_Paulo',
                'execution:',
                '  enabled: false',
                '  mode: disabled',
                'monte_carlo:',
                '  initial_capital_brl: 1000.0',
                '  horizon_days: 15',
                '  trials: 150',
                '  rng_seed: 99',
                '  min_realized_trades: 10',
            ]
        ),
        encoding='utf-8',
    )
    return path


def _seed_execution_db(repo_root: Path) -> None:
    db_path = repo_root / 'runs' / 'runtime_execution.sqlite3'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    ensure_execution_db(con)
    base = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    for idx in range(24):
        trade_at = base + timedelta(hours=12 * idx)
        amount = 5.0 + float(idx % 3)
        settlement = 'win' if idx % 2 == 0 else 'loss'
        pnl = round(amount * 0.82, 2) if settlement == 'win' else round(-amount, 2)
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
                f'order-{idx:03d}',
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
                json.dumps({'order_id': idx}),
                (trade_at + timedelta(minutes=6)).isoformat(),
            ),
        )
    con.commit()
    con.close()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_mc4_smoke_') as tmp:
        repo_root = Path(tmp)
        cfg = _write_config(repo_root)
        _seed_execution_db(repo_root)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = runtime_main(
                [
                    'monte-carlo',
                    '--repo-root',
                    str(repo_root),
                    '--config',
                    str(cfg),
                    '--json',
                ]
            )
        if int(code) != 0:
            raise SystemExit(f'expected exit 0, got {code}')
        report_dir = repo_root / 'runs' / 'reports' / 'monte_carlo'
        latest_json = report_dir / 'monte_carlo_latest.json'
        latest_html = report_dir / 'monte_carlo_latest.html'
        latest_pdf = report_dir / 'monte_carlo_latest.pdf'
        if not latest_json.exists() or not latest_html.exists() or not latest_pdf.exists():
            raise SystemExit('expected monte carlo report outputs to exist')
    print('OK monte_carlo_package_4_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
