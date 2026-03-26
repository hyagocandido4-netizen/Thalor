from __future__ import annotations

import tempfile
from pathlib import Path

from natbin.dashboard.analytics import build_dashboard_snapshot
from natbin.dashboard.report import export_dashboard_report
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
                '  profile: dashboard_package_3_smoke',
                'multi_asset:',
                '  enabled: true',
                '  max_parallel_assets: 2',
                'execution:',
                '  enabled: false',
                '  mode: disabled',
                '  provider: fake',
                'dashboard:',
                '  title: Thalor Smoke Deck',
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
    outcomes = [
        ('intent-a', ASSET_A, 'win', 0.6),
        ('intent-b', ASSET_A, 'loss', -2.0),
        ('intent-c', ASSET_B, 'win', 0.7),
    ]
    for idx, (intent_id, asset, outcome, pnl) in enumerate(outcomes):
        created = f'2026-03-25T10:{idx:02d}:00+00:00'
        intent = OrderIntent(
            intent_id=intent_id,
            scope_tag=f'{asset}_{INTERVAL}s',
            broker_name='iqoption',
            account_mode='PRACTICE',
            day='2026-03-25',
            asset=asset,
            interval_sec=INTERVAL,
            signal_ts=1_770_000_000 + idx * INTERVAL,
            decision_action='CALL',
            decision_conf=0.82,
            decision_score=0.11,
            stake_amount=2.0,
            stake_currency='BRL',
            expiry_ts=1_770_000_000 + (idx + 1) * INTERVAL,
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
                latency_ms=120 + idx * 10,
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
                gross_payout=2.0 + pnl,
                net_pnl=pnl,
                settlement_status=outcome,
                estimated_pnl=False,
                raw_snapshot_json='{}',
                last_seen_at_utc=created,
            ),
            intent_id=intent_id,
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_dashboard_3_') as tmp:
        root = Path(tmp)
        cfg = _write_config(root)
        _seed_execution(root)
        snapshot = build_dashboard_snapshot(repo_root=root, config_path=cfg, equity_start=1000.0, max_alerts=20)
        assert snapshot['performance']['trade_count_realized'] == 3
        assert len(snapshot['asset_status']) == 2
        paths = export_dashboard_report(snapshot, output_dir=root / 'runs' / 'reports' / 'dashboard', title='Smoke Deck', export_json=True)
        assert Path(paths['html_path']).exists()
        assert Path(paths['json_path']).exists()
    print('OK dashboard_package_3_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
