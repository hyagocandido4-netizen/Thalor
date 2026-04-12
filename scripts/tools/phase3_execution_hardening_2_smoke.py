from __future__ import annotations

import contextlib
import io
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from natbin.control import app as control_app
from natbin.state.repos import SignalsRepository

ASSET = 'EURUSD-OTC'
INTERVAL = 300


def _write_config(repo: Path) -> Path:
    cfg = repo / 'config' / 'multi_asset_live_real_smoke.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: phase3_execution_hardening_2_smoke',
                'broker:',
                '  provider: iqoption',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: fake',
                '  account_mode: REAL',
                '  stake:',
                '    amount: 2.0',
                '    currency: BRL',
                '  real_guard:',
                '    enabled: true',
                '    require_env_allow_real: true',
                '    allow_multi_asset_live: false',
                '    serialize_submits: true',
                '    min_submit_spacing_sec: 0',
                '    max_pending_unknown_total: 1',
                '    max_open_positions_total: 1',
                '    recent_failure_window_sec: 300',
                '    max_recent_transport_failures: 2',
                '    post_submit_verify_enable: true',
                '    post_submit_verify_timeout_sec: 1',
                '    post_submit_verify_poll_sec: 0.01',
                '  fake:',
                '    submit_behavior: ack',
                '    settlement: open',
                '    heartbeat_ok: true',
                'multi_asset:',
                '  enabled: true',
                '  max_parallel_assets: 3',
                'assets:',
                f'  - asset: {ASSET}',
                f'    interval_sec: {INTERVAL}',
                '    timezone: UTC',
                '    cluster_key: fx',
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
            'conf': 0.85,
            'score': 0.12,
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
            'reason': 'phase3_execution_hardening_smoke',
            'blockers': '',
            'close': 1.0,
            'payout': 0.80,
            'ev': 0.05,
        }
    )


def _run(argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = control_app.main(argv)
    return code, json.loads(buf.getvalue())


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_phase3_exec_hardening_2_') as td:
        repo = Path(td)
        cfg = _write_config(repo)
        _seed_trade_signal(repo)

        import os

        os.environ['THALOR_EXECUTION_ALLOW_REAL'] = '1'
        code, payload = _run(['execute-order', '--repo-root', str(repo), '--config', str(cfg), '--json'])
        if code != 0:
            raise SystemExit('execute-order returned non-zero')
        if str(payload.get('blocked_reason') or '') != 'real_multi_asset_not_enabled':
            raise SystemExit(f"unexpected blocked_reason: {payload.get('blocked_reason')!r}")
        hardening = dict(payload.get('execution_hardening') or {})
        if hardening.get('reason') != 'real_multi_asset_not_enabled':
            raise SystemExit('execution_hardening reason mismatch')
    print('OK phase3_execution_hardening_2_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
