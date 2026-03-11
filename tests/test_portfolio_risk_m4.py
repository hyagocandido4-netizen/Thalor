from __future__ import annotations

import json
from pathlib import Path

from natbin.portfolio.allocator import allocate
from natbin.portfolio.models import CandidateDecision, PortfolioScope
from natbin.portfolio.quota import compute_asset_quotas, compute_portfolio_quota
from natbin.runtime.execution_models import OrderIntent
from natbin.state.execution_repo import ExecutionRepository


def _write_config(repo_root: Path) -> Path:
    cfg_path = repo_root / 'config' / 'base.yaml'
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        '\n'.join([
            'version: "2.0"',
            'multi_asset:',
            '  enabled: true',
            '  max_parallel_assets: 2',
            '  portfolio_topk_total: 3',
            '  portfolio_hard_max_positions: 4',
            '  portfolio_hard_max_trades_per_day: 10',
            '  portfolio_hard_max_pending_unknown_total: 2',
            '  portfolio_hard_max_positions_per_asset: 1',
            '  portfolio_hard_max_positions_per_cluster: 1',
            '  correlation_filter_enable: true',
            '  max_trades_per_cluster_per_cycle: 1',
            'execution:',
            '  enabled: false',
            '  limits:',
            '    max_pending_unknown: 1',
            '    max_open_positions: 1',
            'quota:',
            '  hard_max_trades_per_day: 3',
            'assets:',
            '  - asset: EURUSD-OTC',
            '    interval_sec: 300',
            '    timezone: America/Sao_Paulo',
            '    cluster_key: fx',
            '  - asset: EURUSD-OTC',
            '    interval_sec: 60',
            '    timezone: America/Sao_Paulo',
            '    cluster_key: fx',
            '  - asset: USDJPY-OTC',
            '    interval_sec: 300',
            '    timezone: America/Sao_Paulo',
            '    cluster_key: fx',
            '  - asset: BTCUSD-OTC',
            '    interval_sec: 300',
            '    timezone: America/Sao_Paulo',
            '    cluster_key: crypto',
            '',
        ]),
        encoding='utf-8',
    )
    return cfg_path


def _scopes() -> list[PortfolioScope]:
    return [
        PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='America/Sao_Paulo', scope_tag='EURUSD-OTC_300s', cluster_key='fx'),
        PortfolioScope(asset='EURUSD-OTC', interval_sec=60, timezone='America/Sao_Paulo', scope_tag='EURUSD-OTC_60s', cluster_key='fx'),
        PortfolioScope(asset='USDJPY-OTC', interval_sec=300, timezone='America/Sao_Paulo', scope_tag='USDJPY-OTC_300s', cluster_key='fx'),
        PortfolioScope(asset='BTCUSD-OTC', interval_sec=300, timezone='America/Sao_Paulo', scope_tag='BTCUSD-OTC_300s', cluster_key='crypto'),
    ]


def _save_intent(repo_root: Path, *, intent_id: str, scope_tag: str, asset: str, interval_sec: int, day: str, state: str, cluster_key: str) -> None:
    repo = ExecutionRepository(repo_root / 'runs' / 'runtime_execution.sqlite3')
    repo.save_intent(
        OrderIntent(
            intent_id=intent_id,
            scope_tag=scope_tag,
            broker_name='iqoption',
            account_mode='PRACTICE',
            day=day,
            asset=asset,
            interval_sec=int(interval_sec),
            signal_ts=1772492400,
            decision_action='CALL',
            decision_conf=0.61,
            decision_score=0.42,
            stake_amount=2.0,
            stake_currency='BRL',
            expiry_ts=1772492700,
            entry_deadline_utc='2026-03-03T10:00:02+00:00',
            client_order_key=f'ck_{intent_id}',
            intent_state=state,
            broker_status='open' if state == 'accepted_open' else 'unknown',
            created_at_utc='2026-03-03T10:00:00+00:00',
            updated_at_utc='2026-03-03T10:00:00+00:00',
            cluster_key=cluster_key,
            portfolio_score=0.42,
        )
    )


def test_compute_portfolio_quota_blocks_on_global_pending_unknown(tmp_path: Path):
    repo_root = tmp_path / 'repo'
    cfg_path = _write_config(repo_root)
    _save_intent(
        repo_root,
        intent_id='intent_pending_fx',
        scope_tag='EURUSD-OTC_300s',
        asset='EURUSD-OTC',
        interval_sec=300,
        day='2026-03-03',
        state='submitted_unknown',
        cluster_key='fx',
    )

    quota = compute_portfolio_quota(repo_root, _scopes(), config_path=cfg_path)

    assert quota.kind == 'open'
    assert quota.pending_unknown_total == 1
    assert quota.hard_max_pending_unknown_total == 2
    assert quota.budget_left_pending_unknown_total == 1
    assert quota.pending_unknown_by_asset['EURUSD-OTC'] == 1
    assert quota.pending_unknown_by_cluster['fx'] == 1
    assert quota.correlation_filter_enable is True


def test_allocate_respects_asset_and_cluster_exposure_caps(tmp_path: Path):
    repo_root = tmp_path / 'repo'
    cfg_path = _write_config(repo_root)
    scopes = _scopes()

    _save_intent(
        repo_root,
        intent_id='intent_open_fx',
        scope_tag='EURUSD-OTC_300s',
        asset='EURUSD-OTC',
        interval_sec=300,
        day='2026-03-03',
        state='accepted_open',
        cluster_key='fx',
    )

    asset_quotas = compute_asset_quotas(repo_root, scopes, config_path=cfg_path)
    portfolio_quota = compute_portfolio_quota(repo_root, scopes, config_path=cfg_path)

    candidates = [
        CandidateDecision(
            scope_tag='USDJPY-OTC_300s',
            asset='USDJPY-OTC',
            interval_sec=300,
            day='2026-03-03',
            ts=1772492400,
            action='CALL',
            score=0.91,
            conf=0.70,
            ev=0.35,
            reason='best_fx',
            blockers=None,
            decision_path='runs/decisions/usdjpy.json',
            raw={},
        ),
        CandidateDecision(
            scope_tag='EURUSD-OTC_60s',
            asset='EURUSD-OTC',
            interval_sec=60,
            day='2026-03-03',
            ts=1772492400,
            action='CALL',
            score=0.88,
            conf=0.68,
            ev=0.30,
            reason='same_asset_other_tf',
            blockers=None,
            decision_path='runs/decisions/eurusd60.json',
            raw={},
        ),
        CandidateDecision(
            scope_tag='BTCUSD-OTC_300s',
            asset='BTCUSD-OTC',
            interval_sec=300,
            day='2026-03-03',
            ts=1772492400,
            action='PUT',
            score=0.77,
            conf=0.66,
            ev=0.25,
            reason='uncorrelated_cluster',
            blockers=None,
            decision_path='runs/decisions/btc.json',
            raw={},
        ),
    ]

    allocation = allocate(
        str(repo_root),
        scopes=scopes,
        candidates=candidates,
        asset_quotas=asset_quotas,
        portfolio_quota=portfolio_quota,
        config_path=str(cfg_path),
    )

    selected_tags = [item.scope_tag for item in allocation.selected]
    assert selected_tags == ['BTCUSD-OTC_300s']

    suppressed_reasons = {item.scope_tag: item.reason for item in allocation.suppressed}
    assert suppressed_reasons['USDJPY-OTC_300s'] == 'correlation_cluster_cap:fx'
    assert suppressed_reasons['EURUSD-OTC_60s'] == 'asset_exposure_cap:EURUSD-OTC'

    assert len(allocation.suppressed) == 2
    assert allocation.risk_summary['selected_by_cluster'] == {'crypto': 1}
    assert allocation.risk_summary['open_positions_by_cluster'] == {'crypto': 0, 'fx': 1}


def test_allocate_uses_pending_budget_headroom_for_global_quota(tmp_path: Path):
    repo_root = tmp_path / 'repo'
    cfg_path = _write_config(repo_root)
    scopes = _scopes()

    _save_intent(
        repo_root,
        intent_id='intent_pending_crypto',
        scope_tag='BTCUSD-OTC_300s',
        asset='BTCUSD-OTC',
        interval_sec=300,
        day='2026-03-03',
        state='submitted_unknown',
        cluster_key='crypto',
    )

    asset_quotas = compute_asset_quotas(repo_root, scopes, config_path=cfg_path)
    portfolio_quota = compute_portfolio_quota(repo_root, scopes, config_path=cfg_path)

    candidates = [
        CandidateDecision(
            scope_tag='EURUSD-OTC_300s',
            asset='EURUSD-OTC',
            interval_sec=300,
            day='2026-03-03',
            ts=1772492400,
            action='CALL',
            score=0.81,
            conf=0.61,
            ev=0.20,
            reason='fx_a',
            blockers=None,
            decision_path='runs/decisions/fx_a.json',
            raw={},
        ),
        CandidateDecision(
            scope_tag='USDJPY-OTC_300s',
            asset='USDJPY-OTC',
            interval_sec=300,
            day='2026-03-03',
            ts=1772492400,
            action='PUT',
            score=0.80,
            conf=0.60,
            ev=0.19,
            reason='fx_b',
            blockers=None,
            decision_path='runs/decisions/fx_b.json',
            raw={},
        ),
        CandidateDecision(
            scope_tag='EURUSD-OTC_60s',
            asset='EURUSD-OTC',
            interval_sec=60,
            day='2026-03-03',
            ts=1772492400,
            action='CALL',
            score=0.79,
            conf=0.59,
            ev=0.18,
            reason='same_asset',
            blockers=None,
            decision_path='runs/decisions/fx_c.json',
            raw={},
        ),
    ]

    allocation = allocate(
        str(repo_root),
        scopes=scopes,
        candidates=candidates,
        asset_quotas=asset_quotas,
        portfolio_quota=portfolio_quota,
        config_path=str(cfg_path),
    )

    assert len(allocation.selected) == 1
    assert all(item.reason == 'portfolio_capacity_reached' for item in allocation.suppressed if item.scope_tag not in {x.scope_tag for x in allocation.selected})
    assert allocation.portfolio_quota.budget_left_pending_unknown_total == 1
