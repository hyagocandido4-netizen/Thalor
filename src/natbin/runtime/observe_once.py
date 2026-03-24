from __future__ import annotations

import argparse
import os
from contextlib import contextmanager
from pathlib import Path

from ..config.paths import resolve_config_path, resolve_repo_root
from .observer_surface import build_observer_environment


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

    The observer/runtime boundary is now shared through
    :mod:`natbin.runtime.observer_surface`, so runtime_app and the observer use
    the same resolved config and legacy env bridge.
    """

    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    path = resolve_config_path(repo_root=root, config_path=config_path)
    return build_observer_environment(
        repo_root=root,
        config_path=path,
        topk=topk,
        lookback_candles=lookback_candles,
    )


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
