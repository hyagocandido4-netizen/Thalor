from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from natbin.control.commands import portfolio_status_payload
from natbin.portfolio.allocator import allocate
from natbin.portfolio.board import build_execution_plan
from natbin.portfolio.correlation import resolve_correlation_group
from natbin.portfolio.latest import write_portfolio_latest_payload
from natbin.portfolio.models import AssetQuota, CandidateDecision, PortfolioQuota, PortfolioScope
from natbin.portfolio.quota import compute_asset_quotas
from natbin.portfolio.runner import load_scopes


def _write_multi_asset_config(repo_root: Path, *, asset_count: int = 6) -> Path:
    assets = [
        ('EURUSD-OTC', None),
        ('GBPUSD-OTC', None),
        ('AUDUSD-OTC', None),
        ('USDJPY-OTC', None),
        ('BTCUSD-OTC', 'crypto'),
        ('XAUUSD-OTC', 'metal'),
    ][:asset_count]
    lines = [
        'version: "2.0"',
        'runtime:',
        '  profile: live_controlled_practice',
        'multi_asset:',
        '  enabled: true',
        '  max_parallel_assets: 3',
        '  stagger_sec: 1.0',
        '  execution_stagger_sec: 2.0',
        '  portfolio_topk_total: 3',
        '  portfolio_hard_max_positions: 2',
        '  portfolio_hard_max_trades_per_day: 4',
        '  portfolio_hard_max_pending_unknown_total: 2',
        '  asset_quota_default_trades_per_day: 2',
        '  asset_quota_default_max_open_positions: 1',
        '  asset_quota_default_max_pending_unknown: 1',
        '  portfolio_hard_max_positions_per_asset: 1',
        '  portfolio_hard_max_positions_per_cluster: 1',
        '  correlation_filter_enable: true',
        '  max_trades_per_cluster_per_cycle: 1',
        '  partition_data_paths: true',
        'execution:',
        '  enabled: false',
        '  mode: disabled',
        '  provider: fake',
        '  account_mode: PRACTICE',
        '  limits:',
        '    max_pending_unknown: 1',
        '    max_open_positions: 1',
        'assets:',
    ]
    for asset, cluster in assets:
        lines.extend([
            f'  - asset: {asset}',
            '    interval_sec: 300',
            '    timezone: UTC',
            '    payout_default: 0.80',
            '    topk_k: 3',
            '    weight: 1.0',
        ])
        if cluster:
            lines.append(f'    cluster_key: {cluster}')
    cfg = repo_root / 'config' / 'multi_asset.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return cfg


def test_resolve_correlation_group_prefers_explicit_cluster_key() -> None:
    assert resolve_correlation_group('EURUSD-OTC', 'fx-major') == 'fx-major'
    assert resolve_correlation_group('EURUSD-OTC', 'default') == 'pair_quote:USD'


def test_compute_asset_quotas_uses_multi_asset_defaults(tmp_path: Path) -> None:
    cfg = _write_multi_asset_config(tmp_path, asset_count=2)
    scopes, _cfg = load_scopes(repo_root=tmp_path, config_path=cfg)

    quotas = compute_asset_quotas(tmp_path, scopes, config_path=cfg)

    assert len(quotas) == 2
    assert all(int(item.max_trades_per_day) == 2 for item in quotas)
    assert all(int(item.max_open_positions) == 1 for item in quotas)
    assert all(int(item.max_pending_unknown) == 1 for item in quotas)
    assert quotas[0].correlation_group == 'pair_quote:USD'


def test_allocator_blocks_auto_correlated_quote_bucket(tmp_path: Path) -> None:
    cfg = _write_multi_asset_config(tmp_path, asset_count=2)
    scopes = [
        PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s', cluster_key='default', correlation_group='pair_quote:USD'),
        PortfolioScope(asset='GBPUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='GBPUSD-OTC_300s', cluster_key='default', correlation_group='pair_quote:USD'),
    ]
    asset_quotas = [
        AssetQuota(scope_tag='EURUSD-OTC_300s', asset='EURUSD-OTC', interval_sec=300, day='2026-03-25', kind='open', reason='', executed_today=0, max_trades_per_day=2, budget_left=2, pending_unknown=0, max_pending_unknown=1, open_positions=0, max_open_positions=1, cluster_key='pair_quote:USD', correlation_group='pair_quote:USD'),
        AssetQuota(scope_tag='GBPUSD-OTC_300s', asset='GBPUSD-OTC', interval_sec=300, day='2026-03-25', kind='open', reason='', executed_today=0, max_trades_per_day=2, budget_left=2, pending_unknown=0, max_pending_unknown=1, open_positions=0, max_open_positions=1, cluster_key='pair_quote:USD', correlation_group='pair_quote:USD'),
    ]
    portfolio_quota = PortfolioQuota(
        day='2026-03-25',
        kind='open',
        reason='',
        executed_today_total=0,
        hard_max_trades_per_day_total=4,
        budget_left_total=4,
        pending_unknown_total=0,
        open_positions_total=0,
        hard_max_positions_total=2,
        open_positions_by_asset={},
        pending_unknown_by_asset={},
        executed_today_by_asset={},
        open_positions_by_cluster={},
        pending_unknown_by_cluster={},
        executed_today_by_cluster={},
        hard_max_positions_per_asset=1,
        hard_max_positions_per_cluster=1,
        correlation_filter_enable=True,
    )
    candidates = [
        CandidateDecision(scope_tag='EURUSD-OTC_300s', asset='EURUSD-OTC', interval_sec=300, day='2026-03-25', ts=1773956100, action='CALL', score=0.8, conf=0.8, ev=0.2, reason='fx', blockers=None, decision_path='runs/decisions/eurusd.json', raw={}),
        CandidateDecision(scope_tag='GBPUSD-OTC_300s', asset='GBPUSD-OTC', interval_sec=300, day='2026-03-25', ts=1773956100, action='PUT', score=0.7, conf=0.7, ev=0.19, reason='fx', blockers=None, decision_path='runs/decisions/gbpusd.json', raw={}),
    ]

    allocation = allocate(str(tmp_path), scopes=scopes, candidates=candidates, asset_quotas=asset_quotas, portfolio_quota=portfolio_quota, config_path=str(cfg))

    assert [item.scope_tag for item in allocation.selected] == ['EURUSD-OTC_300s']
    suppressed = {item.scope_tag: item.reason for item in allocation.suppressed}
    assert suppressed['GBPUSD-OTC_300s'] == 'correlation_cluster_cap:pair_quote:USD'


def test_build_execution_plan_is_monotonic() -> None:
    scopes = [
        PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s', cluster_key='default', correlation_group='pair_quote:USD'),
        PortfolioScope(asset='USDJPY-OTC', interval_sec=300, timezone='UTC', scope_tag='USDJPY-OTC_300s', cluster_key='default', correlation_group='pair_quote:JPY'),
        PortfolioScope(asset='BTCUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='BTCUSD-OTC_300s', cluster_key='crypto', correlation_group='crypto'),
    ]
    plan = build_execution_plan(
        selected=[
            {'scope_tag': 'EURUSD-OTC_300s', 'asset': 'EURUSD-OTC', 'interval_sec': 300},
            {'scope_tag': 'USDJPY-OTC_300s', 'asset': 'USDJPY-OTC', 'interval_sec': 300},
            {'scope_tag': 'BTCUSD-OTC_300s', 'asset': 'BTCUSD-OTC', 'interval_sec': 300},
        ],
        scopes=scopes,
        stagger_sec=2.0,
        now_utc=datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC),
    )

    assert [round(float(item['stagger_delay_sec']), 2) for item in plan] == [0.0, 2.0, 4.0]
    assert [item['correlation_group'] for item in plan] == ['pair_quote:USD', 'pair_quote:JPY', 'crypto']
    assert [item['scheduled_at_utc'] for item in plan] == [
        '2026-03-25T12:00:00+00:00',
        '2026-03-25T12:00:02+00:00',
        '2026-03-25T12:00:04+00:00',
    ]


def test_portfolio_status_payload_includes_unified_asset_board_and_quotas(tmp_path: Path) -> None:
    cfg = _write_multi_asset_config(tmp_path, asset_count=6)

    write_portfolio_latest_payload(
        tmp_path,
        name='portfolio_allocation_latest.json',
        config_path=cfg,
        profile='live_controlled_practice',
        payload={
            'allocation_id': 'alloc_1',
            'at_utc': '2026-03-25T12:00:00+00:00',
            'max_select': 3,
            'selected': [
                {
                    'scope_tag': 'EURUSD-OTC_300s',
                    'asset': 'EURUSD-OTC',
                    'interval_sec': 300,
                    'cluster_key': 'pair_quote:USD',
                    'reason': 'selected',
                    'rank': 1,
                }
            ],
            'suppressed': [],
            'portfolio_quota': {},
            'asset_quotas': [],
            'risk_summary': {},
        },
        write_legacy=False,
    )
    write_portfolio_latest_payload(
        tmp_path,
        name='portfolio_cycle_latest.json',
        config_path=cfg,
        profile='live_controlled_practice',
        payload={
            'cycle_id': 'cycle_1',
            'started_at_utc': '2026-03-25T12:00:00+00:00',
            'finished_at_utc': '2026-03-25T12:00:04+00:00',
            'ok': True,
            'message': 'ok',
            'scopes': [],
            'prepare': [],
            'candidate_results': [],
            'candidates': [
                {
                    'scope_tag': 'EURUSD-OTC_300s',
                    'asset': 'EURUSD-OTC',
                    'interval_sec': 300,
                    'action': 'CALL',
                    'reason': 'selected',
                }
            ],
            'allocation': None,
            'execution': [
                {
                    'scope_tag': 'EURUSD-OTC_300s',
                    'asset': 'EURUSD-OTC',
                    'interval_sec': 300,
                    'outcome': {'returncode': 0},
                    'payload': {'intent': {'intent_state': 'submitted_unknown'}, 'submit_transport_status': 'submitted'},
                }
            ],
            'execution_plan': [
                {
                    'scope_tag': 'EURUSD-OTC_300s',
                    'asset': 'EURUSD-OTC',
                    'interval_sec': 300,
                    'correlation_group': 'pair_quote:USD',
                    'order_index': 0,
                    'stagger_delay_sec': 0.0,
                    'scheduled_at_utc': '2026-03-25T12:00:00+00:00',
                }
            ],
            'errors': [],
        },
        write_legacy=False,
    )

    payload = portfolio_status_payload(repo_root=tmp_path, config_path=cfg)

    assert payload['multi_asset']['asset_count'] == 6
    assert payload['multi_asset']['execution_stagger_sec'] == 2.0
    assert len(payload['asset_quotas']) == 6
    assert len(payload['asset_board']) == 6
    board = {row['scope_tag']: row for row in payload['asset_board']}
    assert board['EURUSD-OTC_300s']['selected'] is True
    assert board['EURUSD-OTC_300s']['execution_intent_state'] == 'submitted_unknown'
    assert board['GBPUSD-OTC_300s']['correlation_group'] == 'pair_quote:USD'
    assert payload['portfolio_quota']['hard_max_positions_total'] == 2
