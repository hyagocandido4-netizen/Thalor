# Auto volume controller CLI wrapper (Package F)
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .autos.common import as_float, as_int, repo_context, write_json_atomic
from .autos.summary_loader import collect_checked_summaries
from .autos.volume_policy import build_payload
from .summary_paths import auto_params_path


def main() -> None:
    payout = as_float(os.getenv("PAYOUT"), 0.8)
    lookback = as_int(os.getenv("VOL_LOOKBACK_DAYS"), 7)
    ctx = repo_context()
    scan_result = collect_checked_summaries(
        now=ctx.now,
        lookback_days=lookback,
        asset=ctx.asset,
        interval_sec=ctx.interval_sec,
        runs_dir=ctx.runs_dir,
    )
    payload = build_payload(now=ctx.now, lookback=lookback, payout=payout, scan_result=scan_result)
    out_cur = auto_params_path(day=None, asset=ctx.asset, interval_sec=ctx.interval_sec, out_dir=ctx.runs_dir)
    out_hist = auto_params_path(day=ctx.now.strftime('%Y-%m-%d'), asset=ctx.asset, interval_sec=ctx.interval_sec, out_dir=ctx.runs_dir)
    write_json_atomic(out_cur, payload)
    write_json_atomic(out_hist, payload)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
