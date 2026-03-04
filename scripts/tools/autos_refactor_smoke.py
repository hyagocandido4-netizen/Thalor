#!/usr/bin/env python
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


def _ok(msg: str) -> None:
    print(f"[autos-smoke][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[autos-smoke][FAIL] {msg}")
    raise SystemExit(2)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.autos.summary_loader import collect_checked_summaries
    from natbin.autos.volume_policy import build_payload
    from natbin.autos.isoblend_policy import compute_meta_iso_blend
    from natbin.autos.hour_policy import compute_hour_threshold
    from natbin.summary_paths import daily_summary_path, repo_asset, repo_interval_sec, repo_timezone

    asset = repo_asset()
    interval_sec = repo_interval_sec()
    tz = repo_timezone()
    now = datetime(2026, 3, 3, 12, 40, tzinfo=tz)

    with tempfile.TemporaryDirectory() as td:
        runs = Path(td)
        today = now.strftime("%Y-%m-%d")
        prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        base = {
            "asset": asset,
            "interval_sec": interval_sec,
            "timezone": str(getattr(tz, "key", tz)),
            "break_even": 1.0 / (1.0 + 0.85),
        }
        today_summary = dict(base)
        today_summary.update({
            "day": today,
            "trades_total": 1,
            "trades_eval_total": 1,
            "wins_eval_total": 1,
            "ev_avg_trades": 0.1,
            "by_hour": {"12": {"count": 1, "wins": 1, "ev_mean": 0.1}},
        })
        prev_summary = dict(base)
        prev_summary.update({
            "day": prev,
            "trades_total": 2,
            "trades_eval_total": 2,
            "wins_eval_total": 1,
            "ev_avg_trades": 0.02,
            "by_hour": {"12": {"count": 2, "wins": 1, "ev_mean": 0.02}},
        })
        daily_summary_path(day=today, asset=asset, interval_sec=interval_sec, out_dir=runs).write_text(json.dumps(today_summary), encoding="utf-8")
        daily_summary_path(day=prev, asset=asset, interval_sec=interval_sec, out_dir=runs).write_text(json.dumps(prev_summary), encoding="utf-8")

        old_env = dict(os.environ)
        try:
            os.environ["RUNS_DIR"] = str(runs)
            os.environ["PAYOUT"] = "0.85"
            os.environ["THRESHOLD"] = "0.02"
            os.environ["META_ISO_BLEND"] = "1"
            scan = collect_checked_summaries(now=now, lookback_days=2, asset=asset, interval_sec=interval_sec, runs_dir=runs)
            if not scan.has_day(today):
                _fail("summary loader did not return current day")
            _ok("summary loader returns strict current-day summaries")

            payload = build_payload(now=now, lookback=2, payout=0.85, scan_result=scan)
            if payload.get("summary_fail_closed"):
                _fail("auto_volume payload unexpectedly fail-closed with valid summaries")
            _ok("auto_volume policy builds payload from shared loader")

            out_iso = compute_meta_iso_blend(now=now)
            if out_iso.get("summary_fail_closed"):
                _fail("auto_isoblend unexpectedly fail-closed with valid summaries")
            _ok("auto_isoblend uses refactored policy layer")

            out_hour = compute_hour_threshold(now=now)
            if out_hour.get("summary_fail_closed"):
                _fail("auto_hourthr unexpectedly fail-closed with valid summaries")
            _ok("auto_hourthr uses refactored policy layer")
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    print("[autos-smoke] ALL OK")


if __name__ == "__main__":
    main()
