#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.control.plan import build_context
from natbin.runtime.daemon import run_daemon
from natbin.runtime.hardening import inspect_runtime_freshness
from natbin.state.control_repo import read_control_artifact


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Run a bounded runtime soak and emit a summary artifact.')
    p.add_argument('--repo-root', default='.')
    p.add_argument('--topk', type=int, default=3)
    p.add_argument('--lookback-candles', type=int, default=2000)
    p.add_argument('--max-cycles', type=int, default=12)
    p.add_argument('--sleep-align-offset-sec', type=int, default=3)
    p.add_argument('--quota-aware-sleep', action='store_true')
    p.add_argument('--precheck-market-context', action='store_true')
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    repo_root = Path(ns.repo_root).resolve()
    exit_code = run_daemon(
        repo_root=repo_root,
        topk=ns.topk,
        lookback_candles=ns.lookback_candles,
        max_cycles=ns.max_cycles,
        sleep_align_offset_sec=ns.sleep_align_offset_sec,
        stop_on_failure=True,
        quota_aware_sleep=bool(ns.quota_aware_sleep),
        precheck_market_context=bool(ns.precheck_market_context),
    )
    ctx = build_context(repo_root=repo_root)
    freshness = inspect_runtime_freshness(repo_root=repo_root, ctx=ctx).as_dict()
    guard = read_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='guard')
    lifecycle = read_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='lifecycle')
    loop_status = read_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status')
    summary = {
        'phase': 'runtime_soak',
        'exit_code': int(exit_code),
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': ctx.scope.scope_tag,
        },
        'guard': guard,
        'lifecycle': lifecycle,
        'loop_status': loop_status,
        'freshness': freshness,
    }
    out_dir = repo_root / 'runs' / 'soak'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'soak_latest_{ctx.scope.scope_tag}.json'
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(exit_code)


if __name__ == '__main__':
    raise SystemExit(main())
