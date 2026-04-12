from __future__ import annotations

import argparse
from collections import Counter
import fnmatch
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .release_hygiene import SAFE_PRUNE_GLOBS, build_release_report, should_include_path


TOOL_VERSION = 1
DEFAULT_BASE_REF = 'origin/main'
DEFAULT_MANIFEST_JSON = 'docs/REPO_SYNC_MANIFEST_SYNC1.json'
DEFAULT_MANIFEST_MD = 'docs/REPO_SYNC_MANIFEST_SYNC1.md'


def _now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['git', *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )


def _git_stdout(args: list[str], cwd: Path) -> str | None:
    try:
        cp = _run_git(args, cwd)
    except FileNotFoundError:
        return None
    if cp.returncode != 0:
        return None
    return cp.stdout.strip()


def _git_available(repo_root: Path) -> tuple[bool, str | None, str | None]:
    if shutil.which('git') is None:
        return False, None, 'git_not_installed'
    version = _git_stdout(['--version'], repo_root)
    inside = _git_stdout(['rev-parse', '--is-inside-work-tree'], repo_root)
    if str(inside).lower() != 'true':
        return False, version, 'not_inside_work_tree'
    return True, version, None


def _area_for_path(path: str) -> str:
    rel = str(path).replace('\\', '/').strip('/')
    if not rel:
        return 'repo_root'
    if rel.startswith('README_PACKAGE_'):
        return 'package_readme'
    first = rel.split('/', 1)[0]
    if rel.startswith('README'):
        return 'readme'
    if rel.startswith('.github/'):
        return 'github'
    if first.startswith('.'):
        return 'dotfiles'
    return first


def _change_kind(index_status: str, worktree_status: str) -> str:
    if index_status == '?' and worktree_status == '?':
        return 'untracked'
    if index_status == '!' and worktree_status == '!':
        return 'ignored'
    if 'U' in {index_status, worktree_status} or (index_status == 'A' and worktree_status == 'A') or (index_status == 'D' and worktree_status == 'D'):
        return 'conflicted'
    if index_status == 'R' or worktree_status == 'R':
        return 'renamed'
    if index_status == 'C' or worktree_status == 'C':
        return 'copied'
    if index_status == 'D' or worktree_status == 'D':
        return 'deleted'
    if index_status == 'A' or worktree_status == 'A':
        return 'added'
    if index_status == 'M' or worktree_status == 'M':
        return 'modified'
    if index_status == 'T' or worktree_status == 'T':
        return 'type_changed'
    return 'changed'


def _parse_porcelain(text: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    branch_header = None
    for raw in text.splitlines():
        line = raw.rstrip('\n')
        if not line:
            continue
        if line.startswith('## '):
            branch_header = line[3:].strip()
            continue
        if len(line) < 3:
            continue
        index_status = line[0]
        worktree_status = line[1]
        path = line[3:] if len(line) > 3 else ''
        source_path = None
        if ' -> ' in path and (index_status in {'R', 'C'} or worktree_status in {'R', 'C'}):
            before, after = path.split(' -> ', 1)
            source_path = before
            path = after
        tracked = not (index_status == '?' and worktree_status == '?')
        kind = _change_kind(index_status, worktree_status)
        entries.append(
            {
                'path': path,
                'source_path': source_path,
                'index_status': index_status,
                'worktree_status': worktree_status,
                'tracked': tracked,
                'kind': kind,
                'area': _area_for_path(path),
            }
        )

    staged = sorted({item['path'] for item in entries if item['tracked'] and item['index_status'] not in {' ', '?'}})
    unstaged = sorted({item['path'] for item in entries if item['tracked'] and item['worktree_status'] not in {' ', '?'}})
    tracked_modified = sorted({item['path'] for item in entries if item['tracked'] and item['kind'] != 'ignored'})
    untracked = sorted(item['path'] for item in entries if item['kind'] == 'untracked')
    deleted = sorted(item['path'] for item in entries if item['kind'] == 'deleted')
    renamed = sorted(item['path'] for item in entries if item['kind'] == 'renamed')
    conflicted = sorted(item['path'] for item in entries if item['kind'] == 'conflicted')
    by_area = Counter(_area_for_path(path) for path in [*tracked_modified, *untracked])
    by_kind = Counter(item['kind'] for item in entries)
    return {
        'branch_header': branch_header,
        'entries': entries,
        'summary': {
            'tracked_modified_count': len(tracked_modified),
            'staged_count': len(staged),
            'unstaged_count': len(unstaged),
            'untracked_count': len(untracked),
            'deleted_count': len(deleted),
            'renamed_count': len(renamed),
            'conflicted_count': len(conflicted),
            'dirty': bool(tracked_modified or untracked),
        },
        'staged': staged,
        'unstaged': unstaged,
        'tracked_modified': tracked_modified,
        'untracked': untracked,
        'deleted': deleted,
        'renamed': renamed,
        'conflicted': conflicted,
        'by_area': dict(sorted(by_area.items())),
        'by_kind': dict(sorted(by_kind.items())),
    }


def _parse_name_status(text: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    paths: list[str] = []
    by_area: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split('\t')
        code = parts[0]
        status = code[0] if code else 'M'
        source_path = None
        path = parts[-1] if len(parts) >= 2 else ''
        if status in {'R', 'C'} and len(parts) >= 3:
            source_path = parts[1]
            path = parts[2]
        kind = _change_kind(status, ' ')
        entries.append(
            {
                'status': status,
                'path': path,
                'source_path': source_path,
                'kind': kind,
                'area': _area_for_path(path),
            }
        )
        paths.append(path)
        by_area[_area_for_path(path)] += 1
        by_kind[kind] += 1

    return {
        'count': len(entries),
        'entries': entries,
        'paths': sorted(paths),
        'by_area': dict(sorted(by_area.items())),
        'by_kind': dict(sorted(by_kind.items())),
    }


def _is_noise_path(path: str) -> bool:
    rel = str(path).replace('\\', '/').strip('/')
    if not rel:
        return False
    name = Path(rel).name
    for pattern in SAFE_PRUNE_GLOBS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
            return True
    return not should_include_path(rel, is_dir=False)


def _noise_summary(paths: list[str]) -> dict[str, Any]:
    unique = sorted(dict.fromkeys(paths))
    return {
        'count': len(unique),
        'paths': unique,
        'by_area': dict(sorted(Counter(_area_for_path(path) for path in unique).items())),
    }


def _build_inventory(repo_root: Path, *, changed_paths: list[str]) -> dict[str, Any]:
    append_readmes = sorted(path.name for path in repo_root.glob('README_PACKAGE_*_APPEND.md'))
    docs_markdown = sorted(path.relative_to(repo_root).as_posix() for path in (repo_root / 'docs').glob('*.md')) if (repo_root / 'docs').exists() else []
    smoke_scripts = sorted(path.relative_to(repo_root).as_posix() for path in (repo_root / 'scripts' / 'tools').glob('*smoke*.py')) if (repo_root / 'scripts' / 'tools').exists() else []
    changed = sorted(dict.fromkeys(changed_paths))
    changed_append_readmes = [path for path in changed if Path(path).name.startswith('README_PACKAGE_') and Path(path).name.endswith('_APPEND.md')]
    return {
        'append_readmes': {
            'count': len(append_readmes),
            'items': append_readmes,
        },
        'docs_markdown_count': len(docs_markdown),
        'smoke_script_count': len(smoke_scripts),
        'changed_files_by_area': dict(sorted(Counter(_area_for_path(path) for path in changed).items())),
        'changed_append_readmes': changed_append_readmes,
        'changed_docs': [path for path in changed if path.startswith('docs/')],
        'changed_scripts': [path for path in changed if path.startswith('scripts/')],
        'changed_src_modules': [path for path in changed if path.startswith('src/')],
        'changed_tests': [path for path in changed if path.startswith('tests/')],
        'changed_configs': [path for path in changed if path.startswith('config/') or path.startswith('configs/')],
    }


def _build_recommendations(payload: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    git = dict(payload.get('git') or {})
    worktree = dict(payload.get('worktree') or {})
    divergence = dict(payload.get('divergence') or {})
    inventory = dict(payload.get('inventory') or {})
    status = str(payload.get('status') or 'unknown')

    if not bool(git.get('available')):
        recommendations.append('Extraia este bundle sobre um checkout git existente para recuperar branch/refs e rerode o snapshot.')
        return recommendations

    if status == 'conflicted':
        recommendations.append('Resolva conflitos git antes de iniciar o próximo package.')
    if bool(worktree.get('noise_only_dirty')):
        recommendations.append('Workspace dirty apenas com ruído safe-prune; rode workspace-hygiene/release-hygiene cleanup antes do próximo commit.')
    elif bool(worktree.get('dirty')):
        recommendations.append('Congele o working tree atual com manifest + commit/tag local antes de continuar a refatoração.')
    if int(divergence.get('ahead') or 0) > 0 or int(divergence.get('behind') or 0) > 0:
        recommendations.append(f"Revise a divergência entre HEAD e {divergence.get('base_ref') or DEFAULT_BASE_REF} antes do próximo merge/publish.")
    if not recommendations:
        recommendations.append('Workspace limpo/alinhado; pronto para seguir para o próximo package técnico.')
    if inventory.get('changed_append_readmes'):
        recommendations.append('Há packages locais materializados fora do histórico público; preserve os append docs como trilha de contexto.')
    return recommendations[:5]


def _fingerprint(payload: dict[str, Any]) -> str:
    material = {
        'status': payload.get('status'),
        'repo_root': payload.get('repo_root'),
        'git': payload.get('git'),
        'head': payload.get('head'),
        'base_ref': payload.get('base_ref'),
        'divergence': payload.get('divergence'),
        'worktree': payload.get('worktree'),
        'inventory': payload.get('inventory'),
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


def render_repo_sync_markdown(payload: dict[str, Any]) -> str:
    git = dict(payload.get('git') or {})
    head = dict(payload.get('head') or {})
    base_ref = dict(payload.get('base_ref') or {})
    divergence = dict(payload.get('divergence') or {})
    worktree = dict(payload.get('worktree') or {})
    inventory = dict(payload.get('inventory') or {})
    release_hygiene = dict(payload.get('release_hygiene') or {})

    def _lines(title: str, items: list[str]) -> list[str]:
        lines = [f'## {title}', '']
        if not items:
            lines.append('- nenhum')
            lines.append('')
            return lines
        lines.extend(f'- `{item}`' for item in items)
        lines.append('')
        return lines

    out: list[str] = [
        '# SYNC-1 — Repo Baseline Manifest',
        '',
        f"- Gerado em: `{payload.get('generated_at_utc')}`",
        f"- Repo root: `{payload.get('repo_root')}`",
        f"- Severity: `{payload.get('severity')}`",
        f"- Status: `{payload.get('status')}`",
        f"- Fingerprint: `{payload.get('fingerprint')}`",
        '',
        '## Git context',
        '',
        f"- Git disponível: `{git.get('available')}`",
        f"- Branch atual: `{git.get('branch')}`",
        f"- HEAD: `{head.get('short_sha')}` — {head.get('subject') or ''}",
        f"- Base ref: `{divergence.get('base_ref')}` → `{base_ref.get('short_sha')}`",
        f"- Ahead/behind: `{divergence.get('ahead')}` / `{divergence.get('behind')}`",
        f"- Base ref resolvida: `{base_ref.get('exists')}`",
        '',
        '## Working tree summary',
        '',
        f"- dirty: `{worktree.get('dirty')}`",
        f"- tracked_modified_count: `{worktree.get('tracked_modified_count')}`",
        f"- staged_count: `{worktree.get('staged_count')}`",
        f"- unstaged_count: `{worktree.get('unstaged_count')}`",
        f"- untracked_count: `{worktree.get('untracked_count')}`",
        f"- conflicted_count: `{worktree.get('conflicted_count')}`",
        '',
        '## Working tree by area',
        '',
    ]

    by_area = dict(worktree.get('by_area') or {})
    if by_area:
        out.extend(f'- `{key}`: {value}' for key, value in by_area.items())
    else:
        out.append('- nenhum')
    out.append('')

    out.extend(
        [
            '## Inventory',
            '',
            f"- README_PACKAGE_*_APPEND.md: `{((inventory.get('append_readmes') or {}).get('count'))}`",
            f"- docs/*.md: `{inventory.get('docs_markdown_count')}`",
            f"- scripts/tools/*smoke*.py: `{inventory.get('smoke_script_count')}`",
            '',
        ]
    )

    out.extend(_lines('Changed package append readmes', list(inventory.get('changed_append_readmes') or [])))
    out.extend(_lines('Tracked modified files', list(worktree.get('tracked_modified') or [])))
    out.extend(_lines('Untracked files', list(worktree.get('untracked') or [])))

    committed_delta = dict(payload.get('committed_delta') or {})
    if committed_delta:
        out.extend(
            [
                '## Committed delta vs base ref',
                '',
                f"- count: `{committed_delta.get('count')}`",
                '',
            ]
        )
        out.extend(_lines('Committed delta paths', list(committed_delta.get('paths') or [])))

    out.extend(
        [
            '## Release hygiene snapshot',
            '',
            f"- ok: `{release_hygiene.get('ok')}`",
            f"- included_files: `{release_hygiene.get('included_files')}`",
            f"- included_bytes: `{release_hygiene.get('included_bytes')}`",
            '',
            '## Recommendations',
            '',
        ]
    )
    recommendations = list(payload.get('recommendations') or [])
    if recommendations:
        out.extend(f'- {item}' for item in recommendations)
    else:
        out.append('- nenhuma')
    out.append('')
    return '\n'.join(out)


def write_repo_sync_manifest(
    payload: dict[str, Any],
    *,
    repo_root: str | Path = '.',
    json_path: str | Path | None = None,
    md_path: str | Path | None = None,
) -> dict[str, str | None]:
    root = Path(repo_root).resolve()
    json_target = root / (str(json_path or DEFAULT_MANIFEST_JSON))
    md_target = root / (str(md_path or DEFAULT_MANIFEST_MD))
    json_target.parent.mkdir(parents=True, exist_ok=True)
    md_target.parent.mkdir(parents=True, exist_ok=True)
    manifest_paths = {
        'json': str(json_target),
        'markdown': str(md_target),
    }
    payload_to_write = dict(payload)
    payload_to_write['manifest_paths'] = manifest_paths
    json_target.write_text(json.dumps(payload_to_write, indent=2, ensure_ascii=False), encoding='utf-8')
    md_target.write_text(render_repo_sync_markdown(payload_to_write), encoding='utf-8')
    return manifest_paths


def build_repo_sync_payload(
    *,
    repo_root: str | Path = '.',
    base_ref: str = DEFAULT_BASE_REF,
    write_manifest: bool = False,
    manifest_json_path: str | Path | None = None,
    manifest_md_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    available, version, availability_reason = _git_available(root)
    payload: dict[str, Any] = {
        'kind': 'repo_sync',
        'tool': 'natbin.repo_sync',
        'tool_version': TOOL_VERSION,
        'generated_at_utc': _now_utc(),
        'repo_root': str(root),
        'status': 'no_git',
        'severity': 'ok',
        'ok': True,
        'git': {
            'available': available,
            'version': version,
            'availability_reason': availability_reason,
            'branch': None,
        },
        'head': {},
        'base_ref': {
            'name': str(base_ref or DEFAULT_BASE_REF),
            'exists': False,
        },
        'divergence': {
            'base_ref': str(base_ref or DEFAULT_BASE_REF),
            'ahead': 0,
            'behind': 0,
        },
        'worktree': {
            'dirty': False,
            'tracked_modified_count': 0,
            'staged_count': 0,
            'unstaged_count': 0,
            'untracked_count': 0,
            'deleted_count': 0,
            'renamed_count': 0,
            'conflicted_count': 0,
            'tracked_modified': [],
            'staged': [],
            'unstaged': [],
            'untracked': [],
            'deleted': [],
            'renamed': [],
            'conflicted': [],
            'by_area': {},
            'by_kind': {},
        },
        'committed_delta': {
            'count': 0,
            'entries': [],
            'paths': [],
            'by_area': {},
            'by_kind': {},
        },
        'inventory': {},
        'release_hygiene': None,
        'manifest_paths': {'json': None, 'markdown': None},
        'recommendations': [],
        'fingerprint': None,
    }

    if available:
        branch = _git_stdout(['rev-parse', '--abbrev-ref', 'HEAD'], root)
        head_sha = _git_stdout(['rev-parse', 'HEAD'], root)
        head_short = _git_stdout(['rev-parse', '--short', 'HEAD'], root)
        head_subject = _git_stdout(['show', '-s', '--format=%s', 'HEAD'], root)
        head_date = _git_stdout(['show', '-s', '--format=%cI', 'HEAD'], root)
        payload['git']['branch'] = branch
        payload['head'] = {
            'sha': head_sha,
            'short_sha': head_short,
            'subject': head_subject,
            'committed_at_utc': head_date,
        }

        porcelain = _git_stdout(['status', '--short', '--branch', '--porcelain=v1', '-uall'], root) or ''
        parsed = _parse_porcelain(porcelain)
        dirty_paths = sorted(dict.fromkeys([*parsed['tracked_modified'], *parsed['untracked']]))
        noise_paths = [path for path in dirty_paths if _is_noise_path(path)]
        meaningful_paths = [path for path in dirty_paths if path not in set(noise_paths)]
        payload['worktree'] = {
            **parsed['summary'],
            'tracked_modified': parsed['tracked_modified'],
            'staged': parsed['staged'],
            'unstaged': parsed['unstaged'],
            'untracked': parsed['untracked'],
            'deleted': parsed['deleted'],
            'renamed': parsed['renamed'],
            'conflicted': parsed['conflicted'],
            'by_area': parsed['by_area'],
            'by_kind': parsed['by_kind'],
            'branch_header': parsed['branch_header'],
            'entries': parsed['entries'],
            'noise': _noise_summary(noise_paths),
            'meaningful_paths': sorted(meaningful_paths),
            'noise_only_dirty': bool(parsed['summary'].get('dirty')) and not meaningful_paths,
            'meaningful_dirty': bool(meaningful_paths),
        }

        base_sha = _git_stdout(['rev-parse', str(base_ref)], root)
        base_short = _git_stdout(['rev-parse', '--short', str(base_ref)], root)
        base_subject = _git_stdout(['show', '-s', '--format=%s', str(base_ref)], root) if base_sha else None
        base_date = _git_stdout(['show', '-s', '--format=%cI', str(base_ref)], root) if base_sha else None
        payload['base_ref'] = {
            'name': str(base_ref or DEFAULT_BASE_REF),
            'exists': bool(base_sha),
            'sha': base_sha,
            'short_sha': base_short,
            'subject': base_subject,
            'committed_at_utc': base_date,
        }

        if base_sha:
            counts_raw = _git_stdout(['rev-list', '--left-right', '--count', f'{base_ref}...HEAD'], root)
            behind = 0
            ahead = 0
            if counts_raw:
                parts = counts_raw.split()
                if len(parts) >= 2:
                    behind = int(parts[0] or 0)
                    ahead = int(parts[1] or 0)
            payload['divergence'] = {
                'base_ref': str(base_ref or DEFAULT_BASE_REF),
                'ahead': ahead,
                'behind': behind,
                'merge_base': _git_stdout(['merge-base', 'HEAD', str(base_ref)], root),
            }
            committed_delta_raw = _git_stdout(['diff', '--name-status', f'{base_ref}...HEAD'], root) or ''
            payload['committed_delta'] = _parse_name_status(committed_delta_raw)
        else:
            payload['divergence'] = {
                'base_ref': str(base_ref or DEFAULT_BASE_REF),
                'ahead': 0,
                'behind': 0,
                'merge_base': None,
            }

        if payload['worktree']['conflicted_count']:
            payload['status'] = 'conflicted'
            payload['severity'] = 'error'
            payload['ok'] = False
        elif payload['worktree']['dirty']:
            payload['status'] = 'dirty'
            payload['severity'] = 'warn'
        elif int(payload['divergence']['ahead'] or 0) > 0 or int(payload['divergence']['behind'] or 0) > 0:
            payload['status'] = 'diverged'
            payload['severity'] = 'warn'
        else:
            payload['status'] = 'clean'
            payload['severity'] = 'ok'
    changed_paths = [
        *list((payload.get('worktree') or {}).get('tracked_modified') or []),
        *list((payload.get('worktree') or {}).get('untracked') or []),
        *list((payload.get('committed_delta') or {}).get('paths') or []),
    ]
    payload['inventory'] = _build_inventory(root, changed_paths=changed_paths)
    try:
        release = build_release_report(root)
        payload['release_hygiene'] = release.as_dict()
    except Exception as exc:  # pragma: no cover - defensive only
        payload['release_hygiene'] = {
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }
        if payload['severity'] == 'ok':
            payload['severity'] = 'warn'
    payload['recommendations'] = _build_recommendations(payload)
    payload['fingerprint'] = _fingerprint(payload)
    if write_manifest:
        payload['manifest_paths'] = write_repo_sync_manifest(
            payload,
            repo_root=root,
            json_path=manifest_json_path,
            md_path=manifest_md_path,
        )
    return payload


def _print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(f"[repo_sync] severity={payload.get('severity')} status={payload.get('status')}")
    print(f"[repo_sync] repo_root={payload.get('repo_root')}")
    head = dict(payload.get('head') or {})
    if head.get('short_sha'):
        print(f"[repo_sync] head={head.get('short_sha')} {head.get('subject') or ''}".rstrip())
    worktree = dict(payload.get('worktree') or {})
    print(
        '[repo_sync] dirty=' + str(worktree.get('dirty')) +
        f" tracked_modified={worktree.get('tracked_modified_count')}" +
        f" untracked={worktree.get('untracked_count')}"
    )
    divergence = dict(payload.get('divergence') or {})
    print(
        '[repo_sync] base_ref=' + str(divergence.get('base_ref')) +
        f" ahead={divergence.get('ahead')} behind={divergence.get('behind')}"
    )
    manifest = dict(payload.get('manifest_paths') or {})
    if manifest.get('json'):
        print(f"[repo_sync] manifest_json={manifest.get('json')}")
    if manifest.get('markdown'):
        print(f"[repo_sync] manifest_md={manifest.get('markdown')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Build an offline git workspace snapshot / manifest (SYNC-1).')
    parser.add_argument('--repo-root', default='.', help='repo root (default: current directory)')
    parser.add_argument('--base-ref', default=DEFAULT_BASE_REF, help='git ref used as baseline (default: origin/main)')
    parser.add_argument('--write-manifest', action='store_true', help='write JSON + Markdown manifest files under docs/')
    parser.add_argument('--manifest-json-path', default=DEFAULT_MANIFEST_JSON, help='output path for JSON manifest')
    parser.add_argument('--manifest-md-path', default=DEFAULT_MANIFEST_MD, help='output path for Markdown manifest')
    parser.add_argument('--strict-clean', action='store_true', help='exit non-zero when worktree is dirty or diverged from base-ref')
    parser.add_argument('--strict-base-ref', action='store_true', help='exit non-zero when base-ref is unresolved')
    parser.add_argument('--json', action='store_true', help='print payload as JSON')
    args = parser.parse_args(argv)

    payload = build_repo_sync_payload(
        repo_root=args.repo_root,
        base_ref=str(args.base_ref),
        write_manifest=bool(args.write_manifest),
        manifest_json_path=args.manifest_json_path,
        manifest_md_path=args.manifest_md_path,
    )
    _print_payload(payload, as_json=bool(args.json))

    if bool(args.strict_base_ref) and not bool((payload.get('base_ref') or {}).get('exists')):
        return 2
    if bool(args.strict_clean) and str(payload.get('status')) not in {'clean', 'no_git'}:
        return 2
    return 0 if bool(payload.get('ok', True)) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
