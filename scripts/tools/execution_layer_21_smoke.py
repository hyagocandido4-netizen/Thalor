from __future__ import annotations

import json
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from natbin.control import app as control_app
from natbin.state.repos import SignalsRepository


ASSET = 'EURUSD-OTC'
INTERVAL = 300


def _write_config(repo: Path) -> Path:
    cfg = repo / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: execution_layer_21_smoke',
                'broker:',
                '  provider: iqoption',
                'execution:',
                '  enabled: true',
                '  mode: paper',
                '  provider: fake',
                '  account_mode: PRACTICE',
                '  stake:',
                '    amount: 2.0',
                '    currency: BRL',
                '  fake:',
                '    submit_behavior: ack',
                '    settlement: open',
                '    heartbeat_ok: true',
                'assets:',
                f'  - asset: {ASSET}',
                f'    interval_sec: {INTERVAL}',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def _seed_trade_signal(repo: Path) -> None:
    ts = int(time.time())
    ts -= ts % INTERVAL
    day = datetime.fromtimestamp(ts, tz=UTC).strftime('%Y-%m-%d')
    SignalsRepository(repo / 'runs' / 'live_signals.sqlite3', default_interval=INTERVAL).write_row(
        {
            'dt_local': datetime.fromtimestamp(ts, tz=UTC).strftime('%Y-%m-%d %H:%M:%S'),
            'day': day,
            'asset': ASSET,
            'interval_sec': INTERVAL,
            'ts': ts,
            'proba_up': 0.77,
            'conf': 0.83,
            'score': 0.19,
            'gate_mode': 'cp',
            'gate_mode_requested': 'cp',
            'gate_fail_closed': 0,
            'gate_fail_detail': '',
            'regime_ok': 1,
            'thresh_on': 'ev',
            'threshold': 0.02,
            'k': 3,
            'rank_in_day': 1,
            'executed_today': 0,
            'budget_left': 1,
            'action': 'CALL',
            'reason': 'smoke_trade',
            'blockers': '',
            'close': 1.0,
            'payout': 0.80,
            'ev': 0.11,
            'model_version': 'smoke-v1',
            'train_rows': 120,
            'train_end_ts': ts - INTERVAL,
            'best_source': 'smoke',
            'tune_dir': 'runs/tune',
            'feat_hash': 'abc123',
            'gate_version': 'gate-v1',
            'meta_model': 'hgb',
            'market_context_stale': 0,
            'market_context_fail_closed': 0,
        }
    )


def _run(argv: list[str]) -> dict:
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = control_app.main(argv)
    if code != 0:
        raise SystemExit(f'command failed with code={code}: {argv}')
    return json.loads(buf.getvalue())


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_exec21_smoke_') as tmp:
        repo = Path(tmp)
        cfg = _write_config(repo)
        _seed_trade_signal(repo)

        execute_payload = _run(['execute_order', '--repo-root', str(repo), '--config', str(cfg), '--json'])
        latest_intent = dict(execute_payload.get('latest_intent') or {})
        external_order_id = str(latest_intent.get('external_order_id') or '')
        if not external_order_id:
            raise SystemExit('missing external_order_id after execute_order')

        status_payload = _run([
            'check_order_status',
            '--repo-root',
            str(repo),
            '--config',
            str(cfg),
            '--external-order-id',
            external_order_id,
            '--json',
        ])
        broker_snapshot = dict(status_payload.get('broker_snapshot') or {})
        if broker_snapshot.get('broker_status') != 'open':
            raise SystemExit(f'unexpected broker_status: {broker_snapshot.get("broker_status")}')

        log_path = repo / 'runs' / 'logs' / 'execution_events.jsonl'
        if not log_path.exists():
            raise SystemExit('missing execution_events.jsonl log')

    print('OK execution_layer_21_smoke')
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
