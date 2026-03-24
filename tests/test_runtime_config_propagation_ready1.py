from __future__ import annotations

from pathlib import Path

from natbin.ops.lockfile import acquire_lock, release_lock
from natbin.runtime.cycle import build_auto_cycle_plan
from natbin.runtime.daemon import run_once
from natbin.runtime.scope import daemon_lock_path


def _write_cfg(path: Path, *, asset: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                'version: "2.0"',
                'execution:',
                '  enabled: false',
                'assets:',
                f'  - asset: {asset}',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )


def test_run_once_uses_selected_config_scope_for_lock_resolution(tmp_path: Path) -> None:
    _write_cfg(tmp_path / 'config' / 'base.yaml', asset='EURUSD-OTC')
    practice_cfg = tmp_path / 'config' / 'practice.yaml'
    _write_cfg(practice_cfg, asset='GBPUSD-OTC')

    lock_path = daemon_lock_path(asset='GBPUSD-OTC', interval_sec=300, out_dir=tmp_path / 'runs')
    res = acquire_lock(lock_path, owner={'scope_tag': 'GBPUSD-OTC_300s', 'mode': 'test'})
    assert bool(res.acquired) is True
    try:
        payload = run_once(repo_root=tmp_path, config_path=practice_cfg)
    finally:
        release_lock(lock_path)

    assert payload['ok'] is False
    assert payload['lock_mode'] == 'once'
    assert 'runtime_daemon_GBPUSD-OTC_300s.lock' in str(payload['message'])
    assert 'EURUSD-OTC' not in str(payload['message'])


def test_build_auto_cycle_plan_propagates_selected_config_env(tmp_path: Path) -> None:
    practice_cfg = tmp_path / 'config' / 'practice.yaml'
    _write_cfg(practice_cfg, asset='GBPUSD-OTC')

    steps = build_auto_cycle_plan(
        tmp_path,
        config_path=practice_cfg,
        asset='GBPUSD-OTC',
        interval_sec=300,
        topk=1,
        lookback_candles=120,
    )
    assert steps
    for step in steps:
        assert step.env is not None
        assert step.env.get('THALOR_CONFIG_PATH') == str(practice_cfg.resolve())
        assert step.env.get('ASSET') == 'GBPUSD-OTC'
        assert step.env.get('INTERVAL_SEC') == '300'
