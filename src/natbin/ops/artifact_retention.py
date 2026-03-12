from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..control.plan import build_context
from ..state.control_repo import write_control_artifact


DATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ('daily_summary', re.compile(r'^daily_summary_(\d{8})(?:_[A-Za-z0-9_-]+)?(?:_\d+s)?\.json$')),
    ('live_signals_csv', re.compile(r'^live_signals_v2_(\d{8})(?:_[A-Za-z0-9_-]+)?(?:_\d+s)?\.csv$')),
]

_EFFECTIVE_CONFIG_RE = re.compile(r'^effective_config_(\d{8})_(.+)_(\d{6})\.json$')


@dataclass(frozen=True)
class RetentionCandidate:
    path: str
    category: str
    reason: str
    age_days: float | None
    size_bytes: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _parse_date_from_name(name: str) -> datetime | None:
    for _, pat in DATE_PATTERNS:
        m = pat.match(name)
        if not m:
            continue
        try:
            return datetime.strptime(m.group(1), '%Y%m%d').replace(tzinfo=UTC)
        except Exception:
            return None
    return None


def _age_days_from_stat(path: Path, *, now_utc: datetime) -> float | None:
    try:
        return max(0.0, (now_utc.timestamp() - path.stat().st_mtime) / 86400.0)
    except Exception:
        return None


def _size_bytes(path: Path) -> int | None:
    try:
        return int(path.stat().st_size)
    except Exception:
        return None


def _iter_date_based_candidates(*, runs_dir: Path, cutoff_day: str, now_utc: datetime) -> list[RetentionCandidate]:
    out: list[RetentionCandidate] = []
    if not runs_dir.exists():
        return out
    for path in sorted(runs_dir.iterdir()):
        if not path.is_file():
            continue
        day = _parse_date_from_name(path.name)
        if day is None:
            continue
        if day.date().isoformat() >= cutoff_day:
            continue
        category = 'dated_runtime_file'
        for key, pat in DATE_PATTERNS:
            if pat.match(path.name):
                category = key
                break
        out.append(
            RetentionCandidate(
                path=str(path),
                category=category,
                reason=f'older_than_cutoff_day:{cutoff_day}',
                age_days=_age_days_from_stat(path, now_utc=now_utc),
                size_bytes=_size_bytes(path),
            )
        )
    return out


def _iter_old_files(*, base: Path, patterns: Iterable[str], category: str, cutoff_utc: datetime, now_utc: datetime) -> list[RetentionCandidate]:
    out: list[RetentionCandidate] = []
    if not base.exists():
        return out
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(base.rglob(pattern)):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            except Exception:
                continue
            if mtime >= cutoff_utc:
                continue
            out.append(
                RetentionCandidate(
                    path=str(path),
                    category=category,
                    reason=f'older_than_days:{int((now_utc - cutoff_utc).days or 0)}',
                    age_days=_age_days_from_stat(path, now_utc=now_utc),
                    size_bytes=_size_bytes(path),
                )
            )
    return out


def _iter_effective_config_snapshot_candidates(*, repo_root: Path, keep_latest: int, cutoff_utc: datetime, now_utc: datetime) -> list[RetentionCandidate]:
    config_dir = repo_root / 'runs' / 'config'
    if not config_dir.exists():
        return []
    groups: dict[str, list[Path]] = {}
    for path in sorted(config_dir.glob('effective_config_*.json')):
        if not path.is_file():
            continue
        if '_latest_' in path.name or path.name.startswith('effective_config_latest_'):
            continue
        m = _EFFECTIVE_CONFIG_RE.match(path.name)
        if not m:
            continue
        scope_tag = str(m.group(2))
        groups.setdefault(scope_tag, []).append(path)

    out: list[RetentionCandidate] = []
    for scope_tag, items in groups.items():
        ordered = sorted(items, key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
        keep_set = set(ordered[: max(0, int(keep_latest))])
        for path in ordered[max(0, int(keep_latest)):]:
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            except Exception:
                continue
            if mtime >= cutoff_utc:
                continue
            out.append(
                RetentionCandidate(
                    path=str(path),
                    category='effective_config_snapshot',
                    reason=f'beyond_keep_latest:{int(keep_latest)} for {scope_tag}',
                    age_days=_age_days_from_stat(path, now_utc=now_utc),
                    size_bytes=_size_bytes(path),
                )
            )
        # Safety: if there are very old snapshots that are still inside keep_set, do not delete them.
        _ = keep_set
    return out


def _delete_paths(candidates: list[RetentionCandidate]) -> tuple[int, list[str]]:
    deleted = 0
    errors: list[str] = []
    for item in candidates:
        path = Path(item.path)
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except Exception as exc:
            errors.append(f'{path}:{type(exc).__name__}:{exc}')
    return deleted, errors


def build_retention_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    apply: bool = False,
    days: int | None = None,
    keep_effective_config_snapshots: int = 20,
    list_limit: int = 50,
    write_artifact: bool = True,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    now_utc = _now_utc()
    runtime_cfg = dict(ctx.resolved_config.get('runtime') or {})
    keep_days = max(1, int(days or runtime_cfg.get('runtime_retention_days') or 30))
    cutoff_utc = now_utc - timedelta(days=keep_days)
    cutoff_day = cutoff_utc.date().isoformat()

    runs_dir = repo / 'runs'
    candidates: list[RetentionCandidate] = []
    candidates.extend(_iter_date_based_candidates(runs_dir=runs_dir, cutoff_day=cutoff_day, now_utc=now_utc))
    candidates.extend(_iter_effective_config_snapshot_candidates(repo_root=repo, keep_latest=keep_effective_config_snapshots, cutoff_utc=cutoff_utc, now_utc=now_utc))
    candidates.extend(_iter_old_files(base=runs_dir / 'incidents', patterns=['*.jsonl'], category='incident_stream', cutoff_utc=cutoff_utc, now_utc=now_utc))
    candidates.extend(_iter_old_files(base=runs_dir / 'incidents' / 'reports', patterns=['*.json'], category='incident_report', cutoff_utc=cutoff_utc, now_utc=now_utc))
    candidates.extend(_iter_old_files(base=runs_dir / 'logs', patterns=['*.log', '*.jsonl'], category='runtime_log', cutoff_utc=cutoff_utc, now_utc=now_utc))
    candidates.extend(_iter_old_files(base=runs_dir / 'tests', patterns=['*.json', '*.txt', '*.log'], category='test_report', cutoff_utc=cutoff_utc, now_utc=now_utc))
    candidates.extend(_iter_old_files(base=runs_dir / 'manual_checks', patterns=['*.json', '*.txt', '*.log'], category='manual_check', cutoff_utc=cutoff_utc, now_utc=now_utc))
    candidates.extend(_iter_old_files(base=runs_dir / 'decisions', patterns=['decision_*.json'], category='decision_snapshot', cutoff_utc=cutoff_utc, now_utc=now_utc))

    deduped: dict[str, RetentionCandidate] = {}
    for item in candidates:
        deduped[item.path] = item
    ordered = sorted(deduped.values(), key=lambda item: ((item.category or ''), (item.age_days or 0.0), item.path), reverse=True)

    deleted_total = 0
    delete_errors: list[str] = []
    if apply and ordered:
        deleted_total, delete_errors = _delete_paths(ordered)

    categories: dict[str, int] = {}
    for item in ordered:
        categories[item.category] = categories.get(item.category, 0) + 1

    payload = {
        'at_utc': now_utc.isoformat(timespec='seconds'),
        'kind': 'artifact_retention',
        'ok': len(delete_errors) == 0,
        'apply': bool(apply),
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': ctx.scope.scope_tag,
        },
        'retention_days': keep_days,
        'cutoff_day': cutoff_day,
        'keep_effective_config_snapshots': int(keep_effective_config_snapshots),
        'candidates_total': len(ordered),
        'deleted_total': int(deleted_total),
        'delete_errors': delete_errors,
        'categories': categories,
        'sample_candidates': [item.as_dict() for item in ordered[: max(0, int(list_limit))]],
    }
    if write_artifact:
        write_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='retention', payload=payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Build/apply runtime artifact retention payload')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--days', type=int, default=None)
    ap.add_argument('--keep-effective-config-snapshots', type=int, default=20)
    ap.add_argument('--list-limit', type=int, default=50)
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_retention_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        apply=bool(ns.apply),
        days=ns.days,
        keep_effective_config_snapshots=int(ns.keep_effective_config_snapshots),
        list_limit=int(ns.list_limit),
        write_artifact=True,
    )
    body = json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None)
    print(body)
    return 0 if bool(payload.get('ok', True)) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
