from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runtime.perf import load_json_cached, write_text_if_changed


SYNC_DOCS_DIR_REL = Path('docs') / 'canonical_state'
PUBLISHED_MAIN_BASELINE_REL = SYNC_DOCS_DIR_REL / 'published_main_baseline.json'
WORKSPACE_MANIFEST_REL = SYNC_DOCS_DIR_REL / 'workspace_manifest.json'
REPO_CONTROL_DIR_REL = Path('runs') / 'control' / '_repo'
REPO_SYNC_ARTIFACT_REL = REPO_CONTROL_DIR_REL / 'sync.json'
_SYNC_METADATA_PATHS = {
    str(PUBLISHED_MAIN_BASELINE_REL).replace('\\', '/'),
    str(WORKSPACE_MANIFEST_REL).replace('\\', '/'),
}


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _check(name: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {'name': name, 'status': status, 'message': message}
    if extra:
        item.update(extra)
    return item


def _severity_from_checks(checks: list[dict[str, Any]]) -> str:
    if any(str(item.get('status')) == 'error' for item in checks):
        return 'error'
    if any(str(item.get('status')) == 'warn' for item in checks):
        return 'warn'
    return 'ok'


def _normalize_relpath(path: str | Path) -> str:
    return str(path).replace('\\', '/').lstrip('./')


def _should_ignore_sync_metadata(relpath: str | Path) -> bool:
    return _normalize_relpath(relpath) in _SYNC_METADATA_PATHS


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ['git', *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _git_available(repo: Path) -> bool:
    cp = _run_git(repo, '--version')
    return cp is not None and cp.returncode == 0 and (repo / '.git').exists()


def _git_stdout(repo: Path, *args: str) -> str | None:
    cp = _run_git(repo, *args)
    if cp is None or cp.returncode != 0:
        return None
    return str(cp.stdout).strip()


def _git_commit_info(repo: Path, rev: str) -> dict[str, Any] | None:
    raw = _git_stdout(repo, 'show', '-s', '--format=%H%x1f%h%x1f%s%x1f%cI', rev)
    if not raw:
        return None
    parts = raw.split('\x1f')
    if len(parts) != 4:
        return None
    return {
        'sha': parts[0],
        'short_sha': parts[1],
        'subject': parts[2],
        'committed_at_utc': parts[3],
        'rev': rev,
    }


def _git_commit_count(repo: Path, rev: str = 'HEAD') -> int | None:
    raw = _git_stdout(repo, 'rev-list', '--count', rev)
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _status_bucket(xy: str) -> str:
    code = xy.strip()
    if code == '??':
        return 'untracked'
    if 'U' in xy:
        return 'unmerged'
    if 'R' in xy:
        return 'renamed'
    if 'C' in xy:
        return 'copied'
    if 'A' in xy:
        return 'added'
    if 'D' in xy:
        return 'deleted'
    return 'modified'


def _status_categories() -> dict[str, list[str]]:
    return {
        'modified': [],
        'added': [],
        'deleted': [],
        'renamed': [],
        'copied': [],
        'unmerged': [],
        'untracked': [],
    }


def _parse_git_status(repo: Path) -> dict[str, Any]:
    categories = _status_categories()
    entries: list[dict[str, str]] = []
    cp = _run_git(repo, 'status', '--short', '--untracked-files=all')
    if cp is None or cp.returncode != 0:
        return {
            'entries': entries,
            'categories': categories,
            'counts': {k: 0 for k in categories},
            'tracked_dirty_count': 0,
            'dirty': False,
        }
    raw = str(cp.stdout or '')
    for line in raw.splitlines():
        if not line.strip():
            continue
        xy = line[:2]
        path = _normalize_relpath(line[3:].strip()) if len(line) >= 4 else ''
        if not path or _should_ignore_sync_metadata(path):
            continue
        bucket = _status_bucket(xy)
        categories.setdefault(bucket, []).append(path)
        entries.append({'xy': xy, 'path': path, 'bucket': bucket})
    for values in categories.values():
        values.sort()
    counts = {name: len(values) for name, values in categories.items()}
    tracked_dirty_count = sum(count for name, count in counts.items() if name != 'untracked')
    dirty = bool(tracked_dirty_count or counts.get('untracked'))
    return {
        'entries': entries,
        'categories': categories,
        'counts': counts,
        'tracked_dirty_count': tracked_dirty_count,
        'dirty': dirty,
    }


def _path_groups(categories: dict[str, list[str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for paths in categories.values():
        for raw in paths:
            path = _normalize_relpath(raw)
            head = path.split('/', 1)[0] if '/' in path else '(root)'
            grouped.setdefault(head, []).append(path)
    for values in grouped.values():
        values.sort()
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def _package_inventory(repo: Path) -> dict[str, Any]:
    append_files = sorted(
        p.name
        for p in repo.glob('README_PACKAGE_*_APPEND.md')
        if p.is_file()
    )
    return {
        'append_files': append_files,
        'count': len(append_files),
    }


def _build_git_snapshot(repo: Path) -> dict[str, Any]:
    available = _git_available(repo)
    if not available:
        return {
            'available': False,
            'repo_has_dot_git': (repo / '.git').exists(),
            'branch': None,
            'head': None,
            'published_ref': None,
            'origin_url': None,
            'commit_count_head': None,
            'head_matches_published_ref': None,
        }
    branch = _git_stdout(repo, 'rev-parse', '--abbrev-ref', 'HEAD') or 'HEAD'
    head = _git_commit_info(repo, 'HEAD')
    origin_main = _git_commit_info(repo, 'origin/main')
    published_ref = origin_main or head
    return {
        'available': True,
        'repo_has_dot_git': True,
        'branch': branch,
        'head': head,
        'published_ref': published_ref,
        'published_ref_source': 'origin/main' if origin_main is not None else 'HEAD',
        'origin_main': origin_main,
        'origin_url': _git_stdout(repo, 'remote', 'get-url', 'origin'),
        'commit_count_head': _git_commit_count(repo, 'HEAD'),
        'head_matches_published_ref': bool(
            head is not None and published_ref is not None and head.get('sha') == published_ref.get('sha')
        ),
    }


def _workspace_snapshot(repo: Path) -> dict[str, Any]:
    git = _build_git_snapshot(repo)
    status = _parse_git_status(repo) if bool(git.get('available')) else {
        'entries': [],
        'categories': _status_categories(),
        'counts': {k: 0 for k in _status_categories()},
        'tracked_dirty_count': 0,
        'dirty': False,
    }
    inventory = _package_inventory(repo)
    categories = dict(status.get('categories') or _status_categories())
    return {
        'generated_at_utc': _now_utc(),
        'git': git,
        'dirty': bool(status.get('dirty')),
        'tracked_dirty_count': int(status.get('tracked_dirty_count') or 0),
        'status_counts': dict(status.get('counts') or {}),
        'status': categories,
        'path_groups': _path_groups(categories),
        'package_inventory': inventory,
    }


def _published_baseline_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    git = dict(snapshot.get('git') or {})
    published_ref = dict(git.get('published_ref') or {})
    head = dict(git.get('head') or {})
    return {
        'kind': 'published_main_baseline',
        'schema_version': 1,
        'generated_at_utc': _now_utc(),
        'origin_url': git.get('origin_url'),
        'published_ref_source': git.get('published_ref_source'),
        'published_ref': published_ref,
        'head_at_freeze': head,
        'head_matches_published_ref': git.get('head_matches_published_ref'),
        'notes': [
            'published_main_baseline captures the last commit already visible on public main for this workspace.',
            'workspace_manifest captures the local working-tree delta beyond that published ref.',
            'sync metadata files under docs/canonical_state/ are excluded from drift comparison.',
        ],
    }


def _workspace_manifest_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    git = dict(snapshot.get('git') or {})
    return {
        'kind': 'workspace_manifest',
        'schema_version': 1,
        'generated_at_utc': _now_utc(),
        'base_commit': dict(git.get('head') or {}),
        'published_ref': dict(git.get('published_ref') or {}),
        'dirty': bool(snapshot.get('dirty')),
        'tracked_dirty_count': int(snapshot.get('tracked_dirty_count') or 0),
        'status_counts': dict(snapshot.get('status_counts') or {}),
        'status': dict(snapshot.get('status') or _status_categories()),
        'path_groups': dict(snapshot.get('path_groups') or {}),
        'package_inventory': dict(snapshot.get('package_inventory') or {}),
        'notes': [
            'workspace_manifest is intentionally allowed to describe a dirty working tree.',
            'A dirty state is considered canonical when it matches this manifest exactly.',
            'Use runtime_app sync --freeze-docs only after consciously accepting a new workspace state.',
        ],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    write_text_if_changed(path, body, encoding='utf-8')
    return path


def _freeze_sync_documents(repo: Path, snapshot: dict[str, Any]) -> dict[str, str]:
    if not bool((snapshot.get('git') or {}).get('available')):
        raise RuntimeError('git unavailable')
    published = _published_baseline_payload(snapshot)
    manifest = _workspace_manifest_payload(snapshot)
    published_path = _write_json(repo / PUBLISHED_MAIN_BASELINE_REL, published)
    manifest_path = _write_json(repo / WORKSPACE_MANIFEST_REL, manifest)
    return {
        'published_main_baseline': str(published_path),
        'workspace_manifest': str(manifest_path),
    }


def _load_json_dict(path: Path) -> dict[str, Any] | None:
    obj = load_json_cached(path)
    return obj if isinstance(obj, dict) else None




def _write_repo_sync_artifact(*, repo: Path, payload: dict[str, Any]) -> Path:
    path = repo / REPO_SYNC_ARTIFACT_REL
    body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    write_text_if_changed(path, body, encoding='utf-8')
    return path

def _compare_list(current: list[str], frozen: list[str]) -> dict[str, Any]:
    current_set = sorted(set(current))
    frozen_set = sorted(set(frozen))
    extra = sorted(set(current_set) - set(frozen_set))
    missing = sorted(set(frozen_set) - set(current_set))
    return {
        'matches': not extra and not missing,
        'extra': extra,
        'missing': missing,
        'current_count': len(current_set),
        'frozen_count': len(frozen_set),
    }


def _compare_workspace(snapshot: dict[str, Any], frozen_manifest: dict[str, Any] | None) -> dict[str, Any]:
    result = {
        'manifest_present': isinstance(frozen_manifest, dict),
        'workspace_matches_frozen': None,
        'head_matches_frozen_base': None,
        'published_ref_matches_frozen': None,
        'package_inventory_matches': None,
        'workspace_drift': None,
    }
    if not isinstance(frozen_manifest, dict):
        return result
    current_git = dict(snapshot.get('git') or {})
    current_head = dict(current_git.get('head') or {})
    current_published_ref = dict(current_git.get('published_ref') or {})
    frozen_base = dict(frozen_manifest.get('base_commit') or {})
    frozen_published_ref = dict(frozen_manifest.get('published_ref') or {})
    result['head_matches_frozen_base'] = bool(current_head and frozen_base and current_head.get('sha') == frozen_base.get('sha'))
    result['published_ref_matches_frozen'] = bool(
        current_published_ref and frozen_published_ref and current_published_ref.get('sha') == frozen_published_ref.get('sha')
    )

    drift: dict[str, Any] = {}
    current_status = dict(snapshot.get('status') or _status_categories())
    frozen_status = dict(frozen_manifest.get('status') or _status_categories())
    categories = sorted(set(current_status) | set(frozen_status))
    for name in categories:
        compare = _compare_list(list(current_status.get(name) or []), list(frozen_status.get(name) or []))
        if not compare['matches']:
            drift[name] = compare
    current_inventory = list(((snapshot.get('package_inventory') or {}).get('append_files') or []))
    frozen_inventory = list(((frozen_manifest.get('package_inventory') or {}).get('append_files') or []))
    inventory_compare = _compare_list(current_inventory, frozen_inventory)
    result['package_inventory_matches'] = bool(inventory_compare['matches'])
    if not inventory_compare['matches']:
        drift['package_inventory.append_files'] = inventory_compare

    result['workspace_drift'] = drift
    result['workspace_matches_frozen'] = bool(
        result['head_matches_frozen_base'] and result['published_ref_matches_frozen'] and not drift
    )
    return result


def build_sync_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    freeze_docs: bool = False,
    strict: bool = False,
    write_artifact: bool = True,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    snapshot = _workspace_snapshot(repo)
    paths = {
        'published_main_baseline': str((repo / PUBLISHED_MAIN_BASELINE_REL).resolve()),
        'workspace_manifest': str((repo / WORKSPACE_MANIFEST_REL).resolve()),
        'repo_sync_artifact': str((repo / REPO_SYNC_ARTIFACT_REL).resolve()),
    }
    freeze_result: dict[str, str] | None = None
    freeze_error: str | None = None
    if freeze_docs:
        try:
            freeze_result = _freeze_sync_documents(repo, snapshot)
        except Exception as exc:
            freeze_error = f'{type(exc).__name__}:{exc}'

    frozen_published = _load_json_dict(repo / PUBLISHED_MAIN_BASELINE_REL)
    frozen_manifest = _load_json_dict(repo / WORKSPACE_MANIFEST_REL)
    compare = _compare_workspace(snapshot, frozen_manifest)

    current_git = dict(snapshot.get('git') or {})
    current_published_ref = dict(current_git.get('published_ref') or {})
    frozen_published_ref = dict((frozen_published or {}).get('published_ref') or {})
    published_baseline_matches_current = None
    if frozen_published_ref:
        published_baseline_matches_current = bool(
            current_published_ref and current_published_ref.get('sha') == frozen_published_ref.get('sha')
        )

    checks: list[dict[str, Any]] = []
    if bool(current_git.get('available')):
        checks.append(_check('git_repository', 'ok', 'Repositório git detectado', branch=current_git.get('branch')))
    else:
        checks.append(_check('git_repository', 'error', 'git indisponível ou repo sem .git'))

    if freeze_error is not None:
        checks.append(_check('freeze_docs', 'error', 'Falha ao congelar documentos SYNC-1', error=freeze_error))
    elif freeze_result is not None:
        checks.append(_check('freeze_docs', 'ok', 'Documentos SYNC-1 atualizados', files=freeze_result))

    if frozen_published is not None:
        checks.append(_check('published_main_baseline_doc', 'ok', 'published_main_baseline presente', path=paths['published_main_baseline']))
    else:
        checks.append(_check('published_main_baseline_doc', 'error', 'published_main_baseline ausente', path=paths['published_main_baseline']))

    if frozen_manifest is not None:
        checks.append(_check('workspace_manifest_doc', 'ok', 'workspace_manifest presente', path=paths['workspace_manifest']))
    else:
        checks.append(_check('workspace_manifest_doc', 'error', 'workspace_manifest ausente', path=paths['workspace_manifest']))

    if published_baseline_matches_current is True:
        checks.append(_check('published_main_alignment', 'ok', 'Baseline publicada continua alinhada ao workspace atual', published_ref=current_published_ref.get('short_sha')))
    elif published_baseline_matches_current is False:
        checks.append(
            _check(
                'published_main_alignment',
                'error' if strict else 'warn',
                'Baseline publicada divergiu do ref atual do repo',
                frozen_ref=frozen_published_ref.get('short_sha') or frozen_published_ref.get('sha'),
                current_ref=current_published_ref.get('short_sha') or current_published_ref.get('sha'),
            )
        )

    if compare.get('workspace_matches_frozen') is True:
        checks.append(
            _check(
                'workspace_manifest_match',
                'ok',
                'Workspace atual casa exatamente com o manifesto congelado',
                tracked_dirty_count=snapshot.get('tracked_dirty_count'),
                untracked_count=(snapshot.get('status_counts') or {}).get('untracked'),
            )
        )
    elif compare.get('manifest_present'):
        checks.append(
            _check(
                'workspace_manifest_match',
                'error' if strict else 'warn',
                'Workspace atual divergiu do manifesto congelado',
                drift=compare.get('workspace_drift') or {},
            )
        )

    severity = _severity_from_checks(checks)
    ok = severity != 'error'
    recommended_actions: list[str] = []
    if frozen_published is None or frozen_manifest is None:
        recommended_actions.append('Execute `python -m natbin.runtime_app sync --repo-root . --freeze-docs --json` para inicializar os manifests canônicos.')
    if compare.get('workspace_matches_frozen') is False:
        recommended_actions.append('Revise `compare.workspace_drift`; depois de aceitar conscientemente o novo estado, regenere os manifests com `--freeze-docs`.')
    if published_baseline_matches_current is False:
        recommended_actions.append('Confirme se o ref publicado mudou e, se esse novo baseline for o desejado, atualize `published_main_baseline.json` com `--freeze-docs`.')

    payload = {
        'at_utc': _now_utc(),
        'kind': 'sync_state',
        'ok': ok,
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(config_path) if config_path not in (None, '') else None,
        'paths': paths,
        'git': current_git,
        'current_workspace': snapshot,
        'frozen_documents': {
            'published_main_baseline': frozen_published,
            'workspace_manifest': frozen_manifest,
        },
        'compare': {
            'published_baseline_matches_current': published_baseline_matches_current,
            **compare,
        },
        'checks': checks,
        'recommended_actions': recommended_actions,
    }
    if write_artifact:
        artifact_path = _write_repo_sync_artifact(repo=repo, payload=payload)
        payload['paths']['repo_sync_artifact'] = str(artifact_path)
    return payload
