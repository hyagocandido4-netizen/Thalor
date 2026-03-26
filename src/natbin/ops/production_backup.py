from __future__ import annotations

import fnmatch
import hashlib
import json
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..control.plan import build_context
from ..runtime.perf import write_text_if_changed
from ..state.control_repo import write_repo_control_artifact


@dataclass(frozen=True)
class _BackupFile:
    relative_path: str
    absolute_path: Path
    size_bytes: int
    sha256_16: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _sha256_16(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()[:16]


def _posix_rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(value, str(pattern)) for pattern in patterns)


def _iter_candidates(*, repo_root: Path, include_globs: list[str], exclude_globs: list[str]) -> list[_BackupFile]:
    found: dict[str, _BackupFile] = {}
    for pattern in include_globs:
        for match in repo_root.glob(str(pattern)):
            if match.is_dir():
                iterable = (item for item in match.rglob('*') if item.is_file())
            elif match.is_file():
                iterable = [match]
            else:
                continue
            for item in iterable:
                try:
                    rel = _posix_rel(repo_root, item)
                except Exception:
                    continue
                if _matches_any(rel, exclude_globs):
                    continue
                found[rel] = _BackupFile(
                    relative_path=rel,
                    absolute_path=item.resolve(),
                    size_bytes=int(item.stat().st_size),
                    sha256_16=_sha256_16(item),
                )
    return [found[key] for key in sorted(found)]


def _archive_name(*, prefix: str, when: datetime, fmt: str) -> str:
    stamp = when.astimezone(UTC).strftime('%Y%m%d_%H%M%SZ')
    suffix = '.zip' if fmt == 'zip' else '.tar.gz'
    return f'{prefix}_{stamp}{suffix}'


def _manifest_path_for_archive(path: Path) -> Path:
    return path.with_name(path.name + '.json')


def _write_archive(*, output_dir: Path, prefix: str, when: datetime, fmt: str, files: list[_BackupFile]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / _archive_name(prefix=prefix, when=when, fmt=fmt)
    if fmt == 'zip':
        with zipfile.ZipFile(archive_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            for item in files:
                zf.write(item.absolute_path, arcname=item.relative_path)
    else:
        with tarfile.open(archive_path, mode='w:gz') as tf:
            for item in files:
                tf.add(item.absolute_path, arcname=item.relative_path, recursive=False)
    return archive_path


def _prune_archives(*, output_dir: Path, archive_prefix: str, retention_days: int, max_archives: int, now_utc: datetime) -> list[str]:
    archives: list[Path] = []
    for pattern in (f'{archive_prefix}_*.tar.gz', f'{archive_prefix}_*.zip'):
        archives.extend(output_dir.glob(pattern))
    archives = sorted({p.resolve() for p in archives}, key=lambda item: item.stat().st_mtime, reverse=True)
    cutoff = now_utc - timedelta(days=max(1, int(retention_days)))
    removed: list[str] = []
    for idx, path in enumerate(archives):
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if idx >= max(1, int(max_archives)) or modified_at < cutoff:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                continue
            try:
                _manifest_path_for_archive(path).unlink(missing_ok=True)
            except Exception:
                pass
            removed.append(str(path))
    return removed


def build_backup_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    repo = Path(ctx.repo_root).resolve()
    resolved = dict(ctx.resolved_config or {})
    production = dict(resolved.get('production') or {})
    backup_cfg = dict(production.get('backup') or {})

    now_utc = _utc_now()
    output_dir = Path(backup_cfg.get('output_dir') or 'runs/backups')
    if not output_dir.is_absolute():
        output_dir = repo / output_dir
    latest_manifest_path = Path(backup_cfg.get('latest_manifest_path') or 'runs/backups/latest.json')
    if not latest_manifest_path.is_absolute():
        latest_manifest_path = repo / latest_manifest_path

    files = _iter_candidates(
        repo_root=repo,
        include_globs=[str(item) for item in list(backup_cfg.get('include_globs') or [])],
        exclude_globs=[str(item) for item in list(backup_cfg.get('exclude_globs') or [])],
    )
    payload: dict[str, Any] = {
        'ok': True,
        'kind': 'production_backup',
        'generated_at_utc': now_utc.isoformat(timespec='seconds'),
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'profile': str((ctx.resolved_config or {}).get('profile') or 'default'),
        'settings': {
            'enabled': bool(backup_cfg.get('enabled', True)),
            'output_dir': str(output_dir),
            'archive_prefix': str(backup_cfg.get('archive_prefix') or 'thalor_backup'),
            'format': str(backup_cfg.get('format') or 'tar.gz'),
            'interval_minutes': int(backup_cfg.get('interval_minutes') or 60),
            'retention_days': int(backup_cfg.get('retention_days') or 14),
            'max_archives': int(backup_cfg.get('max_archives') or 48),
            'latest_manifest_path': str(latest_manifest_path),
        },
        'selection': {
            'file_count': len(files),
            'total_bytes': sum(int(item.size_bytes) for item in files),
            'files': [
                {
                    'path': item.relative_path,
                    'size_bytes': int(item.size_bytes),
                    'sha256_16': item.sha256_16,
                }
                for item in files
            ],
        },
        'paths': {},
        'pruned_archives': [],
    }
    if not bool(backup_cfg.get('enabled', True)):
        payload.update({'ok': False, 'severity': 'skip', 'reason': 'backup_disabled'})
        write_repo_control_artifact(repo_root=repo, name='backup', payload=payload)
        return payload
    if not files:
        payload.update({'ok': False, 'severity': 'error', 'reason': 'no_backup_files'})
        write_repo_control_artifact(repo_root=repo, name='backup', payload=payload)
        return payload
    if dry_run:
        payload.update({'severity': 'ok', 'reason': 'dry_run'})
        write_repo_control_artifact(repo_root=repo, name='backup', payload=payload)
        return payload

    archive_path = _write_archive(
        output_dir=output_dir,
        prefix=str(backup_cfg.get('archive_prefix') or 'thalor_backup'),
        when=now_utc,
        fmt=str(backup_cfg.get('format') or 'tar.gz'),
        files=files,
    )
    payload['paths'] = {
        'archive': str(archive_path),
        'manifest': str(_manifest_path_for_archive(archive_path)),
        'latest_manifest': str(latest_manifest_path),
        'archive_size_bytes': int(archive_path.stat().st_size),
    }
    manifest_body = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    write_text_if_changed(_manifest_path_for_archive(archive_path), manifest_body, encoding='utf-8')
    latest_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_if_changed(latest_manifest_path, manifest_body, encoding='utf-8')
    payload['pruned_archives'] = _prune_archives(
        output_dir=output_dir,
        archive_prefix=str(backup_cfg.get('archive_prefix') or 'thalor_backup'),
        retention_days=int(backup_cfg.get('retention_days') or 14),
        max_archives=int(backup_cfg.get('max_archives') or 48),
        now_utc=now_utc,
    )
    payload['severity'] = 'ok'
    write_repo_control_artifact(repo_root=repo, name='backup', payload=payload)
    return payload
