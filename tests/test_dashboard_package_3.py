from __future__ import annotations

import pytest
import json
from pathlib import Path

from natbin.dashboard.analytics import build_dashboard_snapshot
from natbin.dashboard.report import export_dashboard_report, main as report_main
from natbin.runtime.execution_models import BrokerOrderSnapshot, OrderIntent, OrderSubmitAttempt
from natbin.state.execution_repo import ExecutionRepository


ASSET_A = 'EURUSD-OTC'
ASSET_B = 'GBPUSD-OTC'
INTERVAL = 300


def _write_config(root: Path) -> Path:
    cfg = root / 'config' / 'multi_asset.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: dashboard_package_3_test',
                'multi_asset:',
                '  enabled: true',
                '  max_parallel_assets: 2',
                '  execution_stagger_sec: 1.0',
                'execution:',
                '  enabled: false',
                '  mode: disabled',
                '  provider: fake',
                'dashboard:',
                '  title: Thalor Test Deck',
                '  default_equity_start: 1000.0',
                'assets:',
                f'  - asset: {ASSET_A}',
                f'    interval_sec: {INTERVAL}',
                '    timezone: UTC',
                f'  - asset: {ASSET_B}',
                f'    interval_sec: {INTERVAL}',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def _seed_execution(root: Path) -> None:
    repo = ExecutionRepository(root / 'runs' / 'runtime_execution.sqlite3')
    base_times = [
        '2026-03-25T10:00:00+00:00',
        '2026-03-25T10:05:00+00:00',
        '2026-03-25T10:10:00+00:00',
    ]
    payloads = [
        ('intent-a', ASSET_A, 'win', 1.6, 0.6, 120),
        ('intent-b', ASSET_A, 'loss', 2.0, -2.0, 180),
        ('intent-c', ASSET_B, 'win', 1.7, 0.7, 140),
    ]
    for idx, (intent_id, asset, outcome, payout, pnl, latency_ms) in enumerate(payloads):
        ts = 1_770_000_000 + idx * INTERVAL
        created = base_times[idx]
        intent = OrderIntent(
            intent_id=intent_id,
            scope_tag=f'{asset}_{INTERVAL}s',
            broker_name='iqoption',
            account_mode='PRACTICE',
            day='2026-03-25',
            asset=asset,
            interval_sec=INTERVAL,
            signal_ts=ts,
            decision_action='CALL',
            decision_conf=0.81,
            decision_score=0.12,
            stake_amount=2.0,
            stake_currency='BRL',
            expiry_ts=ts + INTERVAL,
            entry_deadline_utc=created,
            client_order_key=f'order-{idx}',
            intent_state='settled',
            broker_status='settled',
            settlement_status=outcome,
            external_order_id=f'ext-{idx}',
            submit_attempt_count=1,
            created_at_utc=created,
            updated_at_utc=created,
            submitted_at_utc=created,
            accepted_at_utc=created,
            settled_at_utc=created,
        )
        repo.save_intent(intent)
        repo.record_attempt(
            OrderSubmitAttempt(
                attempt_id=f'att-{idx}',
                intent_id=intent_id,
                attempt_no=1,
                requested_at_utc=created,
                finished_at_utc=created,
                transport_status='acked',
                latency_ms=latency_ms,
                external_order_id=f'ext-{idx}',
                error_code=None,
                error_message=None,
                request_json='{}',
                response_json='{}',
            )
        )
        repo.upsert_broker_snapshot(
            BrokerOrderSnapshot(
                broker_name='iqoption',
                account_mode='PRACTICE',
                external_order_id=f'ext-{idx}',
                client_order_key=f'order-{idx}',
                asset=asset,
                side='call',
                amount=2.0,
                currency='BRL',
                broker_status='settled',
                opened_at_utc=created,
                expires_at_utc=created,
                closed_at_utc=created,
                gross_payout=payout,
                net_pnl=pnl,
                settlement_status=outcome,
                estimated_pnl=False,
                raw_snapshot_json='{}',
                last_seen_at_utc=created,
            ),
            intent_id=intent_id,
        )
        repo.add_event(
            event_id=f'evt-{idx}',
            event_type='order_settled',
            created_at_utc=created,
            intent_id=intent_id,
            broker_name='iqoption',
            account_mode='PRACTICE',
            external_order_id=f'ext-{idx}',
            payload={'intent': {'asset': asset, 'scope_tag': f'{asset}_{INTERVAL}s'}},
        )

    log_path = root / 'runs' / 'logs' / 'account_protection.jsonl'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps({'kind': 'account_protection', 'reason': 'spacing_warn', 'allowed': False, 'checked_at_utc': '2026-03-25T10:15:00+00:00'}) + '\n',
        encoding='utf-8',
    )


def test_build_dashboard_snapshot_computes_metrics_and_assets(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _seed_execution(tmp_path)

    snapshot = build_dashboard_snapshot(repo_root=tmp_path, config_path=cfg, equity_start=1000.0, max_alerts=20)

    perf = snapshot['performance']
    assert perf['trade_count_realized'] == 3
    assert perf['wins'] == 2
    assert perf['losses'] == 1
    assert perf['win_rate'] == pytest.approx(2 / 3, rel=1e-6)
    assert perf['current_equity'] == 999.3
    assert perf['pnl_total'] == -0.7
    assert snapshot['attempt_metrics']['avg_latency_ms'] == 146.67
    assets = {row['asset']: row for row in snapshot['asset_status']}
    assert assets[ASSET_A]['trade_count_realized'] == 2
    assert assets[ASSET_B]['trade_count_realized'] == 1
    assert snapshot['alerts_feed']


def test_export_dashboard_report_writes_html_and_json(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _seed_execution(tmp_path)
    snapshot = build_dashboard_snapshot(repo_root=tmp_path, config_path=cfg, equity_start=1000.0, max_alerts=20)

    paths = export_dashboard_report(snapshot, output_dir=tmp_path / 'runs' / 'reports' / 'dashboard', title='Deck', export_json=True)
    html_path = Path(paths['html_path'])
    json_path = Path(paths['json_path'])
    assert html_path.exists()
    assert json_path.exists()
    assert 'Deck' in html_path.read_text(encoding='utf-8')
    assert 'performance' in json.loads(json_path.read_text(encoding='utf-8'))


def test_dashboard_report_cli_writes_files(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed_execution(tmp_path)
    out_dir = tmp_path / 'runs' / 'reports' / 'dashboard'
    code = report_main([
        '--repo-root', str(tmp_path),
        '--config', str(cfg),
        '--output-dir', str(out_dir),
        '--json',
    ])
    captured = capsys.readouterr().out
    assert code == 0
    payload = json.loads(captured)
    assert Path(payload['html_path']).exists()
    assert Path(payload['json_path']).exists()
