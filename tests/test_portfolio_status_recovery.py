from __future__ import annotations

import json
from pathlib import Path

from natbin.control.commands import portfolio_status_payload
from natbin.portfolio.latest import write_portfolio_latest_payload
from natbin.portfolio.paths import portfolio_runs_dir


def _write_config(repo: Path, *, name: str, profile: str = "live_controlled_practice") -> Path:
    cfg = repo / "config" / name
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "\n".join(
            [
                'version: "2.0"',
                'runtime:',
                f'  profile: {profile}',
                'multi_asset:',
                '  enabled: false',
                'execution:',
                '  enabled: false',
                '  mode: disabled',
                '  provider: fake',
                'intelligence:',
                '  enabled: true',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def _write_legacy_cycle(path: Path, *, cycle_id: str, config_path: str | None = None, runtime_profile: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'cycle_id': cycle_id,
        'finished_at_utc': '2026-03-11T06:20:34+00:00',
        'config_path': config_path,
        'runtime_profile': runtime_profile,
        'candidates': [],
    }
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def test_portfolio_status_ignores_legacy_cycle_from_other_context(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, name='live_controlled_practice.yaml', profile='live_controlled_practice')
    stale_cfg = _write_config(tmp_path, name='multi_asset.yaml', profile='default')

    legacy_cycle = portfolio_runs_dir(tmp_path) / 'portfolio_cycle_latest.json'
    _write_legacy_cycle(legacy_cycle, cycle_id='stale_cycle', config_path=str(stale_cfg), runtime_profile='default')

    payload = portfolio_status_payload(repo_root=tmp_path, config_path=cfg)

    assert payload['runtime_profile'] == 'live_controlled_practice'
    assert payload['latest_cycle'] is None
    assert payload['latest_sources']['cycle']['source'] == 'legacy_mismatch'
    assert payload['latest_sources']['cycle']['matched'] is False


def test_portfolio_status_prefers_scoped_latest_for_current_profile(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, name='live_controlled_practice.yaml', profile='live_controlled_practice')

    legacy_cycle = portfolio_runs_dir(tmp_path) / 'portfolio_cycle_latest.json'
    _write_legacy_cycle(legacy_cycle, cycle_id='stale_cycle', config_path='config/multi_asset.yaml', runtime_profile='default')

    write_portfolio_latest_payload(
        tmp_path,
        name='portfolio_cycle_latest.json',
        config_path=cfg,
        profile='live_controlled_practice',
        payload={
            'cycle_id': 'practice_cycle',
            'finished_at_utc': '2026-03-22T14:06:28+00:00',
            'candidates': [
                {
                    'scope_tag': 'EURUSD-OTC_300s',
                    'asset': 'EURUSD-OTC',
                    'interval_sec': 300,
                    'action': 'HOLD',
                    'reason': 'regime_block',
                }
            ],
        },
        write_legacy=False,
    )

    payload = portfolio_status_payload(repo_root=tmp_path, config_path=cfg)

    assert payload['latest_cycle'] is not None
    assert payload['latest_cycle']['cycle_id'] == 'practice_cycle'
    assert payload['latest_cycle']['runtime_profile'] == 'live_controlled_practice'
    assert str(payload['latest_cycle']['config_path']).endswith('config/live_controlled_practice.yaml')
    assert payload['latest_sources']['cycle']['source'] == 'scoped'
    assert payload['latest_sources']['cycle']['matched'] is True
