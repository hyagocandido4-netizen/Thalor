from __future__ import annotations

import tempfile
from pathlib import Path

from natbin.control.commands import portfolio_status_payload
from natbin.portfolio.board import build_execution_plan
from natbin.portfolio.runner import load_scopes


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'multi_asset.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
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
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '  - asset: GBPUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '  - asset: AUDUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '  - asset: USDJPY-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '  - asset: BTCUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '    cluster_key: crypto',
                '  - asset: XAUUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '    cluster_key: metal',
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_multi_asset_2_') as tmp:
        root = Path(tmp)
        cfg = _write_config(root)
        scopes, _cfg = load_scopes(repo_root=root, config_path=cfg)
        status = portfolio_status_payload(repo_root=root, config_path=cfg)
        plan = build_execution_plan(
            selected=[{'scope_tag': scope.scope_tag, 'asset': scope.asset, 'interval_sec': scope.interval_sec} for scope in scopes[:3]],
            scopes=scopes,
            stagger_sec=2.0,
        )
        assert len(scopes) == 6
        assert status['multi_asset']['asset_count'] == 6
        assert len(status['asset_board']) == 6
        assert len(plan) == 3
        assert [round(float(item['stagger_delay_sec']), 1) for item in plan] == [0.0, 2.0, 4.0]
    print('OK multi_asset_package_2_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
