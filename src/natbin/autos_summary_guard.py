from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .summary_paths import load_daily_summary_checked, repo_now, repo_timezone_name


def _truthy(v: str | None, default: bool = False) -> bool:
    if v is None:
        return bool(default)
    s = str(v).strip().lower()
    if s in ("", "0", "false", "f", "no", "n", "off"):
        return False
    return True


def collect_summary_window(
    lookback_days: int,
    *,
    asset: str,
    interval_sec: int,
    runs_dir: str | Path = 'runs',
    now: datetime | None = None,
    config_path: str = 'config.yaml',
    require_today: bool = True,
    allow_legacy_fallback: bool | None = None,
) -> dict[str, Any]:
    now = now or repo_now(config_path=config_path)
    runs_dir = Path(runs_dir)
    tz_name = repo_timezone_name(config_path=config_path, default='UTC')

    valid_records: list[tuple[str, dict[str, Any]]] = []
    valid_days: list[str] = []
    missing_days: list[str] = []
    invalid_days: list[str] = []
    warning_days: list[str] = []
    source_counts: dict[str, int] = {}
    issues_by_day: dict[str, list[str]] = {}
    paths_by_day: dict[str, str] = {}
    today_status = 'missing'
    today_path = ''
    today_issues: list[str] = []

    for i in range(max(1, int(lookback_days))):
        day = (now - timedelta(days=i)).strftime('%Y-%m-%d')
        chk = load_daily_summary_checked(
            day=day,
            asset=asset,
            interval_sec=interval_sec,
            out_dir=runs_dir,
            config_path=config_path,
            allow_legacy_fallback=allow_legacy_fallback,
        )
        if chk.get('path'):
            paths_by_day[day] = str(chk.get('path') or '')
        issues = [str(x) for x in (chk.get('issues') or [])]
        if issues:
            issues_by_day[day] = issues
        if i == 0:
            today_status = str(chk.get('status') or 'missing')
            today_path = str(chk.get('path') or '')
            today_issues = issues

        if chk.get('ok') and isinstance(chk.get('summary'), dict):
            summary = chk['summary']
            valid_records.append((day, summary))
            valid_days.append(day)
            src = str(chk.get('source') or 'exact')
            source_counts[src] = int(source_counts.get(src) or 0) + 1
            if any(iss == 'missing_timezone' for iss in issues):
                warning_days.append(day)
        else:
            status = str(chk.get('status') or '')
            if status == 'missing':
                missing_days.append(day)
            else:
                invalid_days.append(day)

    fail_closed_enabled = _truthy(os.getenv('AUTO_SUMMARY_FAIL_CLOSED', '1'), default=True)
    if today_status != 'ok':
        fail_reason = f'today_summary_{today_status}_fail_closed'
    elif len(valid_records) == 0:
        fail_reason = 'summary_window_empty_fail_closed'
    else:
        fail_reason = ''
    fail_closed = bool(fail_closed_enabled and ((require_today and today_status != 'ok') or len(valid_records) == 0))

    return {
        'records': valid_records,
        'diag': {
            'asset': asset,
            'interval_sec': int(interval_sec),
            'timezone': tz_name,
            'lookback_days': int(max(1, int(lookback_days))),
            'valid_days': valid_days,
            'missing_days': missing_days,
            'invalid_days': invalid_days,
            'warning_days': warning_days,
            'valid_count': len(valid_days),
            'missing_count': len(missing_days),
            'invalid_count': len(invalid_days),
            'warning_count': len(warning_days),
            'source_counts': source_counts,
            'today_status': today_status,
            'today_path': today_path,
            'today_issues': today_issues,
            'issues_by_day': issues_by_day,
            'paths_by_day': paths_by_day,
            'fail_closed_enabled': bool(fail_closed_enabled),
            'fail_closed': bool(fail_closed),
            'fail_reason': fail_reason,
            'require_today': bool(require_today),
        },
    }


def compact_source_counts(diag: dict[str, Any] | None) -> str:
    if not isinstance(diag, dict):
        return ''
    src = diag.get('source_counts')
    if not isinstance(src, dict) or not src:
        return ''
    parts = []
    for k in sorted(src.keys()):
        try:
            parts.append(f"{k}={int(src.get(k) or 0)}")
        except Exception:
            parts.append(f"{k}={src.get(k)}")
    return ','.join(parts)
