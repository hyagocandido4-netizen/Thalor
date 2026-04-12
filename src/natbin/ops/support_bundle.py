from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from ..control.plan import build_context
from ..ops.config_provenance import build_config_provenance_payload, collect_project_sensitive_values, _sanitize_text_blob
from ..ops.diagnostic_utils import dedupe_actions, load_selected_scopes
from ..ops.production_doctor import build_production_doctor_payload
from ..ops.production_gate import build_production_gate_payload
from ..ops.provider_probe import build_provider_probe_payload
from ..ops.release_readiness import build_release_readiness_payload
from ..security.audit import audit_security_posture
from ..security.redaction import sanitize_payload
from ..state.control_repo import control_artifact_paths, repo_control_artifact_paths, write_repo_control_artifact


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _safe_rel(rel: str) -> str:
    parts = [part for part in Path(rel).as_posix().split('/') if part not in {'', '.', '..'}]
    return '/'.join(parts)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: dict[str, Any], *, sensitive_values: Iterable[str]) -> None:
    _ensure_parent(path)
    clean = sanitize_payload(payload, sensitive_values=list(sensitive_values), redact_email=True)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def _write_text(path: Path, text: str, *, sensitive_values: Iterable[str]) -> None:
    _ensure_parent(path)
    clean = _sanitize_text_blob(str(text), sensitive_values=list(sensitive_values), redact_email=True)
    path.write_text(clean, encoding='utf-8')


def _copy_file(path: Path, dest: Path, *, sensitive_values: Iterable[str], max_bytes: int | None = None) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {'path': str(path), 'included': False, 'reason': 'missing'}
    raw_bytes = path.read_bytes()
    truncated = False
    if max_bytes is not None and len(raw_bytes) > max_bytes:
        raw_bytes = raw_bytes[-int(max_bytes):]
        truncated = True
    try:
        text = raw_bytes.decode('utf-8')
        _write_text(dest, text, sensitive_values=sensitive_values)
    except Exception:
        _ensure_parent(dest)
        dest.write_bytes(raw_bytes)
    return {
        'path': str(path),
        'included': True,
        'bytes': int(path.stat().st_size),
        'truncated': truncated,
    }


def _zip_dir(root: Path, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, mode='w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in sorted(item for item in root.rglob('*') if item.is_file()):
            zf.write(file_path, arcname=file_path.relative_to(root).as_posix())


def _python_runtime_payload() -> dict[str, Any]:
    try:
        freeze = subprocess.run([
            sys.executable, '-m', 'pip', 'freeze'
        ], capture_output=True, text=True, timeout=60, check=False)
        freeze_lines = [line for line in str(freeze.stdout or '').splitlines() if line.strip()]
    except Exception as exc:
        freeze_lines = [f'error:{type(exc).__name__}:{exc}']
    return {
        'at_utc': _now_utc().isoformat(timespec='seconds'),
        'python_executable': sys.executable,
        'pip_freeze': freeze_lines,
    }


def _git_payload(repo: Path) -> dict[str, Any]:
    def _run(*args: str) -> str:
        try:
            proc = subprocess.run(['git', *args], cwd=str(repo), capture_output=True, text=True, timeout=30, check=False)
        except Exception as exc:
            return f'error:{type(exc).__name__}:{exc}'
        return str(proc.stdout or proc.stderr or '').strip()
    return {
        'rev_parse_head': _run('rev-parse', 'HEAD'),
        'status_short_branch': _run('status', '--short', '--branch'),
    }


def _safe_env_snapshot() -> dict[str, str | None]:
    keys = [
        'THALOR_REPO_ROOT',
        'THALOR_CONFIG_PATH',
        'THALOR_SECRETS_FILE',
        'THALOR_DOTENV_ALLOW_BEHAVIOR',
        'TRANSPORT_ENDPOINT',
        'TRANSPORT_ENDPOINTS',
        'TRANSPORT_NO_PROXY',
        'HTTP_PROXY',
        'HTTPS_PROXY',
        'ALL_PROXY',
        'IQ_BALANCE_MODE',
        'ASSET',
        'INTERVAL_SEC',
        'TIMEZONE',
    ]
    return {key: __import__('os').getenv(key) for key in keys if __import__('os').getenv(key) is not None}


def _copy_yaml_or_text(path: Path, dest: Path, *, sensitive_values: Iterable[str]) -> dict[str, Any]:
    if not path.exists():
        return {'path': str(path), 'included': False, 'reason': 'missing'}
    if yaml is not None and path.suffix.lower() in {'.yaml', '.yml'}:
        try:
            raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            if isinstance(raw, dict):
                clean = sanitize_payload(raw, sensitive_values=list(sensitive_values), redact_email=True)
                _ensure_parent(dest)
                dest.write_text(yaml.safe_dump(clean, sort_keys=False, allow_unicode=True), encoding='utf-8')
                return {'path': str(path), 'included': True, 'kind': 'yaml'}
        except Exception:
            pass
    text = path.read_text(encoding='utf-8', errors='replace')
    _write_text(dest, text, sensitive_values=sensitive_values)
    return {'path': str(path), 'included': True, 'kind': 'text'}


def build_support_bundle_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = False,
    probe_provider: bool = False,
    sample_candles: int = 3,
    market_context_max_age_sec: int | None = None,
    min_dataset_rows: int = 100,
    include_logs: bool = True,
    max_log_bytes: int = 1_000_000,
    output_dir: str | Path | None = None,
    bundle_prefix: str = 'support_bundle',
    write_artifact: bool = True,
) -> dict[str, Any]:
    repo, cfg_path, cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    primary_ctx = build_context(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)

    provenance = build_config_provenance_payload(
        repo_root=repo,
        config_path=cfg_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
        write_artifact=True,
    )
    security = audit_security_posture(
        repo_root=repo,
        config_path=cfg_path,
        resolved_config=primary_ctx.resolved_config,
        source_trace=list(primary_ctx.source_trace),
    )
    provider_probe = build_provider_probe_payload(
        repo_root=repo,
        config_path=cfg_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
        active=bool(probe_provider),
        sample_candles=sample_candles,
        probe_market_context=True,
        market_context_max_age_sec=market_context_max_age_sec,
        write_artifact=True,
    )
    production_gate = build_production_gate_payload(
        repo_root=repo,
        config_path=cfg_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
        probe_provider=bool(probe_provider),
        sample_candles=sample_candles,
        market_context_max_age_sec=market_context_max_age_sec,
        min_dataset_rows=min_dataset_rows,
        write_artifact=True,
    )
    release = build_release_readiness_payload(repo_root=repo, config_path=cfg_path)
    doctor_by_scope: list[dict[str, Any]] = []
    for scope in scopes:
        doctor_by_scope.append(
            build_production_doctor_payload(
                repo_root=repo,
                config_path=cfg_path,
                asset=str(scope.asset),
                interval_sec=int(scope.interval_sec),
                probe_broker=False,
                strict_runtime_artifacts=True,
                enforce_live_broker_prereqs=True,
                market_context_max_age_sec=market_context_max_age_sec,
                min_dataset_rows=int(min_dataset_rows),
                write_artifact=True,
            )
        )

    bundle_path = Path(output_dir) if output_dir not in (None, '') else (repo / 'diag_zips')
    if not bundle_path.is_absolute():
        bundle_path = (repo / bundle_path).resolve()
    when = _now_utc()
    stamp = when.strftime('%Y%m%d_%H%M%S')
    zip_path = bundle_path / f'{bundle_prefix}_{stamp}.zip'

    sensitive_values = collect_project_sensitive_values(
        repo_root=repo,
        resolved_cfg=primary_ctx.resolved_config,
    )
    actions = dedupe_actions(list(production_gate.get('actions') or []) + list(provenance.get('actions') or []))

    copied: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix='thalor_support_bundle_') as tmp:
        stage = Path(tmp)
        _write_json(stage / 'diagnostics' / 'config_provenance_audit.json', provenance, sensitive_values=sensitive_values)
        _write_json(stage / 'diagnostics' / 'security.json', security, sensitive_values=sensitive_values)
        _write_json(stage / 'diagnostics' / 'provider_probe.json', provider_probe, sensitive_values=sensitive_values)
        _write_json(stage / 'diagnostics' / 'production_gate.json', production_gate, sensitive_values=sensitive_values)
        _write_json(stage / 'diagnostics' / 'release.json', release, sensitive_values=sensitive_values)
        _write_json(stage / 'metadata' / 'python_runtime.json', _python_runtime_payload(), sensitive_values=sensitive_values)
        _write_json(stage / 'metadata' / 'git.json', _git_payload(repo), sensitive_values=sensitive_values)
        _write_json(stage / 'metadata' / 'env_snapshot.json', _safe_env_snapshot(), sensitive_values=sensitive_values)
        _write_json(
            stage / 'metadata' / 'bundle_summary.json',
            {
                'at_utc': when.isoformat(timespec='seconds'),
                'repo_root': str(repo),
                'config_path': str(cfg_path),
                'scope_count': len(scopes),
                'all_scopes': bool(all_scopes),
                'probe_provider': bool(probe_provider),
                'actions': actions,
            },
            sensitive_values=sensitive_values,
        )
        for doctor in doctor_by_scope:
            scope = dict(doctor.get('scope') or {})
            scope_tag = str(scope.get('scope_tag') or 'unknown_scope')
            _write_json(stage / 'diagnostics' / 'doctor' / f'{scope_tag}.json', doctor, sensitive_values=sensitive_values)

        copied.append(_copy_yaml_or_text(Path(cfg_path), stage / 'config' / 'selected_config.yaml', sensitive_values=sensitive_values))
        secret_file = repo / 'config' / 'broker_secrets.yaml'
        copied.append(_copy_yaml_or_text(secret_file, stage / 'config' / 'broker_secrets.sanitized.yaml', sensitive_values=sensitive_values))
        for name in ('transport_endpoint', 'transport_endpoints'):
            copied.append(_copy_yaml_or_text(repo / 'secrets' / name, stage / 'config' / f'{name}.sanitized.txt', sensitive_values=sensitive_values))

        repo_artifacts = repo_control_artifact_paths(repo_root=repo)
        for name, path_raw in repo_artifacts.items():
            if name == 'repo_control_dir':
                continue
            copied.append(_copy_file(Path(path_raw), stage / 'artifacts' / '_repo' / f'{name}.json', sensitive_values=sensitive_values, max_bytes=max_log_bytes))

        for scope in scopes:
            scope_tag = str(scope.scope_tag)
            control_paths = control_artifact_paths(repo_root=repo, asset=str(scope.asset), interval_sec=int(scope.interval_sec))
            for name, path_raw in control_paths.items():
                if name == 'control_dir':
                    continue
                copied.append(_copy_file(Path(path_raw), stage / 'artifacts' / scope_tag / f'{name}.json', sensitive_values=sensitive_values, max_bytes=max_log_bytes))
            runtime_paths = provenance.get('selected_scopes') or []
            for item in runtime_paths:
                if str(item.get('scope_tag')) != scope_tag:
                    continue
                copied.append(_copy_file(Path(str((item.get('runtime_paths') or {}).get('market_context_path') or '')), stage / 'artifacts' / scope_tag / 'market_context.json', sensitive_values=sensitive_values, max_bytes=max_log_bytes))
                copied.append(_copy_file(Path(str((item.get('data_paths') or {}).get('dataset_path') or '')), stage / 'artifacts' / scope_tag / 'dataset_preview.csv', sensitive_values=sensitive_values, max_bytes=200_000))

        if include_logs:
            log_dir = repo / 'runs' / 'logs'
            for name in ('runtime_structured.jsonl', 'network_transport.jsonl', 'request_metrics.jsonl'):
                copied.append(_copy_file(log_dir / name, stage / 'logs' / name, sensitive_values=sensitive_values, max_bytes=max_log_bytes))

        manifest = {
            'at_utc': when.isoformat(timespec='seconds'),
            'kind': 'support_bundle_manifest',
            'repo_root': str(repo),
            'config_path': str(cfg_path),
            'scope_count': len(scopes),
            'selected_scopes': [
                {
                    'asset': str(scope.asset),
                    'interval_sec': int(scope.interval_sec),
                    'scope_tag': str(scope.scope_tag),
                }
                for scope in scopes
            ],
            'probe_provider': bool(probe_provider),
            'include_logs': bool(include_logs),
            'copied_files': copied,
            'diagnostic_severity': {
                'config_provenance': provenance.get('severity'),
                'security': security.get('severity'),
                'provider_probe': provider_probe.get('severity'),
                'production_gate': production_gate.get('severity'),
                'release': release.get('severity'),
            },
            'actions': actions,
        }
        _write_json(stage / 'manifest.json', manifest, sensitive_values=sensitive_values)
        _write_text(
            stage / 'README.txt',
            'Thalor support bundle sanitizado. Inclui config provenance, provider probe, production gate, doctor(s), release, artifacts e logs selecionados.',
            sensitive_values=sensitive_values,
        )
        _zip_dir(stage, zip_path)

    payload = {
        'at_utc': when.isoformat(timespec='seconds'),
        'kind': 'support_bundle',
        'ok': True,
        'severity': 'ok',
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'scope_count': len(scopes),
        'all_scopes': bool(all_scopes),
        'probe_provider': bool(probe_provider),
        'include_logs': bool(include_logs),
        'zip_path': str(zip_path),
        'bundle_prefix': str(bundle_prefix),
        'actions': actions,
        'diagnostics': {
            'config_provenance': provenance.get('severity'),
            'security': security.get('severity'),
            'provider_probe': provider_probe.get('severity'),
            'production_gate': production_gate.get('severity'),
            'release': release.get('severity'),
        },
    }
    if write_artifact:
        write_repo_control_artifact(repo_root=repo, name='support_bundle', payload=payload)
    return payload


__all__ = ['build_support_bundle_payload']
