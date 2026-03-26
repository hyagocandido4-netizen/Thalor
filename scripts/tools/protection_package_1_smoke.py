from __future__ import annotations

import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from natbin.control.commands import execute_order_payload, protection_payload
from natbin.runtime.broker_surface import build_context
from natbin.security.account_protection import evaluate_account_protection, note_protection_submit_attempt
from natbin.state.repos import SignalsRepository

ASSET = 'EURUSD-OTC'
OTHER_ASSET = 'GBPUSD-OTC'
INTERVAL = 300


def _write_config(repo: Path) -> Path:
    cfg = repo / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: protection_1_smoke',
                'broker:',
                '  provider: iqoption',
                'execution:',
                '  enabled: true',
                '  mode: practice',
                '  provider: fake',
                '  account_mode: PRACTICE',
                '  stake:',
                '    amount: 2.0',
                '    currency: BRL',
                '  fake:',
                '    submit_behavior: ack',
                '    settlement: open',
                '    heartbeat_ok: true',
                'security:',
                '  enabled: true',
                '  guard:',
                '    enabled: true',
                '    live_only: true',
                '    min_submit_spacing_sec: 0',
                '    max_submit_per_minute: 10',
                '    time_filter_enable: false',
                '  protection:',
                '    enabled: true',
                '    live_submit_only: true',
                '    state_path: runs/security/account_protection_state.json',
                '    decision_log_path: runs/logs/account_protection.jsonl',
                '    sessions:',
                '      enabled: true',
                '      inherit_guard_window: false',
                '      blocked_weekdays_local: []',
                '      windows:',
                '        - name: all_day',
                '          start_local: "00:00"',
                '          end_local: "23:59"',
                '    cadence:',
                '      enabled: true',
                '      apply_delay_before_submit: true',
                '      min_delay_sec: 0.0',
                '      max_delay_sec: 0.0',
                '      early_morning_extra_sec: 0.0',
                '      midday_extra_sec: 0.0',
                '      evening_extra_sec: 0.0',
                '      overnight_extra_sec: 0.0',
                '      volatility_extra_sec: 0.0',
                '      recent_submit_weight_sec: 0.0',
                '      jitter_max_sec: 0.0',
                '    pacing:',
                '      enabled: true',
                '      min_spacing_global_sec: 0',
                '      min_spacing_asset_sec: 20',
                '      max_submit_15m_global: 5',
                '      max_submit_15m_asset: 5',
                '      max_submit_60m_global: 5',
                '      max_submit_60m_asset: 5',
                '      max_submit_day_global: 10',
                '      max_submit_day_asset: 10',
                '    correlation:',
                '      enabled: true',
                '      block_same_cluster_active: true',
                '      max_active_per_cluster: 1',
                '      max_pending_per_cluster: 1',
                'assets:',
                f'  - asset: {ASSET}',
                f'    interval_sec: {INTERVAL}',
                '    timezone: UTC',
                '    cluster_key: fx',
                f'  - asset: {OTHER_ASSET}',
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
            'reason': 'protection_smoke',
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_protection1_') as tmp:
        repo = Path(tmp)
        cfg = _write_config(repo)
        _seed_trade_signal(repo)

        protection = protection_payload(repo_root=repo, config_path=cfg)
        if protection.get('allowed') is not True:
            raise SystemExit(f'protection pre-submit should allow, got: {json.dumps(protection, ensure_ascii=False)}')

        execute = execute_order_payload(repo_root=repo, config_path=cfg)
        if execute.get('enabled') is not True:
            raise SystemExit(f'execute_order should be enabled, got: {json.dumps(execute, ensure_ascii=False)}')
        if not isinstance(execute.get('account_protection'), dict):
            raise SystemExit('execute_order payload missing account_protection block')

        ctx = build_context(repo_root=repo, config_path=cfg)
        note_protection_submit_attempt(repo_root=repo, ctx=ctx, cluster_key='fx', transport_status='ack')
        blocked = evaluate_account_protection(repo_root=repo, ctx=ctx, write_artifact=False)
        if blocked.allowed is not False or blocked.reason != 'protection_asset_spacing':
            raise SystemExit(f'expected asset spacing block after submit note, got: {json.dumps(blocked.as_dict(), ensure_ascii=False)}')

        print('OK protection_package_1_smoke')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
