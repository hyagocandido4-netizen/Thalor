from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Iterable

from ..state.control_repo import write_repo_control_artifact


TOOL_VERSION = 1

TOP_LEVEL_NOISE_DIRS = {
    'test_battery',
    'diag_zips',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.hypothesis',
    '.cache',
    'htmlcov',
    'build',
    'dist',
}
TOP_LEVEL_NOISE_DIR_GLOBS = ('runs_smoke*', 'tmp_*', 'temp_*', 'cache_*', 'artifact_*')
ANYWHERE_NOISE_DIRS = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.hypothesis', '.cache'}
TOP_LEVEL_NOISE_FILE_GLOBS = ('.coverage', 'coverage.xml', 'coverage-*.xml', 'pytest*.xml', 'junit*.xml', 'diag_bundle_*.zip')
ANYWHERE_NOISE_FILE_GLOBS = ('*.pyc', '*.pyo', '*.swp', '*.tmp')
SAFE_REMOVE_REASONS = {'top_level_noise_dir', 'cache_dir', 'egg_info_dir', 'top_level_noise_file', 'cache_file', 'editor_temp'}
SCANNER_EXTRA_SKIP_DIRS = {'test_battery', 'diag_zips', 'htmlcov', 'build', 'dist'}


@dataclass(frozen=True)
class WorkspaceNoiseCandidate:
    path: str
    reason: str
    is_dir: bool
    size_bytes: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _as_posix(rel_path: str | Path) -> str:
    raw = str(rel_path).replace('\\', '/').strip('/')
    return '' if raw in {'', '.'} else raw


def _is_egg_info_dir(name: str) -> bool:
    return str(name).endswith('.egg-info')


def workspace_noise_reason(rel_path: str | Path, *, is_dir: bool = False) -> str | None:
    rel = _as_posix(rel_path)
    if not rel:
        return None
    pp = PurePosixPath(rel)
    parts = pp.parts
    first = parts[0] if parts else ''
    name = pp.name
    if first in TOP_LEVEL_NOISE_DIRS or any(fnmatch.fnmatch(first, pat) for pat in TOP_LEVEL_NOISE_DIR_GLOBS):
        return 'top_level_noise_dir'
    if is_dir:
        if name in ANYWHERE_NOISE_DIRS:
            return 'cache_dir'
        if _is_egg_info_dir(name):
            return 'egg_info_dir'
        return None
    for parent in parts[:-1]:
        if parent in ANYWHERE_NOISE_DIRS:
            return 'cache_file'
        if _is_egg_info_dir(parent):
            return 'egg_info_dir'
    if any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat) for pat in TOP_LEVEL_NOISE_FILE_GLOBS):
        return 'top_level_noise_file'
    if any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat) for pat in ANYWHERE_NOISE_FILE_GLOBS):
        return 'editor_temp' if name.endswith(('.swp', '.tmp')) else 'cache_file'
    return None


def is_workspace_noise(rel_path: str | Path, *, is_dir: bool = False) -> bool:
    return workspace_noise_reason(rel_path, is_dir=is_dir) is not None


def filter_meaningful_paths(paths: Iterable[str | Path]) -> tuple[list[str], list[str]]:
    meaningful: list[str] = []
    noise: list[str] = []
    for item in paths:
        rel = _as_posix(item)
        if not rel:
            continue
        (noise if is_workspace_noise(rel) else meaningful).append(rel)
    return sorted(dict.fromkeys(meaningful)), sorted(dict.fromkeys(noise))


def summarize_workspace_noise_paths(paths: Iterable[str | Path]) -> dict[str, Any]:
    unique = sorted(dict.fromkeys(_as_posix(p) for p in paths if _as_posix(p)))
    by_reason: Counter[str] = Counter()
    by_area: Counter[str] = Counter()
    for rel in unique:
        by_reason[str(workspace_noise_reason(rel) or 'unknown')] += 1
        by_area[rel.split('/', 1)[0] if '/' in rel else rel] += 1
    return {'count': len(unique), 'paths': unique, 'by_reason': dict(sorted(by_reason.items())), 'by_area': dict(sorted(by_area.items()))}


def _size_bytes(path: Path) -> int | None:
    try:
        if path.is_file():
            return int(path.stat().st_size)
        if path.is_dir():
            total = 0
            for item in path.rglob('*'):
                if item.is_file():
                    total += int(item.stat().st_size)
            return total
    except Exception:
        return None
    return None


def find_workspace_hygiene_candidates(repo_root: str | Path = '.') -> list[WorkspaceNoiseCandidate]:
    root = Path(repo_root).resolve()
    hits: dict[str, WorkspaceNoiseCandidate] = {}
    if not root.exists():
        return []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        rel = child.relative_to(root).as_posix()
        reason = workspace_noise_reason(rel, is_dir=child.is_dir())
        if reason in SAFE_REMOVE_REASONS:
            hits[rel] = WorkspaceNoiseCandidate(path=str(child), reason=reason, is_dir=child.is_dir(), size_bytes=_size_bytes(child))
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        rel_dir_posix = '' if rel_dir == Path('.') else rel_dir.as_posix()
        kept_dirs: list[str] = []
        for dirname in dirnames:
            rel = dirname if not rel_dir_posix else f'{rel_dir_posix}/{dirname}'
            reason = workspace_noise_reason(rel, is_dir=True)
            if reason in SAFE_REMOVE_REASONS and rel not in hits:
                path = current / dirname
                hits[rel] = WorkspaceNoiseCandidate(path=str(path), reason=reason, is_dir=True, size_bytes=_size_bytes(path))
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in filenames:
            rel = filename if not rel_dir_posix else f'{rel_dir_posix}/{filename}'
            reason = workspace_noise_reason(rel, is_dir=False)
            if reason in SAFE_REMOVE_REASONS and rel not in hits:
                path = current / filename
                hits[rel] = WorkspaceNoiseCandidate(path=str(path), reason=reason, is_dir=False, size_bytes=_size_bytes(path))
    return sorted(hits.values(), key=lambda item: (item.reason, item.path))


def _delete_candidate(item: WorkspaceNoiseCandidate) -> str | None:
    path = Path(item.path)
    try:
        if item.is_dir:
            shutil.rmtree(path, ignore_errors=False)
        else:
            path.unlink(missing_ok=True)
        return None
    except Exception as exc:
        return f'{path}:{type(exc).__name__}:{exc}'


def build_workspace_hygiene_payload(*, repo_root: str | Path = '.', apply: bool = False, list_limit: int = 50, write_artifact: bool = True) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    candidates = find_workspace_hygiene_candidates(root)
    deleted_total = 0
    delete_errors: list[str] = []
    if apply:
        for item in candidates:
            err = _delete_candidate(item)
            if err is None:
                deleted_total += 1
            else:
                delete_errors.append(err)
    payload = {
        'at_utc': _now_utc(),
        'kind': 'workspace_hygiene',
        'tool': 'natbin.workspace_hygiene',
        'tool_version': TOOL_VERSION,
        'ok': not delete_errors,
        'apply': bool(apply),
        'repo_root': str(root),
        'candidates_total': len(candidates),
        'deleted_total': int(deleted_total),
        'delete_errors': delete_errors,
        'categories': dict(sorted(Counter(item.reason for item in candidates).items())),
        'sample_candidates': [item.as_dict() for item in candidates[: max(0, int(list_limit))]],
        'recommended_actions': [] if not candidates else [
            'O workspace contém ruído gerado por testes/diagnósticos; execute runtime_app workspace-hygiene --apply após revisar os candidates.',
            'Rerode runtime_app sync --json depois da limpeza para medir apenas drift relevante.',
        ],
    }
    if write_artifact:
        write_repo_control_artifact(repo_root=root, name='workspace_hygiene', payload=payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Preview/apply safe cleanup of generated test/diagnostic workspace noise.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--list-limit', type=int, default=50)
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_workspace_hygiene_payload(repo_root=ns.repo_root, apply=bool(ns.apply), list_limit=max(0, int(ns.list_limit or 0)), write_artifact=True)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get('ok', True)) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
