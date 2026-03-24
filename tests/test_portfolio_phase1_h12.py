from __future__ import annotations

from pathlib import Path

from natbin.portfolio.allocator import allocate
from natbin.portfolio.models import AssetQuota, CandidateDecision, PortfolioQuota, PortfolioScope


def _write_config(repo_root: Path) -> Path:
    cfg_path = repo_root / 'config' / 'base.yaml'
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        '\n'.join([
            'version: "2.0"',
            'multi_asset:',
            '  enabled: true',
            '  portfolio_topk_total: 2',
            '  portfolio_hard_max_positions: 4',
            '  portfolio_hard_max_positions_per_asset: 2',
            '  portfolio_hard_max_positions_per_cluster: 2',
            '  max_trades_per_cluster_per_cycle: 2',
            'execution:',
            '  enabled: false',
            'assets:',
            '  - asset: EURUSD-OTC',
            '    interval_sec: 300',
            '    timezone: UTC',
            '    cluster_key: fx',
            '  - asset: BTCUSD-OTC',
            '    interval_sec: 300',
            '    timezone: UTC',
            '    cluster_key: crypto',
            '',
        ]),
        encoding='utf-8',
    )
    return cfg_path


def test_candidate_rank_prefers_portfolio_score():
    cand = CandidateDecision(
        scope_tag='EURUSD-OTC_300s',
        asset='EURUSD-OTC',
        interval_sec=300,
        day='2026-03-19',
        ts=1773956100,
        action='CALL',
        score=0.9,
        conf=0.9,
        ev=0.5,
        reason='x',
        blockers=None,
        decision_path='runs/decisions/x.json',
        raw={},
        intelligence_score=1.0,
        portfolio_score=0.15,
    )
    assert cand.rank_value() == 0.15


def test_allocate_suppresses_allocator_feedback_block(tmp_path: Path):
    repo_root = tmp_path / 'repo'
    cfg_path = _write_config(repo_root)
    scopes = [
        PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s', cluster_key='fx'),
        PortfolioScope(asset='BTCUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='BTCUSD-OTC_300s', cluster_key='crypto'),
    ]
    asset_quotas = [
        AssetQuota(scope_tag='EURUSD-OTC_300s', asset='EURUSD-OTC', interval_sec=300, day='2026-03-19', kind='open', reason='', executed_today=0, max_trades_per_day=3, budget_left=3, pending_unknown=0, max_pending_unknown=1, open_positions=0, max_open_positions=1, cluster_key='fx'),
        AssetQuota(scope_tag='BTCUSD-OTC_300s', asset='BTCUSD-OTC', interval_sec=300, day='2026-03-19', kind='open', reason='', executed_today=0, max_trades_per_day=3, budget_left=3, pending_unknown=0, max_pending_unknown=1, open_positions=0, max_open_positions=1, cluster_key='crypto'),
    ]
    portfolio_quota = PortfolioQuota(day='2026-03-19', kind='open', reason='', executed_today_total=0, hard_max_trades_per_day_total=10, budget_left_total=10, pending_unknown_total=0, open_positions_total=0, hard_max_positions_total=4)
    candidates = [
        CandidateDecision(
            scope_tag='EURUSD-OTC_300s',
            asset='EURUSD-OTC',
            interval_sec=300,
            day='2026-03-19',
            ts=1773956100,
            action='CALL',
            score=0.8,
            conf=0.7,
            ev=0.2,
            reason='fx',
            blockers=None,
            decision_path='runs/decisions/fx.json',
            raw={},
            intelligence_score=0.5,
            portfolio_score=0.9,
            portfolio_feedback={'allocator_blocked': True, 'block_reason': 'regime_block'},
            regime_level='block',
        ),
        CandidateDecision(
            scope_tag='BTCUSD-OTC_300s',
            asset='BTCUSD-OTC',
            interval_sec=300,
            day='2026-03-19',
            ts=1773956100,
            action='PUT',
            score=0.7,
            conf=0.65,
            ev=0.18,
            reason='crypto',
            blockers=None,
            decision_path='runs/decisions/crypto.json',
            raw={},
            intelligence_score=0.4,
            portfolio_score=0.4,
            portfolio_feedback={'allocator_blocked': False},
            regime_level='ok',
        ),
    ]
    allocation = allocate(str(repo_root), scopes=scopes, candidates=candidates, asset_quotas=asset_quotas, portfolio_quota=portfolio_quota, config_path=str(cfg_path))
    assert [item.scope_tag for item in allocation.selected] == ['BTCUSD-OTC_300s']
    reasons = {item.scope_tag: item.reason for item in allocation.suppressed}
    assert reasons['EURUSD-OTC_300s'] == 'portfolio_feedback_block:regime_block'
    assert allocation.risk_summary['suppressed_feedback_blocks'] == 1
