#!/usr/bin/env python
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
import sys
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.portfolio.allocator import allocate
from natbin.portfolio.models import CandidateDecision, PortfolioScope
from natbin.portfolio.quota import compute_asset_quotas, compute_portfolio_quota
from natbin.runtime.execution_models import OrderIntent
from natbin.state.execution_repo import ExecutionRepository


def _ok(msg: str) -> None:
    print(f'[smoke][OK] {msg}')


def _fail(msg: str) -> None:
    print(f'[smoke][FAIL] {msg}')
    raise SystemExit(2)


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


def _save_intent(repo_root: Path, *, intent_id: str, scope_tag: str, asset: str, interval_sec: int, state: str, cluster_key: str) -> None:
    ExecutionRepository(repo_root / 'runs' / 'runtime_execution.sqlite3').save_intent(
        OrderIntent(
            intent_id=intent_id,
            scope_tag=scope_tag,
            broker_name='iqoption',
            account_mode='PRACTICE',
            day='2026-03-03',
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


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix='thalor_m4_portfolio_risk_'))
    try:
        cfg_path = _write_config(tmp)
        scopes = _scopes()

        _save_intent(
            tmp,
            intent_id='intent_open_fx',
            scope_tag='EURUSD-OTC_300s',
            asset='EURUSD-OTC',
            interval_sec=300,
            state='accepted_open',
            cluster_key='fx',
        )

        asset_quotas = compute_asset_quotas(tmp, scopes, config_path=cfg_path)
        portfolio_quota = compute_portfolio_quota(tmp, scopes, config_path=cfg_path)

        if portfolio_quota.open_positions_total != 1:
            _fail(f'expected one open position in portfolio quota, got {portfolio_quota.as_dict()}')
        if portfolio_quota.open_positions_by_cluster.get('fx') != 1:
            _fail(f'expected fx cluster exposure, got {portfolio_quota.as_dict()}')
        _ok('portfolio quota tracks cross-asset exposure')

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
            str(tmp),
            scopes=scopes,
            candidates=candidates,
            asset_quotas=asset_quotas,
            portfolio_quota=portfolio_quota,
            config_path=str(cfg_path),
        )

        selected_tags = [item.scope_tag for item in allocation.selected]
        if selected_tags != ['BTCUSD-OTC_300s']:
            _fail(f'expected only BTC scope selected, got {allocation.as_dict()}')
        reasons = {item.scope_tag: item.reason for item in allocation.suppressed}
        if reasons.get('USDJPY-OTC_300s') != 'correlation_cluster_cap:fx':
            _fail(f'expected correlation cap for USDJPY scope, got {allocation.as_dict()}')
        if reasons.get('EURUSD-OTC_60s') != 'asset_exposure_cap:EURUSD-OTC':
            _fail(f'expected asset exposure cap for EURUSD 60s, got {allocation.as_dict()}')
        _ok('allocator enforces correlation filter + asset exposure caps')

        _save_intent(
            tmp,
            intent_id='intent_pending_crypto',
            scope_tag='BTCUSD-OTC_300s',
            asset='BTCUSD-OTC',
            interval_sec=300,
            state='submitted_unknown',
            cluster_key='crypto',
        )
        quota2 = compute_portfolio_quota(tmp, scopes, config_path=cfg_path)
        if quota2.budget_left_pending_unknown_total != 1:
            _fail(f'expected global pending headroom=1, got {quota2.as_dict()}')
        _ok('portfolio global pending headroom tracked')

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
