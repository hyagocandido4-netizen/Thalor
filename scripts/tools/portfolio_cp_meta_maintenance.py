#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass
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


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    parsed: dict[str, Any] | None = None


def _repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parents[2]


def _python_executable(repo_root: Path) -> str:
    if os.name == 'nt':
        candidate = repo_root / '.venv' / 'Scripts' / 'python.exe'
    else:
        candidate = repo_root / '.venv' / 'bin' / 'python'
    return str(candidate if candidate.exists() else Path(sys.executable).resolve())


def _with_src_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    src = str(repo_root / 'src')
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = src if not existing else src + os.pathsep + existing
    return env


def extract_last_json(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    best_with_kind: dict[str, Any] | None = None
    i = 0
    while i < len(text):
        if text[i] != '{':
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text[i:])
        except Exception:
            i += 1
            continue
        if isinstance(obj, dict):
            best = obj
            if obj.get('kind'):
                best_with_kind = obj
        i += max(end, 1)
    return best_with_kind or best


def _stdout_tail(text: str, *, limit: int = 1600) -> str:
    return (text or '')[-limit:]


def run_command(repo_root: Path, cmd: list[str], *, timeout_sec: int) -> CommandResult:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            env=_with_src_env(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        stdout = proc.stdout or ''
        parsed = extract_last_json(stdout)
        return CommandResult(
            command=cmd,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=proc.stderr or '',
            timed_out=False,
            parsed=parsed,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ''
        parsed = extract_last_json(stdout)
        return CommandResult(
            command=cmd,
            returncode=124,
            stdout=stdout,
            stderr=exc.stderr or '',
            timed_out=True,
            parsed=parsed,
        )


def run_script(repo_root: Path, script_rel: str, args: list[str], *, timeout_sec: int) -> CommandResult:
    python_exe = _python_executable(repo_root)
    script = repo_root / script_rel
    cmd = [python_exe, str(script), *args]
    return run_command(repo_root, cmd, timeout_sec=timeout_sec)


def run_control_app(repo_root: Path, config: str, argv: list[str], *, timeout_sec: int) -> CommandResult:
    python_exe = _python_executable(repo_root)
    cmd = [python_exe, '-m', 'natbin.control.app', '--repo-root', str(repo_root), '--config', config, *argv, '--json']
    return run_command(repo_root, cmd, timeout_sec=timeout_sec)


def run_closure_report(repo_root: Path, config: str, timeout_sec: int = 420) -> dict[str, Any]:
    res = run_script(
        repo_root,
        'scripts/tools/portfolio_canary_closure_report.py',
        ['--config', config, '--all-scopes', '--json'],
        timeout_sec=timeout_sec,
    )
    payload = res.parsed
    if res.returncode != 0 or not isinstance(payload, dict):
        raise RuntimeError(f'Closure report inválido. returncode={res.returncode} stderr_tail={_stdout_tail(res.stderr)}')
    return payload


def run_signal_artifact_audit(repo_root: Path, config: str, timeout_sec: int = 300) -> dict[str, Any]:
    res = run_script(
        repo_root,
        'scripts/tools/portfolio_signal_artifact_audit.py',
        ['--config', config, '--all-scopes', '--json'],
        timeout_sec=timeout_sec,
    )
    payload = res.parsed
    if res.returncode != 0 or not isinstance(payload, dict):
        raise RuntimeError(f'Signal artifact audit inválido. returncode={res.returncode} stderr_tail={_stdout_tail(res.stderr)}')
    return payload


def parse_scope_tag(scope_tag: str) -> tuple[str, int]:
    if '_' not in scope_tag:
        raise ValueError(f'scope_tag inválido: {scope_tag}')
    asset, interval_tag = scope_tag.rsplit('_', 1)
    if not interval_tag.endswith('s'):
        raise ValueError(f'intervalo inválido em scope_tag: {scope_tag}')
    return asset, int(interval_tag[:-1])


def _candidate_payload(parsed: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(parsed, Mapping):
        return {}
    candidate = parsed.get('candidate')
    return candidate if isinstance(candidate, Mapping) else {}


def candidate_cp_meta_missing(parsed: Mapping[str, Any] | None) -> bool:
    candidate = _candidate_payload(parsed)
    raw = candidate.get('raw') if isinstance(candidate.get('raw'), Mapping) else {}
    haystack = ' '.join(
        [
            str(candidate.get('reason') or ''),
            str(candidate.get('blockers') or ''),
            str(raw.get('gate_fail_detail') or ''),
            str(raw.get('gate_mode') or ''),
            str(raw.get('gate_mode_requested') or ''),
        ]
    ).lower()
    return 'cp_fail_closed_missing_cp' in haystack or 'missing_cp_meta' in haystack


def _summarize_step(name: str, scope_tag: str, result: CommandResult, *, extra_ok: bool = True) -> StepResult:
    ok = bool(result.returncode == 0 and not result.timed_out and extra_ok)
    return StepResult(
        name=name,
        scope_tag=scope_tag,
        command=result.command,
        returncode=result.returncode,
        timed_out=result.timed_out,
        stdout_tail=_stdout_tail(result.stdout),
        stderr_tail=_stdout_tail(result.stderr),
        ok=ok,
        parsed=result.parsed,
    )


def execute_scope_maintenance(
    repo_root: Path,
    config: str,
    scope_tag: str,
    *,
    timeout_sec: int,
    intelligence_timeout_sec: int,
    dry_run: bool,
) -> list[StepResult]:
    asset, interval_sec = parse_scope_tag(scope_tag)
    if dry_run:
        return [
            StepResult('asset_prepare', scope_tag, ['DRY_RUN', 'asset', 'prepare', asset, str(interval_sec)], 0, False, '', '', True),
            StepResult('asset_candidate', scope_tag, ['DRY_RUN', 'asset', 'candidate', asset, str(interval_sec)], 0, False, '', '', True),
            StepResult('intelligence_refresh', scope_tag, ['DRY_RUN', 'intelligence-refresh', asset, str(interval_sec)], 0, False, '', '', True),
        ]

    steps: list[StepResult] = []
    prepare_res = run_control_app(
        repo_root,
        config,
        ['asset', 'prepare', '--asset', asset, '--interval-sec', str(interval_sec)],
        timeout_sec=timeout_sec,
    )
    steps.append(_summarize_step('asset_prepare', scope_tag, prepare_res))

    candidate_res = run_control_app(
        repo_root,
        config,
        ['asset', 'candidate', '--asset', asset, '--interval-sec', str(interval_sec)],
        timeout_sec=timeout_sec,
    )
    steps.append(
        _summarize_step(
            'asset_candidate',
            scope_tag,
            candidate_res,
            extra_ok=not candidate_cp_meta_missing(candidate_res.parsed),
        )
    )

    refresh_res = run_control_app(
        repo_root,
        config,
        ['intelligence-refresh', '--asset', asset, '--interval-sec', str(interval_sec)],
        timeout_sec=intelligence_timeout_sec,
    )
    steps.append(_summarize_step('intelligence_refresh', scope_tag, refresh_res))
    return steps


def _debt_scope_tags(closure: Mapping[str, Any], audit: Mapping[str, Any]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in list(closure.get('repair_scope_tags') or []):
        tag = str(item or '').strip()
        if tag and tag not in seen:
            ordered.append(tag)
            seen.add(tag)
    for debt in list(closure.get('closure_debts') or []):
        if not isinstance(debt, Mapping) or str(debt.get('name') or '') != 'secondary_cp_meta_debt':
            continue
        for item in list(debt.get('scope_tags') or []):
            tag = str(item or '').strip()
            if tag and tag not in seen:
                ordered.append(tag)
                seen.add(tag)
    for item in list(audit.get('scope_results') or []):
        if not isinstance(item, Mapping) or not _needs_repair_item(item):
            continue
        scope = item.get('scope') if isinstance(item.get('scope'), Mapping) else {}
        tag = str(scope.get('scope_tag') or '').strip()
        if tag and tag not in seen:
            ordered.append(tag)
            seen.add(tag)
    return ordered


def _cp_meta_scope_set(audit: Mapping[str, Any] | None) -> set[str]:
    out: set[str] = set()
    if not isinstance(audit, Mapping):
        return out
    for item in list(audit.get('scope_results') or []):
        if not isinstance(item, Mapping) or not bool(item.get('cp_meta_missing')):
            continue
        scope = item.get('scope') if isinstance(item.get('scope'), Mapping) else {}
        tag = str(scope.get('scope_tag') or '').strip()
        if tag:
            out.add(tag)
    return out


def _summary_delta(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, Any]:
    b = dict((before or {}).get('summary') or {}) if isinstance(before, Mapping) else {}
    a = dict((after or {}).get('summary') or {}) if isinstance(after, Mapping) else {}
    keys = ['cp_meta_missing_scopes', 'missing_artifact_scopes', 'stale_artifact_scopes', 'watch_scopes', 'hold_scopes', 'actionable_scopes']
    return {
        key: {
            'before': int(b.get(key) or 0),
            'after': int(a.get(key) or 0),
            'delta': int(a.get(key) or 0) - int(b.get(key) or 0),
        }
        for key in keys
    }



def _needs_repair_item(item: Mapping[str, Any] | None) -> bool:
    if not isinstance(item, Mapping):
        return False
    if bool(item.get('cp_meta_missing')) or bool(item.get('stale')) or bool(item.get('missing')):
        return True
    if not bool(item.get('exists', True)):
        return True
    if str(item.get('dominant_reason') or '') == 'missing_artifact':
        return True
    flags = item.get('blocker_flags') if isinstance(item.get('blocker_flags'), Mapping) else {}
    return bool(flags.get('gate_fail_closed'))


def _repair_scope_set(audit: Mapping[str, Any] | None, selected_scope_tags: Iterable[str] | None = None) -> set[str]:
    selected = {str(tag) for tag in list(selected_scope_tags or []) if str(tag)}
    out: set[str] = set()
    if not isinstance(audit, Mapping):
        return out
    for item in list(audit.get('scope_results') or []):
        if not _needs_repair_item(item if isinstance(item, Mapping) else None):
            continue
        scope = item.get('scope') if isinstance(item, Mapping) and isinstance(item.get('scope'), Mapping) else {}
        tag = str(scope.get('scope_tag') or '').strip()
        if not tag:
            continue
        if selected and tag not in selected:
            continue
        out.add(tag)
    return out


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Manutenção segura de dívida secundária de cp_meta no canary.')
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--config', required=True)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--max-scopes', type=int, default=6)
    parser.add_argument('--timeout-sec', type=int, default=240)
    parser.add_argument('--intelligence-timeout-sec', type=int, default=420)
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = _repo_root(args.repo_root)
    closure_before = run_closure_report(repo_root=repo_root, config=args.config)
    audit_before = run_signal_artifact_audit(repo_root=repo_root, config=args.config)
    selected_scopes = _debt_scope_tags(closure_before, audit_before)[: max(args.max_scopes, 0)]

    all_steps: list[StepResult] = []
    for scope_tag in selected_scopes:
        all_steps.extend(
            execute_scope_maintenance(
                repo_root=repo_root,
                config=args.config,
                scope_tag=scope_tag,
                timeout_sec=args.timeout_sec,
                intelligence_timeout_sec=args.intelligence_timeout_sec,
                dry_run=args.dry_run,
            )
        )

    audit_after = run_signal_artifact_audit(repo_root=repo_root, config=args.config)
    closure_after = run_closure_report(repo_root=repo_root, config=args.config)

    before_repair = _repair_scope_set(audit_before, selected_scopes)
    after_repair = _repair_scope_set(audit_after, selected_scopes)
    repaired_scope_tags = sorted(before_repair - after_repair)
    unresolved_scope_tags = sorted(after_repair)

    step_ok = all(step.ok for step in all_steps) if all_steps else True
    repair_improved = (len(after_repair) < len(before_repair)) or (not before_repair and not after_repair)
    ok = bool(step_ok and repair_improved)
    payload = {
        'kind': 'portfolio_cp_meta_maintenance',
        'ok': ok,
        'severity': 'ok' if ok else 'warn',
        'repo_root': str(repo_root),
        'config_path': str((repo_root / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config).resolve()),
        'dry_run': args.dry_run,
        'selected_scope_tags': selected_scopes,
        'repaired_scope_tags': repaired_scope_tags,
        'unresolved_scope_tags': unresolved_scope_tags,
        'before': {
            'closure_state': closure_before.get('closure_state'),
            'closure_recommended_action': closure_before.get('recommended_action'),
            'signal_audit_summary': dict(audit_before.get('summary') or {}),
        },
        'after': {
            'closure_state': closure_after.get('closure_state'),
            'closure_recommended_action': closure_after.get('recommended_action'),
            'signal_audit_summary': dict(audit_after.get('summary') or {}),
        },
        'summary_delta': _summary_delta(audit_before, audit_after),
        'steps': [
            {
                'name': step.name,
                'scope_tag': step.scope_tag,
                'command': step.command,
                'returncode': step.returncode,
                'timed_out': step.timed_out,
                'ok': step.ok,
                'stdout_tail': step.stdout_tail,
                'stderr_tail': step.stderr_tail,
                'parsed': step.parsed,
            }
            for step in all_steps
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
