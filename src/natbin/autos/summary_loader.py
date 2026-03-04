from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..summary_paths import load_daily_summary_checked, repo_timezone_name


@dataclass(frozen=True)
class SummaryScanResult:
    summaries: list[tuple[str, dict]]
    scan: dict[str, Any]

    @property
    def used_days(self) -> list[str]:
        return list(self.scan.get("used_days") or [])

    def has_day(self, day: str) -> bool:
        return day in set(self.used_days)


def collect_checked_summaries(
    *,
    now: datetime,
    lookback_days: int,
    asset: str,
    interval_sec: int,
    runs_dir: Path,
) -> SummaryScanResult:
    out: list[tuple[str, dict]] = []
    expected_tz = repo_timezone_name()
    requested_days: list[str] = []
    used_days: list[str] = []
    missing_days: list[str] = []
    invalid_days: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    strict = True
    legacy_fallback_count = 0
    for i in range(max(1, lookback_days)):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        requested_days.append(day)
        s, spath, meta = load_daily_summary_checked(
            day=day,
            asset=asset,
            interval_sec=interval_sec,
            out_dir=runs_dir,
            expected_timezone=expected_tz,
        )
        strict = bool(meta.get("strict", strict))
        if isinstance(s, dict):
            out.append((day, s))
            used_days.append(day)
            if bool(meta.get("legacy_fallback_used", False)):
                legacy_fallback_count += 1
            sources.append(
                {
                    "day": day,
                    "path": str(spath) if spath else str(meta.get("path") or ""),
                    "source": str(meta.get("source") or "missing"),
                }
            )
            continue
        if str(meta.get("status") or "") == "invalid":
            invalid_days.append(
                {
                    "day": day,
                    "path": str(meta.get("path") or ""),
                    "source": str(meta.get("source") or "invalid"),
                    "issues": meta.get("issues") or [],
                }
            )
        else:
            missing_days.append(day)
    scan = {
        "strict": bool(strict),
        "expected_asset": str(asset),
        "expected_interval_sec": int(interval_sec),
        "expected_timezone": expected_tz,
        "requested_days": requested_days,
        "used_days": used_days,
        "missing_days": missing_days,
        "invalid_days": invalid_days,
        "sources": sources,
        "used_count": len(used_days),
        "missing_count": len(missing_days),
        "invalid_count": len(invalid_days),
        "legacy_fallback_count": int(legacy_fallback_count),
    }
    return SummaryScanResult(summaries=out, scan=scan)
