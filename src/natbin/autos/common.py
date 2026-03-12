from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..state.summary_paths import repo_asset, repo_interval_sec, repo_now, repo_timezone_name


@dataclass(frozen=True)
class RepoContext:
    asset: str
    interval_sec: int
    timezone: str
    runs_dir: Path
    now: datetime


def as_float(x: Any, default: float) -> float:
    try:
        if x is None:
            return float(default)
        s = str(x).strip().replace(",", ".")
        if s == "":
            return float(default)
        return float(s)
    except Exception:
        return float(default)


def as_int(x: Any, default: int) -> int:
    try:
        if x is None:
            return int(default)
        s = str(x).strip()
        if s == "":
            return int(default)
        return int(float(s.replace(",", ".")))
    except Exception:
        return int(default)


def as_bool(x: Any, default: bool = False) -> bool:
    if x is None:
        return bool(default)
    s = str(x).strip().lower()
    if s == "":
        return bool(default)
    return s not in ("0", "false", "f", "no", "n", "off")


def write_json_atomic(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def repo_runs_dir() -> Path:
    return Path(os.getenv("RUNS_DIR", "runs")).resolve()


def repo_context(now: datetime | None = None) -> RepoContext:
    now = now or repo_now()
    return RepoContext(
        asset=repo_asset(),
        interval_sec=repo_interval_sec(),
        timezone=repo_timezone_name(),
        runs_dir=repo_runs_dir(),
        now=now,
    )


def today_local(now: datetime | None = None) -> str:
    now = now or repo_now()
    return now.strftime("%Y-%m-%d")


def break_even_from_payout(payout: float) -> float:
    return 1.0 / (1.0 + payout) if payout > 0 else 0.5
