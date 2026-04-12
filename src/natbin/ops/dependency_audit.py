from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..ops.audit_common import safe_import, summarize_status
from ..ops.config_provenance import _collect_transport_sources, _read_bundle, _safe_env_lines, _secret_bundle_path
from ..ops.diagnostic_utils import check, dedupe_actions, load_selected_scopes
from ..state.control_repo import write_control_artifact, write_repo_control_artifact


def _requirements_declares(path: Path, package_name: str) -> bool:
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception:
        return False
    wanted = package_name.strip().lower()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('-r '):
            continue
        token = line.split(';', 1)[0].strip().split()[0]
        base = token.split('==')[0].split('>=')[0].split('<=')[0].split('~=')[0].split('@')[0].strip().lower()
        if base == wanted:
            return True
    return False


def _docker_uses_requirements(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding='utf-8', errors='replace').lower()
    except Exception:
        return False
    return 'requirements.txt' in text or 'pip install .' in text or 'pip install -r' in text


def _scope_payload(*, repo: Path, cfg_path: Path, asset: str, interval_sec: int) -> dict[str, Any]:
    ctx = build_context(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)
    env_path = repo / '.env'
    bundle_path = _secret_bundle_path(repo, dict(ctx.resolved_config or {}), env_path if env_path.exists() else None)
    bundle = _read_bundle(bundle_path)
    env_lines = _safe_env_lines(env_path if env_path.exists() else None)
    transport = _collect_transport_sources(repo, bundle, env_lines)
    uses_socks = bool(transport.get('uses_socks'))

    checks: list[dict[str, Any]] = []
    actions: list[str] = []

    py_ok = sys.version_info >= (3, 11)
    checks.append(check('python_runtime', 'ok' if py_ok else 'warn', f'Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}', requires='>=3.11'))

    for mod_name, check_name in [('yaml', 'import_pyyaml'), ('pydantic', 'import_pydantic'), ('websocket', 'import_websocket_client'), ('iqoptionapi', 'import_iqoptionapi')]:
        ok, reason = safe_import(mod_name)
        checks.append(check(check_name, 'ok' if ok else 'error', 'Import disponível' if ok else 'Import falhou', reason=reason))
        if not ok:
            actions.append(f'Instale/corrija a dependência do módulo {mod_name}.')

    pysocks_ok, pysocks_reason = safe_import('socks')
    if uses_socks:
        checks.append(check('transport_pysocks_runtime', 'ok' if pysocks_ok else 'error', 'PySocks disponível para transporte SOCKS' if pysocks_ok else 'PySocks ausente para transporte SOCKS', scheme=transport.get('scheme'), reason=pysocks_reason))
        if not pysocks_ok:
            actions.append('Instale PySocks no ambiente antes de usar transporte socks*.')
    else:
        checks.append(check('transport_pysocks_runtime', 'ok', 'Transporte SOCKS não exigido no scope atual', scheme=transport.get('scheme')))

    req = repo / 'requirements.txt'
    req_dev = repo / 'requirements-dev.txt'
    req_ci = repo / 'requirements-ci.txt'
    pyproject = repo / 'pyproject.toml'
    dockerfile = repo / 'Dockerfile'
    for label, path in [('requirements_txt', req), ('requirements_dev', req_dev), ('requirements_ci', req_ci)]:
        declared = _requirements_declares(path, 'PySocks')
        status = 'ok' if (declared or not uses_socks) else 'error'
        message = 'PySocks declarado' if declared else ('PySocks ausente mas não obrigatório para este scope' if not uses_socks else 'PySocks ausente no arquivo de dependências')
        checks.append(check(label, status, message, path=str(path)))
        if status == 'error':
            actions.append(f'Adicione PySocks em {path.name}.')

    pyproject_ok = pyproject.exists()
    checks.append(check('pyproject_present', 'ok' if pyproject_ok else 'warn', 'pyproject.toml presente' if pyproject_ok else 'pyproject.toml ausente', path=str(pyproject)))

    docker_ok = _docker_uses_requirements(dockerfile)
    if uses_socks:
        status = 'ok' if docker_ok and _requirements_declares(req, 'PySocks') else 'error'
        message = 'Dockerfile consome requirements e cobre PySocks' if status == 'ok' else 'Dockerfile/requirements podem não instalar PySocks na imagem'
    else:
        status = 'ok' if docker_ok else 'warn'
        message = 'Dockerfile consome requirements' if docker_ok else 'Dockerfile não evidenciou instalação das requirements canônicas'
    checks.append(check('docker_dependency_contract', status, message, path=str(dockerfile)))
    if status == 'error':
        actions.append('Garanta que o Dockerfile instale requirements.txt contendo PySocks.')

    severity = summarize_status(checks)
    payload = {
        'kind': 'dependency_audit',
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'ok': severity != 'error',
        'severity': severity,
        'scope': {'asset': asset, 'interval_sec': int(interval_sec), 'scope_tag': str(ctx.scope.scope_tag)},
        'transport': {
            'configured': bool(transport.get('configured')),
            'selected_source': transport.get('selected_source'),
            'scheme': transport.get('scheme'),
            'uses_socks': uses_socks,
            'pysocks_available': pysocks_ok,
            'pysocks_reason': pysocks_reason,
        },
        'checks': checks,
        'actions': dedupe_actions(actions),
    }
    write_control_artifact(repo_root=repo, asset=asset, interval_sec=interval_sec, name='dependency_audit', payload=payload)
    return payload


def build_dependency_audit_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = False,
    write_artifact: bool = True,
) -> dict[str, Any]:
    repo, cfg_path, cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    results = [_scope_payload(repo=repo, cfg_path=cfg_path, asset=str(scope.asset), interval_sec=int(scope.interval_sec)) for scope in scopes]
    scope_severities = [str(item.get('severity') or 'ok') for item in results]
    severity = 'error' if 'error' in scope_severities else ('warn' if 'warn' in scope_severities else 'ok')
    payload = {
        'kind': 'dependency_audit',
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'summary': {
            'scope_count': len(results),
            'multi_asset_enabled': bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
            'error_scopes': [item['scope']['scope_tag'] for item in results if str(item.get('severity')) == 'error'],
            'warn_scopes': [item['scope']['scope_tag'] for item in results if str(item.get('severity')) == 'warn'],
        },
        'scope_results': results,
        'actions': dedupe_actions([action for result in results for action in list(result.get('actions') or [])]),
    }
    if write_artifact:
        write_repo_control_artifact(repo_root=repo, name='dependency_audit', payload=payload)
    return payload

