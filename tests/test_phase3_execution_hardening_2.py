from __future__ import annotations

import contextlib
import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from natbin.control import app as control_app
from natbin.runtime.broker_surface import build_context, execution_repo_path
from natbin.runtime.execution_hardening import evaluate_execution_hardening, verify_live_submit
from natbin.runtime.execution_models import BrokerOrderSnapshot, OrderIntent
from natbin.state.execution_repo import ExecutionRepository
from natbin.state.repos import SignalsRepository

ASSET = 'EURUSD-OTC'
INTERVAL = 300


def _write_config(
    repo: Path,
    *,
    allow_multi_asset_live: bool = False,
    multi_asset_enabled: bool = True,
    min_submit_spacing_sec: int = 0,
    max_open_positions_total: int = 1,
) -> Path:
    cfg = repo / 'config' / 'phase3_execution_hardening.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: phase3_execution_hardening_2',
                'broker:',
                '  provider: iqoption',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: fake',
                '  account_mode: REAL',
                '  fail_closed: true',
                '  client_order_prefix: thalor',
                '  stake:',
                '    amount: 2.0',
                '    currency: BRL',
                '  submit:',
                '    grace_sec: 2',
                '    max_latency_ms: 1500',
                '    retry_on_reject: false',
                '    retry_on_timeout: false',
                '  reconcile:',
                '    poll_interval_sec: 5',
                '    history_lookback_sec: 3600',
                '    orphan_lookback_sec: 7200',
                '    not_found_grace_sec: 20',
                '    settle_grace_sec: 30',
                '    scan_without_pending: false',
                '  limits:',
                '    max_pending_unknown: 1',
                '    max_open_positions: 1',
                '  real_guard:',
                '    enabled: true',
                '    require_env_allow_real: true',
                f'    allow_multi_asset_live: {str(allow_multi_asset_live).lower()}',
                '    serialize_submits: true',
                '    submit_lock_path: runs/runtime_execution.submit.lock',
                f'    min_submit_spacing_sec: {int(min_submit_spacing_sec)}',
                '    max_pending_unknown_total: 1',
                f'    max_open_positions_total: {int(max_open_positions_total)}',
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
                f'  enabled: {str(multi_asset_enabled).lower()}',
                '  max_parallel_assets: 3',
                '  execution_stagger_sec: 2.0',
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


def _seed_trade_signal(repo: Path) -> tuple[str, int]:
    ts = int(time.time())
    ts -= ts % INTERVAL
    day = datetime.fromtimestamp(ts, tz=UTC).strftime('%Y-%m-%d')
    row = {
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
        'reason': 'phase3_execution_hardening',
        'blockers': '',
        'close': 1.0,
        'payout': 0.80,
        'ev': 0.11,
    }
    SignalsRepository(repo / 'runs' / 'live_signals.sqlite3', default_interval=INTERVAL).write_row(row)
    return day, ts


def _intent(*, day: str, ts: int, state: str = 'accepted_open') -> OrderIntent:
    now_iso = datetime.now(tz=UTC).isoformat(timespec='seconds')
    return OrderIntent(
        intent_id=f'intent-{ts}',
        scope_tag=f'{ASSET}_{INTERVAL}s',
        broker_name='fake',
        account_mode='REAL',
        day=day,
        asset=ASSET,
        interval_sec=INTERVAL,
        signal_ts=ts,
        decision_action='CALL',
        decision_conf=0.8,
        decision_score=0.2,
        stake_amount=2.0,
        stake_currency='BRL',
        expiry_ts=ts + INTERVAL,
        entry_deadline_utc=now_iso,
        client_order_key=f'client-{ts}',
        intent_state=state,
        broker_status='open' if state == 'accepted_open' else 'unknown',
        created_at_utc=now_iso,
        updated_at_utc=now_iso,
        accepted_at_utc=now_iso if state == 'accepted_open' else None,
        cluster_key='fx',
    )


def _run(argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = control_app.main(argv)
    return code, json.loads(buf.getvalue())


def test_execution_hardening_blocks_multi_asset_live_until_explicitly_enabled(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, allow_multi_asset_live=False, multi_asset_enabled=True)
    monkeypatch.setenv('THALOR_EXECUTION_ALLOW_REAL', '1')

    ctx = build_context(repo_root=tmp_path, config_path=cfg)
    decision = evaluate_execution_hardening(repo_root=tmp_path, ctx=ctx, write_artifact=False)

    assert decision.allowed is False
    assert decision.reason == 'real_multi_asset_not_enabled'


def test_execution_hardening_blocks_when_real_open_positions_limit_is_reached(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, allow_multi_asset_live=True, multi_asset_enabled=False, max_open_positions_total=1)
    monkeypatch.setenv('THALOR_EXECUTION_ALLOW_REAL', '1')
    day, ts = _seed_trade_signal(tmp_path)
    repo = ExecutionRepository(execution_repo_path(tmp_path))
    repo.save_intent(_intent(day=day, ts=ts, state='accepted_open'))

    ctx = build_context(repo_root=tmp_path, config_path=cfg)
    decision = evaluate_execution_hardening(repo_root=tmp_path, ctx=ctx, write_artifact=False)

    assert decision.allowed is False
    assert decision.reason == 'real_open_positions_total_limit'
    assert int(decision.open_positions_total or 0) >= 1


def test_execute_order_payload_includes_execution_hardening_block(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, allow_multi_asset_live=False, multi_asset_enabled=True)
    _seed_trade_signal(tmp_path)
    monkeypatch.setenv('THALOR_EXECUTION_ALLOW_REAL', '1')

    code, payload = _run(['execute-order', '--repo-root', str(tmp_path), '--config', str(cfg), '--json'])

    assert code == 0
    assert payload['blocked_reason'] == 'real_multi_asset_not_enabled'
    hardening = dict(payload.get('execution_hardening') or {})
    assert hardening['allowed'] is False
    assert hardening['reason'] == 'real_multi_asset_not_enabled'
    assert payload.get('submit_attempt') is None


class _AdapterStub:
    def __init__(self, snapshot: BrokerOrderSnapshot) -> None:
        self._snapshot = snapshot

    def fetch_order(self, external_order_id: str):
        return self._snapshot if external_order_id == self._snapshot.external_order_id else None


def test_verify_live_submit_persists_snapshot(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, allow_multi_asset_live=True, multi_asset_enabled=False)
    monkeypatch.setenv('THALOR_EXECUTION_ALLOW_REAL', '1')
    day, ts = _seed_trade_signal(tmp_path)
    repo = ExecutionRepository(execution_repo_path(tmp_path))
    intent = repo.save_intent(_intent(day=day, ts=ts, state='accepted_open'))
    ctx = build_context(repo_root=tmp_path, config_path=cfg)
    snapshot = BrokerOrderSnapshot(
        broker_name='fake',
        account_mode='REAL',
        external_order_id='order-123',
        client_order_key=intent.client_order_key,
        asset=ASSET,
        side='CALL',
        amount=2.0,
        currency='BRL',
        broker_status='open',
        opened_at_utc=datetime.now(tz=UTC).isoformat(timespec='seconds'),
        expires_at_utc=datetime.now(tz=UTC).isoformat(timespec='seconds'),
        closed_at_utc=None,
        gross_payout=None,
        net_pnl=None,
        settlement_status=None,
        estimated_pnl=False,
        raw_snapshot_json='{}',
        last_seen_at_utc=datetime.now(tz=UTC).isoformat(timespec='seconds'),
    )

    result = verify_live_submit(
        repo_root=tmp_path,
        ctx=ctx,
        repo=repo,
        adapter=_AdapterStub(snapshot),
        intent=intent,
        external_order_id='order-123',
    )

    assert result.enabled is True
    assert result.verified is True
    stored = repo.get_broker_order(broker_name='fake', account_mode='REAL', external_order_id='order-123')
    assert stored is not None
    assert stored.asset == ASSET
