from __future__ import annotations

import contextlib
import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from natbin.config.loader import load_resolved_config
from natbin.control import app as control_app
from natbin.state.repos import SignalsRepository


ASSET = 'EURUSD-OTC'
INTERVAL = 300


def _write_profile(repo: Path, *, mode: str = 'practice', provider: str = 'fake', account_mode: str = 'PRACTICE') -> Path:
    cfg = repo / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: live_controlled_practice',
                'broker:',
                '  provider: iqoption',
                'execution:',
                '  enabled: true',
                f'  mode: {mode}',
                f'  provider: {provider}',
                f'  account_mode: {account_mode}',
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
    (repo / '.env').write_text('THALOR__EXECUTION__ENABLED=0\nTHALOR__EXECUTION__MODE=paper\n', encoding='utf-8')
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
            'k': 1,
            'rank_in_day': 1,
            'executed_today': 0,
            'budget_left': 1,
            'action': 'CALL',
            'reason': 'execution_profile_merge_fix',
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



def _run(argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = control_app.main(argv)
    return code, json.loads(buf.getvalue())



def test_load_resolved_config_prefers_explicit_profile_execution_over_dotenv_defaults(tmp_path: Path) -> None:
    cfg = _write_profile(tmp_path)

    resolved = load_resolved_config(config_path=cfg, repo_root=tmp_path)

    assert resolved.execution.enabled is True
    assert resolved.execution.mode == 'practice'
    assert resolved.execution.provider == 'fake'
    assert resolved.execution.account_mode == 'PRACTICE'



def test_execute_order_respects_profile_execution_block_even_with_dotenv_disable(tmp_path: Path) -> None:
    cfg = _write_profile(tmp_path)
    _seed_trade_signal(tmp_path)

    code, payload = _run([
        'execute_order',
        '--repo-root',
        str(tmp_path),
        '--config',
        str(cfg),
        '--json',
    ])

    assert code == 0
    assert payload['enabled'] is True
    assert payload['mode'] == 'practice'
    assert payload['provider'] == 'fake'
    latest_intent = dict(payload.get('latest_intent') or {})
    assert latest_intent['account_mode'] == 'PRACTICE'
    assert latest_intent['external_order_id']
