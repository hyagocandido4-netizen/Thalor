from __future__ import annotations

import argparse
import hashlib
import re
from urllib.parse import urlparse
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable

from ..control.plan import build_context
from ..security.redaction import collect_sensitive_values, sanitize_payload
from .config_provenance import _read_bundle, _secret_bundle_path, _safe_env_lines
from .diag_cli_common import (
    add_output_args,
    add_repo_config_args,
    build_logger,
    exception_payload,
    exit_code_from_payload,
    log_event,
    maybe_append_log,
    print_payload,
    utc_now_iso,
    write_json,
    write_repo_artifact,
)

_DEFAULT_INCLUDE_GLOBS = (
    'runs/config/**/*.json',
    'runs/control/**/*.json',
    'runs/logs/**/*.jsonl',
    'runs/logs/**/*.log',
)
_SOURCE_INCLUDE_GLOBS = (
    'src/**/*.py',
    'scripts/tools/**/*.py',
    'scripts/tools/**/*.ps1',
    'config/**/*.yaml',
    'config/**/*.yml',
)
_DEFAULT_EXCLUDE_GLOBS = (
    '.git/**',
    '.venv/**',
    '__pycache__/**',
    '.pytest_cache/**',
    'diag_zips/**',
    'test_battery/**',
    'secrets/**',
    '.env',
    '.env.*',
    'config/*secret*.yaml',
    'config/*secret*.yml',
    'config/*secrets*.yaml',
    'config/*secrets*.yml',
    '*.zip',
    '*.sqlite3',
    '*.sqlite3-wal',
    '*.sqlite3-shm',
    '*.db',
)
_CREDENTIAL_URL_RE = re.compile(r'(?i)\b(?:https?|socks|socks4|socks5|socks5h)://[^/\s:@]+:[^@\s]+@')


def _is_masked_credential_url(value: str) -> bool:
    text = str(value or '').strip()
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except Exception:
        return False
    username = str(parsed.username or '').strip()
    password = str(parsed.password or '').strip()
    if not username and not password:
        return False
    def _masked(part: str) -> bool:
        raw = str(part or '').strip().lower()
        if not raw:
            return True
        return set(raw) <= {'*'} or 'redacted' in raw or raw in {'x', 'xx', 'xxx'}
    return _masked(username) and _masked(password)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8', errors='ignore')).hexdigest()[:12]


def _read_text(path: Path, max_file_bytes: int) -> str | None:
    try:
        if path.stat().st_size > max(1, int(max_file_bytes)):
            return None
    except Exception:
        return None
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return None


def _collect_transport_secret_values(repo: Path, resolved_config: dict[str, Any]) -> list[str]:
    out: list[str] = []
    env_path = repo / '.env'
    env_lines = _safe_env_lines(env_path if env_path.exists() else None)
    bundle_path = _secret_bundle_path(repo, resolved_config, env_path if env_path.exists() else None)
    bundle = _read_bundle(bundle_path)
    for value in collect_sensitive_values(bundle):
        text = str(value or '').strip()
        if text and text not in out:
            out.append(text)
    for candidate in (repo / 'secrets' / 'transport_endpoint', repo / 'secrets' / 'transport_endpoints'):
        if not candidate.exists():
            continue
        try:
            lines = candidate.read_text(encoding='utf-8', errors='replace').splitlines()
        except Exception:
            continue
        for raw in lines:
            text = str(raw or '').strip()
            if text and text not in out:
                out.append(text)
    for key in ('TRANSPORT_ENDPOINT', 'TRANSPORT_ENDPOINTS', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        value = env_lines.get(key)
        text = str(value or '').strip()
        if text and text not in out:
            out.append(text)
    return out


def _matches_any(rel_posix: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if fnmatch(rel_posix, pattern):
            return True
    return False


def _iter_candidates(
    repo: Path,
    *,
    include_globs: Iterable[str],
    exclude_globs: Iterable[str],
) -> list[Path]:
    files: list[Path] = []
    for path in repo.rglob('*'):
        if not path.is_file():
            continue
        rel_posix = path.relative_to(repo).as_posix()
        if _matches_any(rel_posix, exclude_globs):
            continue
        if _matches_any(rel_posix, include_globs):
            files.append(path)
    return sorted(files)


def _scan_text_for_secrets(
    path: Path,
    text: str,
    *,
    concrete_values: list[str],
    limit_findings_per_file: int,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    for line_no, line in enumerate(text.splitlines(), start=1):
        for secret in concrete_values:
            if not secret or secret not in line:
                continue
            token_hash = _hash_secret(secret)
            key = ('concrete_secret_value', line_no, token_hash)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                {
                    'kind': 'concrete_secret_value',
                    'path': str(path),
                    'line_no': line_no,
                    'sha12': token_hash,
                }
            )
            if len(findings) >= int(limit_findings_per_file):
                return findings

        for match in _CREDENTIAL_URL_RE.finditer(line):
            credential_url = match.group(0)
            if _is_masked_credential_url(credential_url):
                continue
            token_hash = _hash_secret(credential_url)
            key = ('credential_url', line_no, token_hash)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                {
                    'kind': 'credential_url',
                    'path': str(path),
                    'line_no': line_no,
                    'sha12': token_hash,
                }
            )
            if len(findings) >= int(limit_findings_per_file):
                return findings

    return findings


def build_redaction_audit_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    scan_source: bool = False,
    max_file_bytes: int = 1_000_000,
    limit_findings_per_file: int = 10,
    dry_run: bool = False,
    logger=None,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    resolved = dict(ctx.resolved_config or {})

    concrete_values = collect_sensitive_values(ctx.resolved_config)
    for value in _collect_transport_secret_values(repo, resolved):
        if value not in concrete_values:
            concrete_values.append(value)

    include_patterns = list(include_globs or _DEFAULT_INCLUDE_GLOBS)
    if scan_source:
        include_patterns.extend(_SOURCE_INCLUDE_GLOBS)
    exclude_patterns = list(_DEFAULT_EXCLUDE_GLOBS)
    exclude_patterns.extend(list(exclude_globs or []))

    log_event(
        logger,
        'redaction_audit_start',
        repo_root=str(repo),
        config_path=str(ctx.config.config_path),
        scan_source=bool(scan_source),
        include_patterns=include_patterns,
    )

    files = _iter_candidates(repo, include_globs=include_patterns, exclude_globs=exclude_patterns)
    scanned_files: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    skipped_large: list[str] = []

    for path in files:
        rel = path.relative_to(repo).as_posix()
        text = _read_text(path, max_file_bytes=max_file_bytes)
        if text is None:
            skipped_large.append(rel)
            continue
        file_findings = _scan_text_for_secrets(
            path,
            text,
            concrete_values=concrete_values,
            limit_findings_per_file=max(1, int(limit_findings_per_file)),
        )
        scanned_files.append(
            {
                'path': rel,
                'size_bytes': int(path.stat().st_size),
                'finding_count': len(file_findings),
            }
        )
        findings.extend(file_findings)

    leak_paths = sorted({str(Path(item['path']).relative_to(repo).as_posix()) if Path(item['path']).is_absolute() else str(item['path']) for item in findings})
    severity = 'error' if findings else ('warn' if skipped_large else 'ok')
    actions: list[str] = []
    if findings:
        actions.append('Redija ou regenere os artifacts listados antes de rodar PRACTICE/produção.')
    if skipped_large:
        actions.append('Revise arquivos grandes ignorados ou aumente --max-file-bytes para uma varredura mais ampla.')

    payload = {
        'kind': 'redaction_audit',
        'at_utc': utc_now_iso(),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'scan_source': bool(scan_source),
        'file_selection': {
            'include_globs': include_patterns,
            'exclude_globs': exclude_patterns,
            'max_file_bytes': int(max_file_bytes),
            'limit_findings_per_file': int(limit_findings_per_file),
        },
        'fingerprint_inventory': {
            'concrete_values_count': len(concrete_values),
            'hashes_sha12': [_hash_secret(value) for value in concrete_values[:20]],
        },
        'summary': {
            'candidate_files': len(files),
            'scanned_files': len(scanned_files),
            'skipped_large_files': len(skipped_large),
            'leak_files': len(leak_paths),
            'findings_total': len(findings),
        },
        'leak_paths': leak_paths,
        'findings': findings,
        'scanned_files': scanned_files,
        'skipped_large': skipped_large,
        'actions': actions,
        'dry_run': bool(dry_run),
    }
    payload = sanitize_payload(payload, sensitive_values=concrete_values)
    if not dry_run:
        try:
            write_repo_artifact(repo, 'redaction_audit', payload)
        except Exception:
            pass
    log_event(
        logger,
        'redaction_audit_complete',
        severity=payload.get('severity'),
        leak_files=len(leak_paths),
        findings_total=len(findings),
    )
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Varre artifacts e, opcionalmente, código-fonte em busca de vazamento de segredos/redaction falha.')
    add_repo_config_args(parser)
    add_output_args(parser)
    parser.add_argument('--include-glob', dest='include_globs', action='append', default=[], help='Glob adicional de inclusão (relativo ao repo)')
    parser.add_argument('--exclude-glob', dest='exclude_globs', action='append', default=[], help='Glob adicional de exclusão (relativo ao repo)')
    parser.add_argument('--scan-source', action='store_true', help='Inclui src/, scripts/tools/ e config/ canônicos na varredura')
    parser.add_argument('--max-file-bytes', type=int, default=1_000_000)
    parser.add_argument('--limit-findings-per-file', type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    logger = build_logger('natbin.redaction_audit', verbose=bool(ns.verbose))
    try:
        payload = build_redaction_audit_payload(
            repo_root=ns.repo_root,
            config_path=ns.config,
            include_globs=list(getattr(ns, 'include_globs', []) or []) or None,
            exclude_globs=list(getattr(ns, 'exclude_globs', []) or []) or None,
            scan_source=bool(getattr(ns, 'scan_source', False)),
            max_file_bytes=int(getattr(ns, 'max_file_bytes', 1_000_000) or 1_000_000),
            limit_findings_per_file=int(getattr(ns, 'limit_findings_per_file', 10) or 10),
            dry_run=bool(getattr(ns, 'dry_run', False)),
            logger=logger,
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        payload = exception_payload('redaction_audit', exc)
        print_payload(payload, as_json=True)
        return 2

    if ns.output:
        write_json(ns.output, payload)
    maybe_append_log(getattr(ns, 'log_jsonl_path', None), payload)
    print_payload(payload, as_json=bool(ns.json))
    return exit_code_from_payload(payload)


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
