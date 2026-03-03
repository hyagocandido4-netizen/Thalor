from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .summary_paths import daily_summary_path, repo_asset, repo_interval_sec, sanitize_asset


def _scoped_csv_candidates(day_tag: str, asset: str, interval_sec: int, out_dir: Path) -> list[Path]:
    tag = sanitize_asset(asset)
    return [
        out_dir / f"live_signals_v2_{day_tag}_{tag}_{int(interval_sec)}s.csv",
        out_dir / f"live_signals_v2_{day_tag}_{int(interval_sec)}s.csv",
    ]


def _scoped_log_candidates(day_tag: str, asset: str, interval_sec: int, out_dir: Path) -> list[Path]:
    tag = sanitize_asset(asset)
    return [
        out_dir / f"observe_loop_auto_{tag}_{int(interval_sec)}s_{day_tag}.log",
    ]


def _move_if_pair_exists(legacy: Path, scoped: Path, archive_dir: Path) -> bool:
    if not legacy.exists() or not scoped.exists():
        return False
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / legacy.name
    if target.exists():
        # keep the first archived copy; legacy file can be removed
        legacy.unlink(missing_ok=True)
        return True
    shutil.move(str(legacy), str(target))
    return True


def main() -> None:
    runs = Path("runs")
    runs.mkdir(parents=True, exist_ok=True)
    archive_dir = runs / "legacy_global"
    asset = os.getenv("ASSET", "").strip() or repo_asset()
    env_interval = os.getenv("INTERVAL_SEC", "").strip()
    interval_sec = int(env_interval) if env_interval else repo_interval_sec()

    moved: dict[str, int] = {
        "sidecars": 0,
        "daily_summary": 0,
        "live_signals_csv": 0,
        "logs": 0,
    }

    # sidecars
    sidecar_pairs = [
        (runs / "effective_env.json", runs / f"effective_env_{sanitize_asset(asset)}_{interval_sec}s.json"),
        (runs / "market_context.json", runs / f"market_context_{sanitize_asset(asset)}_{interval_sec}s.json"),
        (runs / "observe_loop_auto_status.json", runs / f"observe_loop_auto_status_{sanitize_asset(asset)}_{interval_sec}s.json"),
    ]
    for legacy, scoped in sidecar_pairs:
        if _move_if_pair_exists(legacy, scoped, archive_dir):
            moved["sidecars"] += 1

    # daily summaries
    for legacy in sorted(runs.glob("daily_summary_????????.json")):
        day_tag = legacy.stem.split("_")[-1]
        day = f"{day_tag[:4]}-{day_tag[4:6]}-{day_tag[6:8]}"
        scoped = daily_summary_path(day=day, asset=asset, interval_sec=interval_sec, out_dir=runs)
        if _move_if_pair_exists(legacy, scoped, archive_dir):
            moved["daily_summary"] += 1

    # live_signals csv legacy day files
    for legacy in sorted(runs.glob("live_signals_v2_????????.csv")):
        day_tag = legacy.stem.split("_")[-1]
        candidates = _scoped_csv_candidates(day_tag, asset, interval_sec, runs)
        if any(_move_if_pair_exists(legacy, scoped, archive_dir) for scoped in candidates):
            moved["live_signals_csv"] += 1

    logs_dir = runs / "logs"
    archive_logs = archive_dir / "logs"
    if logs_dir.exists():
        for legacy in sorted(logs_dir.glob("observe_loop_auto_????????.log")):
            day_tag = legacy.stem.split("_")[-1]
            candidates = _scoped_log_candidates(day_tag, asset, interval_sec, logs_dir)
            if any(_move_if_pair_exists(legacy, scoped, archive_logs) for scoped in candidates):
                moved["logs"] += 1

    out = {
        "asset": asset,
        "interval_sec": interval_sec,
        "archive_dir": str(archive_dir),
        "moved": moved,
        "moved_total": int(sum(moved.values())),
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
