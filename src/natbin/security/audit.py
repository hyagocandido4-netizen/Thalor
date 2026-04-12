from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config.execution_mode import execution_mode_uses_broker_submit

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from .broker_guard import read_guard_state
from .redaction import collect_sensitive_values


def _read_yaml(path: Path) -> dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}



def _walk_embedded_credentials(node: Any, *, path: tuple[str, ...] = ()) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            ks = str(k)
            p = path + (ks,)
            low = ks.lower()
            if v not in (None, ''):
                if p == ('broker', 'email'):
                    findings.append({'path': '.'.join(p), 'kind': 'broker.email'})
                elif p == ('broker', 'password'):
                    findings.append({'path': '.'.join(p), 'kind': 'broker.password'})
                elif low in {'iq_email', 'iq_password'}:
                    findings.append({'path': '.'.join(p), 'kind': low})
            findings.extend(_walk_embedded_credentials(v, path=p))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            findings.extend(_walk_embedded_credentials(item, path=path + (str(idx),)))
    return findings



def _gitignore_has_env(gitignore_path: Path) -> bool:
    if not gitignore_path.exists():
        return False
    try:
        lines = gitignore_path.read_text(encoding='utf-8').splitlines()
    except Exception:
        return False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line in {'.env', '.env.*', '/.env', '/.env.*'}:
            return True
    return False



def _credential_source(source_trace: list[str], *, embedded: bool, broker_email: str | None, broker_password: str | None) -> str:
    if any(str(item).startswith('secret_file:') for item in source_trace):
        return 'external_secret_file'
    if embedded and (broker_email or broker_password):
        return 'config_yaml_embedded'
    if 'compat_env:IQ_*' in source_trace:
        return 'compat_env_iq'
    if 'env:THALOR__*' in source_trace:
        return 'env_thalor'
    if broker_email or broker_password:
        return 'other'
    return 'missing'



def _artifact_safe(path: Path, sensitive_values: list[str]) -> tuple[bool | None, list[str]]:
    if not path.exists():
        return None, []
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return False, ['unreadable']
    leaked = [secret for secret in sensitive_values if secret and secret in text]
    return (len(leaked) == 0), leaked



def audit_security_posture(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    resolved_config: Any | None = None,
    source_trace: list[str] | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    cfg_path = Path(config_path).resolve() if config_path is not None else (repo / 'config' / 'base.yaml').resolve()

    if resolved_config is None:
        from ..config.loader import load_resolved_config
        cfg_obj = load_resolved_config(repo_root=repo, config_path=cfg_path)
        cfg = cfg_obj.as_dict()
        trace = list(getattr(cfg_obj, 'source_trace', []) or [])
    else:
        if hasattr(resolved_config, 'as_dict'):
            cfg = resolved_config.as_dict()
            trace = list(getattr(resolved_config, 'source_trace', []) or [])
        elif hasattr(resolved_config, 'model_dump'):
            cfg = resolved_config.model_dump(mode='python')
            trace = list(source_trace or [])
        else:
            cfg = dict(resolved_config or {})
            trace = list(source_trace or [])

    if source_trace:
        for item in list(source_trace):
            if item not in trace:
                trace.append(item)

    security = dict(cfg.get('security') or {})
    observability = dict(cfg.get('observability') or {})
    execution = dict(cfg.get('execution') or {})
    broker = dict(cfg.get('broker') or {})
    execution_live = bool(execution.get('enabled')) and execution_mode_uses_broker_submit(execution.get('mode')) and str(execution.get('provider') or 'fake') == 'iqoption'

    embedded = _walk_embedded_credentials(_read_yaml(cfg_path))
    embedded_found = len(embedded) > 0
    allow_embedded = bool(security.get('allow_embedded_credentials', False))

    broker_email = str(broker.get('email') or '').strip() or None
    broker_password = None
    if broker.get('password') not in (None, ''):
        try:
            if hasattr(broker.get('password'), 'get_secret_value'):
                broker_password = broker['password'].get_secret_value()
            else:
                broker_password = str(broker.get('password')).strip() or None
        except Exception:
            broker_password = None

    credential_source = _credential_source(trace, embedded=embedded_found, broker_email=broker_email, broker_password=broker_password)

    checks: list[dict[str, Any]] = []

    def _add(name: str, status: str, message: str, **extra: Any) -> None:
        item = {'name': name, 'status': status, 'message': message}
        if extra:
            item.update(extra)
        checks.append(item)

    env_path = repo / '.env'
    gitignore_path = repo / '.gitignore'
    if env_path.exists():
        if _gitignore_has_env(gitignore_path):
            _add('dotenv_gitignore', 'ok', '.env presente e protegido por .gitignore')
        else:
            _add('dotenv_gitignore', 'error', '.env presente mas .gitignore não protege .env')
    else:
        _add('dotenv_gitignore', 'ok', '.env ausente na raiz do repo')

    if embedded_found:
        status = 'warn' if allow_embedded else 'error'
        _add('embedded_credentials', status, 'Credenciais embutidas encontradas no YAML canônico', findings=embedded)
    else:
        _add('embedded_credentials', 'ok', 'Nenhuma credencial embutida encontrada no YAML canônico')

    require_credentials_when_execution_enabled = bool(security.get('live_require_credentials', True))
    execution_enabled = bool(execution.get('enabled'))

    if broker_email and broker_password:
        _add('broker_credentials_present', 'ok', 'Credenciais do broker resolvidas com sucesso', credential_source=credential_source)
    else:
        creds_blocking = bool(execution_live or (execution_enabled and require_credentials_when_execution_enabled))
        status = 'error' if creds_blocking else 'warn'
        _add(
            'broker_credentials_present',
            status,
            'Credenciais do broker ausentes ou incompletas',
            credential_source=credential_source,
            execution_enabled=execution_enabled,
            execution_live=execution_live,
            live_require_credentials=require_credentials_when_execution_enabled,
        )

    if execution_live and bool(security.get('live_require_external_credentials', False)) and credential_source != 'external_secret_file':
        _add('live_external_credentials', 'error', 'Modo live exige external secret file, mas a fonte atual não é externa')
    else:
        _add('live_external_credentials', 'ok', 'Política de credenciais externas satisfeita ou não exigida')

    sensitive_values = collect_sensitive_values({'broker': {'email': broker_email, 'password': broker_password}}, redact_email=bool(security.get('redact_email', True)))

    scope_tag = None
    try:
        asset = str(cfg.get('asset') or ((cfg.get('assets') or [{}])[0].get('asset') or ''))
        interval = int(cfg.get('interval_sec') or ((cfg.get('assets') or [{}])[0].get('interval_sec') or 300))
        scope_tag = f"{asset.replace('/', '_').replace(':', '_').replace(' ', '_')}_{interval}s"
    except Exception:
        scope_tag = None

    artifact_checks: dict[str, Any] = {}
    if scope_tag is not None:
        eff_latest = repo / 'runs' / 'config' / f'effective_config_latest_{scope_tag}.json'
        eff_control = repo / 'runs' / 'control' / scope_tag / 'effective_config.json'
        struct_log = Path(str(observability.get('structured_logs_path') or 'runs/logs/runtime_structured.jsonl'))
        if not struct_log.is_absolute():
            struct_log = repo / struct_log
        for name, path in {
            'effective_config_latest': eff_latest,
            'effective_config_control': eff_control,
            'structured_log': struct_log,
        }.items():
            safe, leaked = _artifact_safe(path, sensitive_values)
            artifact_checks[name] = {
                'path': str(path),
                'exists': path.exists(),
                'safe': safe,
                'leaked_values': leaked,
            }
            if safe is False:
                _add(name, 'error', f'Artifact expõe valor sensível: {path.name}', leaked_values=leaked)
            elif safe is True:
                _add(name, 'ok', f'Artifact redigido corretamente: {path.name}')
            else:
                _add(name, 'ok', f'Artifact ainda não existe: {path.name}')

    guard_state = None
    if scope_tag is not None:
        try:
            dummy_ctx = type('DummyCtx', (), {
                'resolved_config': cfg,
                'config': type('DummyCfg', (), {'timezone': str(cfg.get('timezone') or ((cfg.get('assets') or [{}])[0].get('timezone') or 'UTC'))})(),
                'scope': type('DummyScope', (), {'scope_tag': scope_tag})(),
            })()
            guard_state = read_guard_state(repo_root=repo, ctx=dummy_ctx)
        except Exception:
            guard_state = None

    blocked = any(item['status'] == 'error' for item in checks)
    severity = 'error' if blocked else ('warn' if any(item['status'] == 'warn' for item in checks) else 'ok')

    payload = {
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'ok': not blocked,
        'blocked': blocked,
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'deployment_profile': str(security.get('deployment_profile') or 'local'),
        'execution_live': execution_live,
        'credential_source': credential_source,
        'source_trace': trace,
        'checks': checks,
        'artifacts': artifact_checks,
        'guard_state': guard_state,
    }
    return payload
