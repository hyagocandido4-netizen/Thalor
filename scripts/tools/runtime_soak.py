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

from natbin.runtime.soak import build_runtime_soak_summary


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Run a bounded runtime soak and emit a summary artifact.')
    p.add_argument('--repo-root', default='.')
    p.add_argument('--config', default=None)
    p.add_argument('--asset', default=None)
    p.add_argument('--interval-sec', type=int, default=None)
    p.add_argument('--topk', type=int, default=3)
    p.add_argument('--lookback-candles', type=int, default=2000)
    p.add_argument('--max-cycles', type=int, default=12)
    p.add_argument('--sleep-align-offset-sec', type=int, default=3)
    p.add_argument('--quota-aware-sleep', action='store_true')
    p.add_argument('--precheck-market-context', action='store_true')
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    summary = build_runtime_soak_summary(
        repo_root=ns.repo_root,
        config_path=ns.config,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        topk=ns.topk,
        lookback_candles=ns.lookback_candles,
        max_cycles=ns.max_cycles,
        sleep_align_offset_sec=ns.sleep_align_offset_sec,
        quota_aware_sleep=bool(ns.quota_aware_sleep),
        precheck_market_context=bool(ns.precheck_market_context),
        write_artifact=True,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(summary.get('exit_code') or 0)


if __name__ == '__main__':
    raise SystemExit(main())
