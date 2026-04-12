from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENERATED_DIRS = (
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.coverage_cache',
    '__pycache__',
    'diag_zips',
    'provider_recheck',
    'test_battery',
)
GENERATED_FILE_GLOBS = (
    '*.pyc',
    '*.pyo',
    '*.pyd',
    '.coverage',
    'pytest-*.xml',
)
RUNLIKE_DIRS = (
    'runs',
    'data',
)
LEGACY_ROOT_GLOBS = (
    'BACKLOG_PART*.md',
    'README_BACKLOG_PART*.md',
    'PART*.md',
    'PROVIDER_*_README.md',
    'Thalor_*.diff',
    'WINDOWS_EXECUTION_POLICY_HOTFIX.md',
    'README_CAPTURE_BUNDLE.md',
    'README.txt',
    'help.txt',
    '.env.debugbak',
    '.env.off_debug',
)
LEGACY_RELATIVE_PATHS = (
    'docs/canonical_state/published_main_baseline.json',
    'docs/canonical_state/workspace_manifest.json',
)
PROTECTED_PREFIXES = ('secrets', 'secrets/')
EXCLUDED_ROOT_DIRS = {'.git', '.venv', 'secrets', 'src', 'tests', 'scripts', 'config', 'docs', 'configs', '.github'}


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _is_protected(rel_path: str) -> bool:
    rel = str(rel_path).replace('\\', '/').strip('/')
    if not rel:
        return False
    return any(rel == prefix.rstrip('/') or rel.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def _safe_candidates(root: Path) -> list[Path]:
    seen: dict[str, Path] = {}

    for dirname in GENERATED_DIRS + RUNLIKE_DIRS:
        path = root / dirname
        if path.exists() and not _is_protected(dirname):
            seen[_rel(root, path)] = path

    # top-level generated caches/files only
    for pattern in GENERATED_FILE_GLOBS + LEGACY_ROOT_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = _rel(root, path)
            if _is_protected(rel):
                continue
            seen.setdefault(rel, path)

    # recursive __pycache__ only outside critical/protected roots
    for path in sorted(root.rglob('__pycache__')):
        rel = _rel(root, path)
        top = rel.split('/', 1)[0]
        if top in EXCLUDED_ROOT_DIRS or _is_protected(rel):
            continue
        seen.setdefault(rel, path)

    for raw in LEGACY_RELATIVE_PATHS:
        path = root / raw
        if path.exists() and not _is_protected(raw):
            seen.setdefault(_rel(root, path), path)

    return [seen[k] for k in sorted(seen)]


def _remove_path(path: Path) -> str | None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        return None
    except Exception as exc:  # pragma: no cover
        return f'{type(exc).__name__}: {exc}'


def build_rational_workspace_cleanup_payload(*, repo_root: str | Path = '.', apply: bool = True) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    candidates = _safe_candidates(root)
    removed: list[str] = []
    errors: list[dict[str, str]] = []
    missing: list[str] = []

    categories = {'runlike_dirs': 0, 'generated_dirs': 0, 'legacy_or_generated_files': 0}
    for path in candidates:
        rel = _rel(root, path)
        if not path.exists():
            missing.append(rel)
            continue
        top = rel.split('/', 1)[0]
        if top in RUNLIKE_DIRS:
            categories['runlike_dirs'] += 1
        elif top in GENERATED_DIRS or rel.endswith('/__pycache__') or top == '__pycache__':
            categories['generated_dirs'] += 1
        else:
            categories['legacy_or_generated_files'] += 1
        if not apply:
            continue
        err = _remove_path(path)
        if err is None:
            removed.append(rel)
        else:
            errors.append({'path': rel, 'error': err})

    payload = {
        'kind': 'rational_workspace_cleanup',
        'at_utc': _now_utc(),
        'repo_root': str(root),
        'apply': bool(apply),
        'ok': not errors,
        'protected_prefixes': list(PROTECTED_PREFIXES),
        'categories': categories,
        'candidates_total': len(candidates),
        'deleted_total': len(removed),
        'removed': removed,
        'missing': missing,
        'errors': errors,
        'recommended_action': 'workspace_cleanup_applied' if apply else 'review_candidates_then_apply',
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Apply a conservative cleanup pass using stdlib only; safe for systems without project deps installed.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_rational_workspace_cleanup_payload(repo_root=ns.repo_root, apply=not bool(ns.dry_run))
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get('ok', True)) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
