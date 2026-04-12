from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.ops.diagnostic_utils import load_selected_scopes  # noqa: E402
from natbin.ops.provider_session_governor import build_provider_session_governor_payload  # noqa: E402
from natbin.ops.safe_refresh import refresh_market_context_safe  # noqa: E402
from natbin.utils.provider_issue_taxonomy import aggregate_provider_issue_texts  # noqa: E402


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _find_python(repo_root: Path) -> str:
    for candidate in (repo_root / '.venv' / 'Scripts' / 'python.exe', repo_root / '.venv' / 'bin' / 'python'):
        if candidate.exists():
            return str(candidate)
    return shutil.which('python') or sys.executable


def _build_env(repo_root: Path, config_path: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    src = repo_root / 'src'
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(src) if not existing else f"{src}{os.pathsep}{existing}"
    env['THALOR_REPO_ROOT'] = str(repo_root)
    if config_path is not None:
        env['THALOR_CONFIG'] = str(config_path)
        env['THALOR_CONFIG_PATH'] = str(config_path)
    return env


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _market_context_state(repo_root: Path, scope_tag: str, interval_sec: int) -> dict[str, Any]:
    path = repo_root / 'runs' / f'market_context_{scope_tag}.json'
    out: dict[str, Any] = {'path': str(path), 'exists': path.exists(), 'fresh': False, 'age_sec': None, 'at_utc': None}
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return out
    at_utc = payload.get('at_utc')
    out['at_utc'] = at_utc
    try:
        if isinstance(at_utc, str) and at_utc:
            when = datetime.fromisoformat(at_utc.replace('Z', '+00:00'))
            age = max(0.0, (datetime.now(tz=UTC) - when.astimezone(UTC)).total_seconds())
            out['age_sec'] = round(age, 3)
            out['fresh'] = bool(age <= max(int(interval_sec) * 3, 900))
    except Exception:
        pass
    out['market_open'] = payload.get('market_open')
    out['open_source'] = payload.get('open_source')
    out['dependency_available'] = payload.get('dependency_available')
    out['dependency_reason'] = payload.get('dependency_reason')
    return out


def _last_json_from_stdout(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    lines = text.splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not (line.startswith('{') and line.endswith('}')):
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None


def _sleep_between_scopes(ms: int) -> None:
    delay = max(0.0, float(ms or 0) / 1000.0)
    if delay > 0:
        time.sleep(delay)


def _run_asset_prepare(*, repo: Path, cfg_path: Path, asset: str, interval_sec: int, timeout_sec: int, env: dict[str, str], python_exe: str) -> dict[str, Any]:
    cmd = [
        python_exe, '-m', 'natbin.runtime_app', '--repo-root', str(repo), '--config', str(cfg_path),
        'asset', 'prepare', '--asset', asset, '--interval-sec', str(int(interval_sec)), '--json',
    ]
    started = time.monotonic()
    timed_out = False
    stdout = ''
    stderr = ''
    returncode: int | None = None
    try:
        proc = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True, timeout=max(1, int(timeout_sec)))
        stdout = proc.stdout or ''
        stderr = proc.stderr or ''
        returncode = int(proc.returncode)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = (exc.stdout or b'').decode('utf-8', errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        stderr = (exc.stderr or b'').decode('utf-8', errors='replace') if isinstance(exc.stderr, bytes) else (exc.stderr or '')
    return {
        'command': cmd,
        'returncode': returncode,
        'timed_out': timed_out,
        'duration_sec': round(time.monotonic() - started, 3),
        'stdout_tail': '\n'.join(stdout.splitlines()[-12:]),
        'stderr_tail': '\n'.join(stderr.splitlines()[-12:]),
        'last_json': _last_json_from_stdout(stdout),
        'kind': 'ok' if returncode == 0 and not timed_out else 'timeout' if timed_out else 'nonzero_exit',
    }


def build_warmup_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = True,
    timeout_sec: int = 900,
    force_refresh_all: bool = False,
    legacy_full_prepare: bool = False,
    refresh_timeout_sec: int | None = None,
    prepare_timeout_sec: int | None = None,
    max_prepare_fallback_scopes: int | None = None,
    sleep_between_scopes_ms: int | None = None,
    refresh_stability: bool = False,
) -> dict[str, Any]:
    repo, cfg_path, _cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    if not scopes:
        return {
            'kind': 'portfolio_canary_warmup',
            'at_utc': _now_iso(),
            'ok': False,
            'severity': 'error',
            'repo_root': str(repo),
            'config_path': str(cfg_path),
            'message': 'no_scopes_selected',
            'scope_results': [],
        }

    governor_payload = build_provider_session_governor_payload(
        repo_root=repo,
        config_path=cfg_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
        active_provider_probe=True,
        refresh_stability=bool(refresh_stability),
        write_artifact=False,
    )
    governor = dict(governor_payload.get('governor') or {})
    governor_mode = str(governor.get('mode') or 'normal')
    skip_fresh = bool(governor.get('skip_fresh_market_context_scopes', True)) and not bool(force_refresh_all)
    refresh_timeout = int(refresh_timeout_sec if refresh_timeout_sec is not None else governor.get('refresh_market_context_timeout_sec') or min(int(timeout_sec), 90))
    prepare_timeout = int(prepare_timeout_sec if prepare_timeout_sec is not None else governor.get('asset_prepare_timeout_sec') or min(int(timeout_sec), 300))
    sleep_ms = int(sleep_between_scopes_ms if sleep_between_scopes_ms is not None else governor.get('sleep_between_scopes_ms') or 0)
    prepare_budget = int(max_prepare_fallback_scopes if max_prepare_fallback_scopes is not None else governor.get('max_asset_prepare_fallback_scopes') or len(scopes))

    python_exe = _find_python(repo)
    env = _build_env(repo, cfg_path)
    scope_results: list[dict[str, Any]] = []
    fresh_count = 0
    ok_count = 0
    effective_ok_count = 0
    prepare_failed_but_fresh = 0
    prepare_failed_and_stale = 0
    skipped_fresh_scopes = 0
    refresh_safe_scopes = 0
    asset_prepare_fallback_scopes = 0

    for index, scope in enumerate(scopes):
        scope_tag = str(scope.scope_tag)
        asset_name = str(scope.asset)
        interval = int(scope.interval_sec)
        before = _market_context_state(repo, scope_tag, interval)
        strategy = 'skip_fresh'
        command = None
        returncode = 0
        timed_out = False
        duration_sec = 0.0
        stdout_tail = ''
        stderr_tail = ''
        last_json = None
        command_ok = False
        step: dict[str, Any] | None = None

        if bool(before.get('fresh')) and skip_fresh and not legacy_full_prepare:
            skipped_fresh_scopes += 1
            command_ok = False
            strategy = 'skip_fresh'
            after = before
        else:
            if legacy_full_prepare:
                strategy = 'asset_prepare'
                step = _run_asset_prepare(repo=repo, cfg_path=cfg_path, asset=asset_name, interval_sec=interval, timeout_sec=prepare_timeout, env=env, python_exe=python_exe)
                command = step.get('command')
                returncode = int(step.get('returncode') or 0)
                timed_out = bool(step.get('timed_out'))
                duration_sec = float(step.get('duration_sec') or 0.0)
                stdout_tail = str(step.get('stdout_tail') or '')
                stderr_tail = str(step.get('stderr_tail') or '')
                last_json = step.get('last_json') if isinstance(step.get('last_json'), dict) else None
                command_ok = returncode == 0 and not timed_out
                after = _market_context_state(repo, scope_tag, interval)
            else:
                strategy = 'refresh_market_context_safe'
                step = refresh_market_context_safe(repo_root=repo, config_path=cfg_path, asset=asset_name, interval_sec=interval, timeout_sec=refresh_timeout)
                command = step.get('command')
                step_returncode = step.get('returncode')
                returncode = int(step_returncode) if step_returncode not in (None, '') else None
                timed_out = bool(step.get('timed_out'))
                duration_sec = float(step.get('duration_sec') or 0.0)
                stdout_tail = str(step.get('stdout_tail') or '')
                stderr_tail = str(step.get('stderr_tail') or '')
                command_ok = bool(step.get('kind') == 'ok' and returncode == 0 and not timed_out)
                last_json = _last_json_from_stdout(str(step.get('stdout_tail') or ''))
                if command_ok:
                    refresh_safe_scopes += 1
                after = _market_context_state(repo, scope_tag, interval)
                if not (command_ok or bool(after.get('fresh'))) and prepare_budget > 0 and governor_mode != 'hold_only':
                    prepare_budget -= 1
                    strategy = 'asset_prepare_fallback'
                    step2 = _run_asset_prepare(repo=repo, cfg_path=cfg_path, asset=asset_name, interval_sec=interval, timeout_sec=prepare_timeout, env=env, python_exe=python_exe)
                    command = step2.get('command')
                    returncode = int(step2.get('returncode') or 0)
                    timed_out = bool(step2.get('timed_out'))
                    duration_sec = float(step.get('duration_sec') or 0.0) + float(step2.get('duration_sec') or 0.0)
                    stdout_tail = '\n'.join([part for part in (stdout_tail, str(step2.get('stdout_tail') or '')) if part])
                    stderr_tail = '\n'.join([part for part in (stderr_tail, str(step2.get('stderr_tail') or '')) if part])
                    last_json = step2.get('last_json') if isinstance(step2.get('last_json'), dict) else None
                    command_ok = returncode == 0 and not timed_out
                    asset_prepare_fallback_scopes += 1
                    after = _market_context_state(repo, scope_tag, interval)
                elif not (command_ok or bool(after.get('fresh'))):
                    strategy = 'refresh_only_insufficient'

        is_fresh = bool(after.get('fresh'))
        if is_fresh:
            fresh_count += 1
        if command_ok:
            ok_count += 1
        effective_ok = bool(command_ok or is_fresh)
        if effective_ok:
            effective_ok_count += 1
        if (not command_ok) and is_fresh:
            prepare_failed_but_fresh += 1
        if (not command_ok) and (not is_fresh):
            prepare_failed_and_stale += 1

        issue_inputs: list[Any] = [stderr_tail]
        if timed_out:
            issue_inputs.append('timeout')
        if returncode not in (None, 0):
            issue_inputs.append(f"returncode={returncode}")
            issue_inputs.append(stdout_tail)
        if isinstance(step, dict) and step.get('message') not in (None, '', 'market_context_fresh'):
            issue_inputs.append(step.get('message'))
        issue_categories = aggregate_provider_issue_texts(issue_inputs)
        scope_results.append({
            'scope': {'asset': asset_name, 'interval_sec': interval, 'scope_tag': scope_tag},
            'strategy': strategy,
            'command': command,
            'returncode': returncode,
            'timed_out': timed_out,
            'duration_sec': round(duration_sec, 3),
            'ok': command_ok,
            'effective_ok': effective_ok,
            'stdout_tail': stdout_tail,
            'stderr_tail': stderr_tail,
            'issue_categories': issue_categories,
            'last_json': last_json,
            'market_context_before': before,
            'market_context': after,
        })

        if index < len(scopes) - 1 and strategy != 'skip_fresh':
            _sleep_between_scopes(sleep_ms)

    if effective_ok_count == len(scope_results):
        severity = 'ok'
    elif effective_ok_count > 0:
        severity = 'warn'
    else:
        severity = 'error'

    payload = {
        'kind': 'portfolio_canary_warmup',
        'at_utc': _now_iso(),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'all_scopes': bool(all_scopes),
        'governor': governor_payload,
        'summary': {
            'scope_count': len(scope_results),
            'prepare_ok_scopes': ok_count,
            'effective_ready_scopes': effective_ok_count,
            'fresh_market_context_scopes': fresh_count,
            'prepare_failed_but_fresh_scopes': prepare_failed_but_fresh,
            'prepare_failed_and_stale_scopes': prepare_failed_and_stale,
            'skipped_fresh_scopes': skipped_fresh_scopes,
            'refresh_safe_scopes': refresh_safe_scopes,
            'asset_prepare_fallback_scopes': asset_prepare_fallback_scopes,
            'timeout_scopes': sum(1 for item in scope_results if item.get('timed_out')),
            'governor_mode': governor_mode,
            'issue_category_counts': {
                category.get('category'): int(category.get('count') or 0)
                for item in scope_results
                for category in list((item.get('issue_categories') or {}).get('categories') or [])
                if isinstance(category, dict)
            },
        },
        'scope_results': scope_results,
        'actions': [
            'Use evidence-window-scan logo após o warmup para ranquear os scopes com artifacts atualizados.',
            'Se prepare falhar mas market_context continuar fresh, trate o warmup como operacionalmente suficiente e siga para o signal proof.',
            'Se algum scope terminar stale, inspecione o stderr_tail do warmup e o provider path desse scope.',
            'O Provider Session Governor já serializa o fan-out do canary; preserve max_parallel_assets=1 enquanto stability_state permanecer degraded.',
        ],
    }
    artifact = repo / 'runs' / 'control' / '_repo' / 'portfolio_canary_warmup.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Warmup seguro do portfolio canary: atualiza artifacts de todos os scopes sem submeter ordens, usando Provider Session Governor para pacing/fallback.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--asset', default=None)
    ap.add_argument('--interval-sec', type=int, default=None)
    ap.add_argument('--all-scopes', action='store_true')
    ap.add_argument('--timeout-sec', type=int, default=900)
    ap.add_argument('--force-refresh-all', action='store_true')
    ap.add_argument('--legacy-full-prepare', action='store_true')
    ap.add_argument('--refresh-timeout-sec', type=int, default=None)
    ap.add_argument('--prepare-timeout-sec', type=int, default=None)
    ap.add_argument('--max-prepare-fallback-scopes', type=int, default=None)
    ap.add_argument('--sleep-between-scopes-ms', type=int, default=None)
    ap.add_argument('--refresh-stability', action='store_true')
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_warmup_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        all_scopes=bool(ns.all_scopes),
        timeout_sec=int(ns.timeout_sec or 900),
        force_refresh_all=bool(ns.force_refresh_all),
        legacy_full_prepare=bool(ns.legacy_full_prepare),
        refresh_timeout_sec=ns.refresh_timeout_sec,
        prepare_timeout_sec=ns.prepare_timeout_sec,
        max_prepare_fallback_scopes=ns.max_prepare_fallback_scopes,
        sleep_between_scopes_ms=ns.sleep_between_scopes_ms,
        refresh_stability=bool(ns.refresh_stability),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
