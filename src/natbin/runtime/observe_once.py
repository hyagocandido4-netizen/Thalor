from __future__ import annotations

import argparse
import os
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config.paths import resolve_config_path, resolve_repo_root
from ..runtime.scope import live_signals_csv_path, market_context_path, repo_scope
from ..state.summary_paths import repo_now, repo_timezone_name


@contextmanager
def _patched_env(updates: dict[str, str | None]):
    old = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _pushd(path: Path):
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def prepare_observer_environment(
    *,
    repo_root: str | Path,
    config_path: str | Path | None = None,
    topk: int = 3,
    lookback_candles: int = 2000,
) -> dict[str, str | None]:
    """Build environment overrides for the legacy observer.

    This function must be **scope-aware**.

    When called from the portfolio runner, the child process already receives
    `ASSET` / `INTERVAL_SEC` / `TIMEZONE` via env. When called standalone, we
    fall back to the selected config.
    """

    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    path = resolve_config_path(repo_root=root, config_path=config_path)

    # Defaults from config (works for standalone mode).
    scope_cfg = repo_scope(config_path=str(path), repo_root=root)

    asset = os.getenv('ASSET') or scope_cfg.asset

    interval_raw = os.getenv('INTERVAL_SEC') or str(scope_cfg.interval_sec)
    try:
        interval_sec = int(str(interval_raw).strip())
    except Exception:
        interval_sec = int(scope_cfg.interval_sec)

    tz_name = os.getenv('TIMEZONE') or repo_timezone_name(config_path=str(path), repo_root=root, default='UTC')
    try:
        now_local = datetime.now(tz=ZoneInfo(str(tz_name)))
    except Exception:
        # Defensive: fallback to config-derived timezone.
        now_local = repo_now(config_path=str(path), repo_root=root, default_tz='UTC')
        tz_name = str(getattr(now_local.tzinfo, 'key', str(now_local.tzinfo)))

    day = now_local.strftime('%Y-%m-%d')

    updates: dict[str, str | None] = {
        'GATE_FAIL_CLOSED': os.getenv('GATE_FAIL_CLOSED', '1') or '1',
        'LOOKBACK_CANDLES': str(int(lookback_candles)),
        # Make config v2 selection explicit for legacy modules.
        'THALOR_CONFIG_PATH': str(path),
        # Scope exports.
        'ASSET': str(asset),
        'INTERVAL_SEC': str(int(interval_sec)),
        'TIMEZONE': str(tz_name),
        # Sidecar paths (scope-aware).
        'MARKET_CONTEXT_PATH': str(market_context_path(asset=str(asset), interval_sec=int(interval_sec), out_dir=root / 'runs')),
        'LIVE_SIGNALS_PATH': str(live_signals_csv_path(day=day, asset=str(asset), interval_sec=int(interval_sec), out_dir=root / 'runs')),
    }

    if int(topk) > 0:
        updates['TOPK_K'] = str(int(topk))
    else:
        updates['TOPK_K'] = None

    return updates



def run_observe_once(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    topk: int = 3,
    lookback_candles: int = 2000,
) -> int:
    from ..observe_signal_topk_perday import main as observe_main
    from .execution import process_latest_signal

    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    updates = prepare_observer_environment(
        repo_root=root,
        config_path=config_path,
        topk=topk,
        lookback_candles=lookback_candles,
    )
    with _pushd(root), _patched_env(updates):
        observe_main()
    process_latest_signal(repo_root=root, config_path=config_path)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Run the legacy observer step from the Package M Python control plane')
    p.add_argument('--repo-root', default='.')
    p.add_argument('--config', default=None)
    p.add_argument('--topk', type=int, default=3)
    p.add_argument('--lookback-candles', type=int, default=2000)
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    return run_observe_once(
        repo_root=ns.repo_root,
        config_path=ns.config,
        topk=ns.topk,
        lookback_candles=ns.lookback_candles,
    )


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
