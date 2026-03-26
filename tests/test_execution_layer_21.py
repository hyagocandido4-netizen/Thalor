from __future__ import annotations

import contextlib
import io
import json
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
                '  profile: execution_layer_21_test',
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


def _seed_trade_signal(repo: Path, *, asset: str = ASSET, interval_sec: int = INTERVAL) -> tuple[str, int]:
    ts = int(time.time())
    ts -= ts % interval_sec
    day = datetime.fromtimestamp(ts, tz=UTC).strftime('%Y-%m-%d')
    row = {
        'dt_local': datetime.fromtimestamp(ts, tz=UTC).strftime('%Y-%m-%d %H:%M:%S'),
        'day': day,
        'asset': asset,
        'interval_sec': interval_sec,
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
        'reason': 'test_trade',
        'blockers': '',
        'close': 1.0,
        'payout': 0.80,
        'ev': 0.11,
        'model_version': 'test-v1',
        'train_rows': 120,
        'train_end_ts': ts - interval_sec,
        'best_source': 'test',
        'tune_dir': 'runs/tune',
        'feat_hash': 'abc123',
        'gate_version': 'gate-v1',
        'meta_model': 'hgb',
        'market_context_stale': 0,
        'market_context_fail_closed': 0,
    }
    SignalsRepository(repo / 'runs' / 'live_signals.sqlite3', default_interval=interval_sec).write_row(row)
    return day, ts


def _json_from_cli(argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = control_app.main(argv)
    return code, json.loads(buf.getvalue())



def test_control_app_execute_order_and_check_order_status_aliases(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _seed_trade_signal(tmp_path)

    code, execute_payload = _json_from_cli([
        'execute_order',
        '--repo-root',
        str(tmp_path),
        '--config',
        str(cfg),
        '--json',
    ])
    assert code == 0
    assert execute_payload['enabled'] is True
    assert execute_payload['provider'] == 'fake'
    latest_intent = dict(execute_payload['latest_intent'] or {})
    assert latest_intent['account_mode'] == 'PRACTICE'
    assert latest_intent['external_order_id']
    assert latest_intent['intent_state'] in {'accepted_open', 'settled'}

    log_path = tmp_path / 'runs' / 'logs' / 'execution_events.jsonl'
    assert log_path.exists()
    log_text = log_path.read_text(encoding='utf-8')
    assert 'submit_requested' in log_text
    assert 'submit_acked' in log_text

    code, status_payload = _json_from_cli([
        'check_order_status',
        '--repo-root',
        str(tmp_path),
        '--config',
        str(cfg),
        '--external-order-id',
        str(latest_intent['external_order_id']),
        '--json',
    ])
    assert code == 0
    assert status_payload['requested_external_order_id'] == latest_intent['external_order_id']
    broker_snapshot = dict(status_payload['broker_snapshot'] or {})
    assert broker_snapshot['external_order_id'] == latest_intent['external_order_id']
    assert broker_snapshot['broker_status'] == 'open'
    assert dict(status_payload['intent'] or {})['intent_id'] == latest_intent['intent_id']
