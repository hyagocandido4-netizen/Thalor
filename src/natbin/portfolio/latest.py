from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..runtime.perf import write_text_if_changed
from .paths import portfolio_runs_dir


def _normalize_repo_root(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve()


def _normalize_config_path(repo_root: str | Path, config_path: str | Path | None) -> str | None:
    if config_path in (None, ''):
        return None
    root = _normalize_repo_root(repo_root)
    p = Path(config_path)
    if not p.is_absolute():
        p = (root / p).resolve()
    else:
        p = p.resolve()
    try:
        return str(p.relative_to(root)).replace('\\', '/')
    except Exception:
        return str(p)


def _slug(value: Any) -> str:
    raw = str(value or '').strip()
    out: list[str] = []
    prev_sep = False
    for ch in raw:
        if ch.isalnum():
            out.append(ch.lower())
            prev_sep = False
            continue
        if ch in {'-', '_', '.', '/'}:
            if not prev_sep:
                out.append('_')
                prev_sep = True
    slug = ''.join(out).strip('_')
    return slug or 'default'


def portfolio_profile_key(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> str:
    cfg_norm = _normalize_config_path(repo_root, config_path)
    profile_norm = str(profile or '').strip()
    if not cfg_norm and not profile_norm:
        return 'default'
    label = _slug(profile_norm or (Path(cfg_norm or 'config').stem))
    digest_src = f'{profile_norm}|{cfg_norm or ""}'
    digest = hashlib.sha1(digest_src.encode('utf-8')).hexdigest()[:10]
    return f'{label}__{digest}'


def portfolio_profile_dir(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> Path:
    root = _normalize_repo_root(repo_root)
    key = portfolio_profile_key(root, config_path=config_path, profile=profile)
    p = portfolio_runs_dir(root) / 'profiles' / key
    p.mkdir(parents=True, exist_ok=True)
    return p


def scoped_portfolio_cycle_latest_path(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> Path:
    return portfolio_profile_dir(repo_root, config_path=config_path, profile=profile) / 'portfolio_cycle_latest.json'


def scoped_portfolio_allocation_latest_path(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> Path:
    return portfolio_profile_dir(repo_root, config_path=config_path, profile=profile) / 'portfolio_allocation_latest.json'


def portfolio_context_metadata(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    cfg_norm = _normalize_config_path(repo_root, config_path)
    runtime_profile = str(profile or '').strip() or None
    profile_key = portfolio_profile_key(repo_root, config_path=config_path, profile=profile)
    return {
        'artifact_scope': 'config_profile',
        'profile_key': profile_key,
        'runtime_profile': runtime_profile,
        'config_path': cfg_norm,
        'config_name': Path(cfg_norm).name if cfg_norm else None,
    }


def with_portfolio_context(
    payload: dict[str, Any],
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    out = dict(payload)
    out.update(portfolio_context_metadata(repo_root, config_path=config_path, profile=profile))
    return out


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return None
    return dict(obj) if isinstance(obj, dict) else None


def portfolio_payload_matches_context(
    payload: dict[str, Any] | None,
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    if config_path in (None, '') and not str(profile or '').strip():
        return True

    expected = portfolio_context_metadata(repo_root, config_path=config_path, profile=profile)
    actual_cfg = _normalize_config_path(repo_root, payload.get('config_path'))
    actual_profile = str(payload.get('runtime_profile') or '').strip() or None
    actual_key = str(payload.get('profile_key') or '').strip() or None

    if actual_key and actual_key == str(expected.get('profile_key')):
        return True
    if actual_cfg and expected.get('config_path') and actual_cfg == expected.get('config_path'):
        return True
    if actual_profile and expected.get('runtime_profile') and actual_profile == expected.get('runtime_profile'):
        return True
    return False


def load_portfolio_latest_payload(
    repo_root: str | Path,
    *,
    name: str,
    config_path: str | Path | None = None,
    profile: str | None = None,
    allow_legacy_fallback: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    root = _normalize_repo_root(repo_root)
    scoped_dir = portfolio_profile_dir(root, config_path=config_path, profile=profile)
    scoped_path = scoped_dir / str(name)
    legacy_path = portfolio_runs_dir(root) / str(name)
    info: dict[str, Any] = {
        'requested_name': str(name),
        'profile_key': portfolio_profile_key(root, config_path=config_path, profile=profile),
        'runtime_profile': str(profile or '').strip() or None,
        'config_path': _normalize_config_path(root, config_path),
        'scoped_path': str(scoped_path),
        'scoped_exists': bool(scoped_path.exists()),
        'legacy_path': str(legacy_path),
        'legacy_exists': bool(legacy_path.exists()),
        'source': 'missing',
        'matched': False,
    }

    scoped_payload = _read_json(scoped_path)
    if isinstance(scoped_payload, dict):
        info.update({'source': 'scoped', 'matched': True})
        return scoped_payload, info

    if not allow_legacy_fallback:
        return None, info

    legacy_payload = _read_json(legacy_path)
    if not isinstance(legacy_payload, dict):
        return None, info

    matched = portfolio_payload_matches_context(legacy_payload, root, config_path=config_path, profile=profile)
    info.update({'source': 'legacy_matched' if matched else 'legacy_mismatch', 'matched': bool(matched)})
    if matched:
        return legacy_payload, info
    return None, info


def write_portfolio_latest_payload(
    repo_root: str | Path,
    *,
    name: str,
    payload: dict[str, Any],
    config_path: str | Path | None = None,
    profile: str | None = None,
    write_legacy: bool = True,
) -> dict[str, Any]:
    root = _normalize_repo_root(repo_root)
    enriched = with_portfolio_context(payload, root, config_path=config_path, profile=profile)
    body = json.dumps(enriched, indent=2, ensure_ascii=False, default=str)

    scoped_path = portfolio_profile_dir(root, config_path=config_path, profile=profile) / str(name)
    write_text_if_changed(scoped_path, body, encoding='utf-8')

    legacy_path = portfolio_runs_dir(root) / str(name)
    if write_legacy:
        write_text_if_changed(legacy_path, body, encoding='utf-8')

    return {
        'profile_key': portfolio_profile_key(root, config_path=config_path, profile=profile),
        'runtime_profile': str(profile or '').strip() or None,
        'config_path': _normalize_config_path(root, config_path),
        'scoped_path': str(scoped_path),
        'legacy_path': str(legacy_path),
        'write_legacy': bool(write_legacy),
    }
