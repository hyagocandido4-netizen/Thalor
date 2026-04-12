from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .diagnostic_utils import load_selected_scopes, now_utc, resolve_scope_paths
from .intelligence_surface import build_portfolio_intelligence_payload
from .signal_artifact_audit import build_signal_artifact_audit_payload
from ..intelligence.refresh import refresh_config_intelligence


@dataclass(slots=True)
class StepResult:
    name: str
    scope_tag: str
    command: list[str]
    returncode: int
    timed_out: bool
    stdout_tail: str
    stderr_tail: str
    ok: bool
    parsed: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'name': self.name,
            'scope_tag': self.scope_tag,
            'command': list(self.command),
            'returncode': int(self.returncode),
            'timed_out': bool(self.timed_out),
            'stdout_tail': str(self.stdout_tail or ''),
            'stderr_tail': str(self.stderr_tail or ''),
            'ok': bool(self.ok),
        }
        if self.parsed is not None:
            payload['parsed'] = self.parsed
        return payload


def _tail(text: str | None, *, max_len: int = 2000) -> str:
    raw = str(text or '')
    if len(raw) <= max_len:
        return raw
    return raw[-max_len:]


def _run_runtime_app_json(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    subargs: list[str],
    timeout_sec: int = 300,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    cmd = [
        sys.executable,
        '-m',
        'natbin.control.app',
        '--repo-root',
        str(root),
    ]
    if config_path not in (None, ''):
        cmd.extend(['--config', str(config_path)])
    cmd.extend(list(subargs))
    if '--json' not in cmd:
        cmd.append('--json')
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_sec)),
            check=False,
        )
        parsed: dict[str, Any] | None = None
        stdout = completed.stdout or ''
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                loaded = json.loads(line)
            except Exception:
                continue
            if isinstance(loaded, dict):
                parsed = loaded
                break
        parsed_ok = isinstance(parsed, dict)
        stdout_tail = _tail(completed.stdout)
        stderr_tail = _tail(completed.stderr)
        return {
            'ok': bool(completed.returncode == 0 and parsed_ok),
            'returncode': int(completed.returncode),
            'timed_out': False,
            'command': cmd,
            'stdout_tail': stdout_tail,
            'stderr_tail': stderr_tail,
            'parsed': parsed,
            'subargs': list(subargs),
            'missing_payload': bool(completed.returncode == 0 and not parsed_ok),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            'ok': False,
            'returncode': 124,
            'timed_out': True,
            'command': cmd,
            'stdout_tail': _tail(getattr(exc, 'stdout', '')),
            'stderr_tail': _tail(getattr(exc, 'stderr', '')),
            'parsed': None,
            'subargs': list(subargs),
        }


def _scope_tag_map(items: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        tag = str(getattr(item, 'scope_tag', '') or '')
        if tag:
            out[tag] = item
    return out


def _surface_map(surface_payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in list((surface_payload or {}).get('items') or []):
        if isinstance(item, Mapping):
            tag = str(item.get('scope_tag') or '')
            if tag:
                out[tag] = dict(item)
    return out


def _audit_map(audit_payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in list((audit_payload or {}).get('scope_results') or []):
        if isinstance(item, Mapping):
            scope = item.get('scope')
            if isinstance(scope, Mapping):
                tag = str(scope.get('scope_tag') or '')
                if tag:
                    out[tag] = dict(item)
    return out


def _scope_needs_maintenance(
    *,
    surface_item: Mapping[str, Any] | None,
    audit_item: Mapping[str, Any] | None,
    only_cp_meta: bool,
) -> bool:
    cp_meta_missing = bool((audit_item or {}).get('cp_meta_missing'))
    if only_cp_meta:
        return cp_meta_missing
    pack_available = bool((surface_item or {}).get('pack_available'))
    eval_available = bool((surface_item or {}).get('eval_available'))
    warnings = list((surface_item or {}).get('warnings') or [])
    missing_or_warn = (not pack_available) or (not eval_available) or bool(warnings)
    stale_or_missing = bool((audit_item or {}).get('stale')) or bool((audit_item or {}).get('missing'))
    return cp_meta_missing or missing_or_warn or stale_or_missing


def _build_prepare_subargs(scope: Any) -> list[str]:
    return ['asset', 'prepare', '--asset', str(scope.asset), '--interval-sec', str(int(scope.interval_sec))]


def _build_candidate_subargs(scope: Any) -> list[str]:
    return ['asset', 'candidate', '--asset', str(scope.asset), '--interval-sec', str(int(scope.interval_sec))]


def _needs_prepare(paths: Mapping[str, Any] | None) -> bool:
    data_paths = (paths or {}).get('data')
    db_path = Path(getattr(data_paths, 'db_path', '')) if getattr(data_paths, 'db_path', None) else None
    dataset_path = Path(getattr(data_paths, 'dataset_path', '')) if getattr(data_paths, 'dataset_path', None) else None
    if db_path is None or dataset_path is None:
        return True
    if not db_path.exists():
        return True
    if not dataset_path.exists():
        return True
    try:
        return dataset_path.stat().st_size <= 0
    except OSError:
        return True


def _refresh_step_payload(*, repo_root: Path, config_path: Path, scope: Any) -> dict[str, Any]:
    return refresh_config_intelligence(
        repo_root=repo_root,
        config_path=config_path,
        asset=str(scope.asset),
        interval_sec=int(scope.interval_sec),
        rebuild_pack=True,
        materialize_portfolio=True,
    )


def _refresh_step(scope: Any, payload: Mapping[str, Any]) -> StepResult:
    ok = bool(payload.get('ok', True))
    item_match = None
    for item in list(payload.get('items') or []):
        if isinstance(item, Mapping) and str(item.get('scope_tag') or '') == str(scope.scope_tag):
            item_match = dict(item)
            ok = ok and bool(item.get('ok', True))
            break
    return StepResult(
        name='intelligence_refresh',
        scope_tag=str(scope.scope_tag),
        command=['python', '-m', 'natbin.control.app', 'intelligence-refresh'],
        returncode=0 if ok else 1,
        timed_out=False,
        stdout_tail='',
        stderr_tail='',
        ok=ok,
        parsed=dict(payload),
    )


def build_portfolio_intelligence_maintenance_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = False,
    only_cp_meta: bool = False,
    timeout_sec: int = 300,
    intelligence_timeout_sec: int = 300,
) -> dict[str, Any]:
    repo, cfg_path, cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    scope_by_tag = _scope_tag_map(scopes)
    before_surface = build_portfolio_intelligence_payload(repo_root=repo, config_path=cfg_path)
    before_audit = build_signal_artifact_audit_payload(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec, all_scopes=all_scopes)
    surface_map = _surface_map(before_surface)
    audit_map = _audit_map(before_audit)

    selected_scope_tags = [
        tag
        for tag in [str(getattr(scope, 'scope_tag', '') or '') for scope in scopes]
        if tag and _scope_needs_maintenance(
            surface_item=surface_map.get(tag),
            audit_item=audit_map.get(tag),
            only_cp_meta=bool(only_cp_meta),
        )
    ]

    steps: list[dict[str, Any]] = []
    for scope_tag in selected_scope_tags:
        scope = scope_by_tag[scope_tag]
        paths = resolve_scope_paths(repo_root=repo, cfg=cfg, scope=scope)
        if _needs_prepare(paths):
            run = _run_runtime_app_json(
                repo_root=repo,
                config_path=cfg_path,
                subargs=_build_prepare_subargs(scope),
                timeout_sec=timeout_sec,
            )
            steps.append(
                StepResult(
                    name='asset_prepare',
                    scope_tag=scope_tag,
                    command=list(run.get('command') or run.get('subargs') or []),
                    returncode=int(run.get('returncode', 1)),
                    timed_out=bool(run.get('timed_out', False)),
                    stdout_tail=str(run.get('stdout_tail', '')),
                    stderr_tail=str(run.get('stderr_tail', '')),
                    ok=bool(run.get('ok', False)),
                    parsed=run.get('parsed') if isinstance(run.get('parsed'), Mapping) else None,
                ).as_dict()
            )

        run = _run_runtime_app_json(
            repo_root=repo,
            config_path=cfg_path,
            subargs=_build_candidate_subargs(scope),
            timeout_sec=timeout_sec,
        )
        steps.append(
            StepResult(
                name='asset_candidate',
                scope_tag=scope_tag,
                command=list(run.get('command') or run.get('subargs') or []),
                returncode=int(run.get('returncode', 1)),
                timed_out=bool(run.get('timed_out', False)),
                stdout_tail=str(run.get('stdout_tail', '')),
                stderr_tail=str(run.get('stderr_tail', '')),
                ok=bool(run.get('ok', False)),
                parsed=run.get('parsed') if isinstance(run.get('parsed'), Mapping) else None,
            ).as_dict()
        )

        refresh_payload = _refresh_step_payload(repo_root=repo, config_path=cfg_path, scope=scope)
        steps.append(_refresh_step(scope, refresh_payload).as_dict())

    after_surface = build_portfolio_intelligence_payload(repo_root=repo, config_path=cfg_path)
    after_audit = build_signal_artifact_audit_payload(repo_root=repo, config_path=cfg_path, asset=asset, interval_sec=interval_sec, all_scopes=all_scopes)
    after_audit_map = _audit_map(after_audit)
    before_cp = {tag for tag, item in audit_map.items() if bool(item.get('cp_meta_missing'))}
    after_cp = {tag for tag, item in after_audit_map.items() if bool(item.get('cp_meta_missing'))}
    repaired_scope_tags = sorted(before_cp - after_cp)
    unresolved_scope_tags = sorted(after_cp & set(selected_scope_tags))

    def _summary_delta(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, Any]:
        keys = (
            'cp_meta_missing_scopes',
            'missing_artifact_scopes',
            'stale_artifact_scopes',
            'watch_scopes',
            'hold_scopes',
            'actionable_scopes',
        )
        out: dict[str, Any] = {}
        before_summary = dict((before or {}).get('summary') or {})
        after_summary = dict((after or {}).get('summary') or {})
        for key in keys:
            b = int(before_summary.get(key, 0) or 0)
            a = int(after_summary.get(key, 0) or 0)
            out[key] = {'before': b, 'after': a, 'delta': a - b}
        return out

    ok = all(bool(step.get('ok', False)) for step in steps) if steps else True
    return {
        'kind': 'portfolio_intelligence_maintenance',
        'at_utc': now_utc().isoformat(timespec='seconds'),
        'ok': ok,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'all_scopes': bool(all_scopes),
        'only_cp_meta': bool(only_cp_meta),
        'selected_scope_tags': selected_scope_tags,
        'repaired_scope_tags': repaired_scope_tags,
        'unresolved_scope_tags': unresolved_scope_tags,
        'before': {
            'portfolio_intelligence': before_surface,
            'signal_artifact_audit': before_audit,
        },
        'after': {
            'portfolio_intelligence': after_surface,
            'signal_artifact_audit': after_audit,
        },
        'summary_delta': _summary_delta(before_audit, after_audit),
        'steps': steps,
    }


__all__ = ['StepResult', 'build_portfolio_intelligence_maintenance_payload']
