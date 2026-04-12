from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired

import natbin.ops.safe_refresh as module


def test_maybe_heal_market_context_skips_on_synthetic_repo(tmp_path: Path) -> None:
    payload = module.maybe_heal_market_context(
        repo_root=tmp_path,
        config_path=tmp_path / 'config.yaml',
        asset='EURUSD-OTC',
        interval_sec=300,
        max_age_sec=900,
        enabled=True,
        dry_run=False,
    )
    assert payload['status'] == 'skip'
    assert str(payload['message']).startswith('repo_missing_src_natbin')


def test_refresh_market_context_safe_catches_timeout(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / 'src' / 'natbin').mkdir(parents=True)

    def fake_run(*args, **kwargs):
        raise TimeoutExpired(cmd=['python', '-m', 'natbin.refresh_market_context'], timeout=90)

    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    payload = module.refresh_market_context_safe(
        repo_root=tmp_path,
        config_path=tmp_path / 'config.yaml',
        asset='EURUSD-OTC',
        interval_sec=300,
        timeout_sec=90,
    )
    assert payload['kind'] == 'timeout'
    assert payload['timed_out'] is True
    assert payload['returncode'] == 124
