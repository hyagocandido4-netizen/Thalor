from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..ops.audit_common import inspect_artifact, now_utc, read_jsonish, summarize_status
from ..ops.diagnostic_utils import check, dedupe_actions, load_selected_scopes, resolve_scope_paths
from ..state.control_repo import control_artifact_paths, write_control_artifact, write_repo_control_artifact
from ..runtime.scope import market_context_path


_RUNTIME_ARTIFACT_SPECS = (
    ('effective_config_latest', 'runtime', 'effective_config', True, 86400),
    ('effective_config_control', 'control', 'effective_config', True, 86400),
    ('market_context', 'runtime', 'market_context', True, None),
    ('loop_status', 'control', 'loop_status', True, 1200),
    ('health', 'control', 'health', True, 1200),
    ('doctor', 'control', 'doctor', True, 3600),
    ('intelligence', 'control', 'intelligence', True, 3600),
)

_OPTIONAL_INFORMATIVE_CONTROL_ARTIFACTS = (
    'release',
    'incidents',
    'retrain',
    'practice_round',
)


def _scope_payload(*, repo: Path, cfg_path: Path, asset: str, interval_sec: int) -> dict[str, Any]:
    ctx = build_context(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)
    _, _, cfg, scopes = load_selected_scopes(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec, all_scopes=False)
    scope_obj = scopes[0] if scopes else None
    scope_paths = resolve_scope_paths(repo_root=repo, cfg=cfg, scope=scope_obj) if scope_obj is not None else {'runtime': {}}
    control_paths = control_artifact_paths(repo_root=repo, asset=asset, interval_sec=interval_sec)
    current = now_utc()
    market_max_age = max(int(interval_sec) * 3, 900)

    artifacts: list[dict[str, Any]] = []

    def _informative_artifact_status(name: str, path: str | Path | None) -> dict[str, Any]:
        raw = inspect_artifact(name=name, path=path, required=False, max_age_sec=None, now=current).as_dict()
        raw['informative_only'] = True
        resolved_path = Path(str(path)) if path not in (None, '') else None
        payload = read_jsonish(resolved_path) if resolved_path is not None and resolved_path.exists() else None
        payload_cfg_path = str((payload or {}).get('config_path') or '').strip() if isinstance(payload, dict) else ''
        matches_cfg = False
        if payload_cfg_path:
            try:
                matches_cfg = Path(payload_cfg_path).resolve() == cfg_path.resolve()
            except Exception:
                matches_cfg = str(payload_cfg_path) == str(cfg_path)
        if payload_cfg_path and not matches_cfg:
            raw['status'] = 'ok'
            raw['message'] = 'Artifact informativo pertence a outro profile/config; ignorado para o audit atual'
            raw['fresh'] = None
        elif str(name) in {'retrain', 'practice_round'} and not bool(raw.get('exists')):
            raw['status'] = 'ok'
            raw['message'] = 'Artifact informativo ainda não existe para este scope'
            raw['fresh'] = None
        elif str(name) == 'incidents' and isinstance(payload, dict):
            total = int((payload.get('incidents') or {}).get('total') or payload.get('total') or 0)
            if total == 0:
                raw['status'] = 'ok'
                raw['message'] = 'Surface de incidentes sem eventos; frescor não bloqueia o scope'
                raw['fresh'] = None
        elif str(name) == 'release' and isinstance(payload, dict):
            raw['status'] = 'ok'
            raw['message'] = 'Artifact informativo de release mantido apenas para referência operacional'
            raw['fresh'] = None
        return raw
    for name, kind, key, required, max_age in _RUNTIME_ARTIFACT_SPECS:
        if name == 'market_context':
            path = market_context_path(asset=asset, interval_sec=int(interval_sec), out_dir=repo / 'runs')
            max_age = market_max_age
        elif kind == 'control':
            path = control_paths.get(key)
        else:
            if key == 'effective_config':
                path = ctx.scoped_paths.get('effective_config') if name == 'effective_config_latest' else ctx.scoped_paths.get('effective_config_control')
            else:
                path = scope_paths.get('runtime', {}).get(key)
        status = inspect_artifact(name=name, path=path, required=required, max_age_sec=max_age, now=current)
        artifacts.append(status.as_dict())

    for name in _OPTIONAL_INFORMATIVE_CONTROL_ARTIFACTS:
        artifacts.append(_informative_artifact_status(name, control_paths.get(name)))

    checks: list[dict[str, Any]] = []
    for item in artifacts:
        status = str(item.get('status') or 'ok')
        msg = str(item.get('message') or '')
        checks.append(check(str(item.get('name')), status, msg, path=item.get('path'), age_sec=item.get('age_sec'), max_age_sec=item.get('max_age_sec')))

    severity = summarize_status(artifacts)
    actions: list[str] = []
    if any(str(item.get('name')) == 'market_context' and str(item.get('status')) == 'error' for item in artifacts):
        actions.append('Execute natbin.refresh_market_context ou observe --once para regenerar o market_context do scope.')
    if any(str(item.get('name')) in {'loop_status', 'health'} and str(item.get('status')) != 'ok' for item in artifacts):
        actions.append('Execute runtime_app observe --once para materializar loop_status/health do scope.')
    payload = {
        'kind': 'runtime_artifact_audit',
        'at_utc': current.isoformat(timespec='seconds'),
        'ok': severity != 'error',
        'severity': severity,
        'scope': {'asset': asset, 'interval_sec': int(interval_sec), 'scope_tag': str(ctx.scope.scope_tag)},
        'artifacts': artifacts,
        'checks': checks,
        'actions': dedupe_actions(actions),
    }
    write_control_artifact(repo_root=repo, asset=asset, interval_sec=interval_sec, name='runtime_artifact_audit', payload=payload)
    return payload


def build_runtime_artifact_audit_payload(
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
    warnings = [item['scope']['scope_tag'] for item in results if str(item.get('severity')) == 'warn']
    errors = [item['scope']['scope_tag'] for item in results if str(item.get('severity')) == 'error']
    payload = {
        'kind': 'runtime_artifact_audit',
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'summary': {
            'scope_count': len(results),
            'multi_asset_enabled': bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
            'warn_scopes': warnings,
            'error_scopes': errors,
        },
        'scope_results': results,
        'actions': dedupe_actions([action for result in results for action in list(result.get('actions') or [])]),
    }
    if write_artifact:
        write_repo_control_artifact(repo_root=repo, name='runtime_artifact_audit', payload=payload)
    return payload

