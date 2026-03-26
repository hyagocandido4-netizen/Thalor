from __future__ import annotations

import contextlib
import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from natbin.control import app as control_app
from natbin.runtime.broker_surface import build_context, execution_repo_path
from natbin.security.account_protection import evaluate_account_protection, note_protection_submit_attempt
from natbin.state.execution_repo import ExecutionRepository
from natbin.state.repos import SignalsRepository
from natbin.runtime.execution_models import OrderIntent

ASSET = 'EURUSD-OTC'
OTHER_ASSET = 'GBPUSD-OTC'
INTERVAL = 300


def _write_config(
    repo: Path,
    *,
    min_spacing_global_sec: int = 0,
    min_spacing_asset_sec: int = 15,
    enable_correlation: bool = True,
    max_active_per_cluster: int = 1,
    min_delay_sec: float = 0.0,
    max_delay_sec: float = 0.0,
) -> Path:
    cfg = repo / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: protection_1_test',
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
                f'      min_delay_sec: {min_delay_sec}',
                f'      max_delay_sec: {max_delay_sec}',
                '      early_morning_extra_sec: 0.0',
                '      midday_extra_sec: 0.0',
                '      evening_extra_sec: 0.0',
                '      overnight_extra_sec: 0.0',
                '      volatility_extra_sec: 0.0',
                '      recent_submit_weight_sec: 0.0',
                '      jitter_max_sec: 0.0',
                '    pacing:',
                '      enabled: true',
                f'      min_spacing_global_sec: {min_spacing_global_sec}',
                f'      min_spacing_asset_sec: {min_spacing_asset_sec}',
                '      max_submit_15m_global: 5',
                '      max_submit_15m_asset: 5',
                '      max_submit_60m_global: 5',
                '      max_submit_60m_asset: 5',
                '      max_submit_day_global: 10',
                '      max_submit_day_asset: 10',
                '    correlation:',
                f'      enabled: {str(enable_correlation).lower()}',
                '      block_same_cluster_active: true',
                f'      max_active_per_cluster: {max_active_per_cluster}',
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
        'k': 1,
        'rank_in_day': 1,
        'executed_today': 0,
        'budget_left': 1,
        'action': 'CALL',
        'reason': 'protection_test_trade',
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


def _intent(*, asset: str, day: str, ts: int, scope_tag: str, cluster_key: str, state: str = 'accepted_open') -> OrderIntent:
    now_iso = datetime.now(tz=UTC).isoformat(timespec='seconds')
    return OrderIntent(
        intent_id=f'intent-{asset}-{ts}',
        scope_tag=scope_tag,
        broker_name='fake',
        account_mode='PRACTICE',
        day=day,
        asset=asset,
        interval_sec=INTERVAL,
        signal_ts=ts,
        decision_action='CALL',
        decision_conf=0.8,
        decision_score=0.2,
        stake_amount=2.0,
        stake_currency='BRL',
        expiry_ts=ts + INTERVAL,
        entry_deadline_utc=now_iso,
        client_order_key=f'client-{asset}-{ts}',
        intent_state=state,
        broker_status='open' if state == 'accepted_open' else 'unknown',
        created_at_utc=now_iso,
        updated_at_utc=now_iso,
        cluster_key=cluster_key,
    )


def _run(argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = control_app.main(argv)
    return code, json.loads(buf.getvalue())


def test_protection_command_writes_artifact_and_metrics(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _seed_trade_signal(tmp_path)

    code, payload = _run(['protection', '--repo-root', str(tmp_path), '--config', str(cfg), '--json'])

    assert code == 0
    assert payload['kind'] == 'account_protection'
    assert payload['allowed'] is True
    assert payload['action'] == 'allow'
    assert 'behavior_metrics' in dict(payload['details'])
    artifact = tmp_path / 'runs' / 'control' / f'{ASSET}_{INTERVAL}s' / 'protection.json'
    assert artifact.exists()
    logged = tmp_path / 'runs' / 'logs' / 'account_protection.jsonl'
    assert logged.exists()


def test_protection_spacing_blocks_after_recent_submit_note(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, min_spacing_global_sec=0, min_spacing_asset_sec=30)
    _seed_trade_signal(tmp_path)
    ctx = build_context(repo_root=tmp_path, config_path=cfg)

    initial = evaluate_account_protection(repo_root=tmp_path, ctx=ctx, write_artifact=False)
    assert initial.allowed is True

    note_protection_submit_attempt(repo_root=tmp_path, ctx=ctx, cluster_key='fx', transport_status='ack')

    blocked = evaluate_account_protection(repo_root=tmp_path, ctx=ctx, write_artifact=False)
    assert blocked.allowed is False
    assert blocked.reason == 'protection_asset_spacing'
    assert blocked.asset_spacing_open is False


def test_protection_blocks_same_cluster_active_intent(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, enable_correlation=True, max_active_per_cluster=1)
    day, ts = _seed_trade_signal(tmp_path)
    ctx = build_context(repo_root=tmp_path, config_path=cfg)
    repo = ExecutionRepository(execution_repo_path(tmp_path))
    repo.save_intent(_intent(asset=OTHER_ASSET, day=day, ts=ts - INTERVAL, scope_tag=f'{OTHER_ASSET}_{INTERVAL}s', cluster_key='fx'))

    decision = evaluate_account_protection(repo_root=tmp_path, ctx=ctx, write_artifact=False)

    assert decision.allowed is False
    assert decision.reason == 'protection_correlation_cluster_active'
    assert decision.correlation_open is False
    assert int(decision.details['cluster_open_count']) >= 1


def test_execute_order_payload_includes_account_protection(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _seed_trade_signal(tmp_path)

    code, payload = _run(['execute_order', '--repo-root', str(tmp_path), '--config', str(cfg), '--json'])

    assert code == 0
    assert payload['enabled'] is True
    protection = dict(payload.get('account_protection') or {})
    assert protection['allowed'] is True
    assert protection['action'] in {'allow', 'delay'}
    assert dict(payload.get('latest_intent') or {})['external_order_id']
