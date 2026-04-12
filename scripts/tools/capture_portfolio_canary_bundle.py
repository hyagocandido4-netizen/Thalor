from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _capture_json import write_json_summary


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]




def _tool_script(repo_root: Path, name: str) -> str:
    return str(repo_root / 'scripts' / 'tools' / name)

def _default_config(repo_root: Path) -> Path | None:
    for rel in (
        'config/practice_portfolio_canary.yaml',
        'config/multi_asset_practice.yaml',
        'config/multi_asset.yaml',
        'config/live_controlled_practice.yaml',
        'config/base.yaml',
    ):
        path = repo_root / rel
        if path.exists():
            return path
    return None


def _find_python(repo_root: Path) -> str:
    for candidate in (repo_root / '.venv' / 'Scripts' / 'python.exe', repo_root / '.venv' / 'bin' / 'python'):
        if candidate.exists():
            return str(candidate)
    return shutil.which('python') or sys.executable


def _build_env(repo_root: Path, config_path: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    src = repo_root / 'src'
    existing_pythonpath = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(src) if not existing_pythonpath else f"{src}{os.pathsep}{existing_pythonpath}"
    env['THALOR_REPO_ROOT'] = str(repo_root)
    if config_path is not None:
        env['THALOR_CONFIG'] = str(config_path)
        env['THALOR_CONFIG_PATH'] = str(config_path)
    return env


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _safe_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _zip_dir(source_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob('*')):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(source_dir)))


def _run_one(*, name: str, argv: list[str], cwd: Path, env: dict[str, str], timeout_sec: int, output_dir: Path) -> dict[str, Any]:
    started = _now_utc()
    started_monotonic = time.monotonic()
    timed_out = False
    stdout_text = ''
    stderr_text = ''
    returncode = None
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=max(1, int(timeout_sec)))
        returncode = int(proc.returncode)
        stdout_text = _safe_text(proc.stdout)
        stderr_text = _safe_text(proc.stderr)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout_text = _safe_text(exc.stdout)
        stderr_text = _safe_text(exc.stderr)
    duration_sec = round(time.monotonic() - started_monotonic, 3)
    finished = _now_utc()

    step_dir = output_dir / name
    step_dir.mkdir(parents=True, exist_ok=True)
    _write_text(step_dir / 'stdout.txt', stdout_text)
    _write_text(step_dir / 'stderr.txt', stderr_text)
    _write_text(step_dir / 'command.txt', ' '.join(argv) + '\n')
    _write_text(step_dir / 'exit_code.txt', f'{returncode}\n')
    parsed_summary = write_json_summary(base_dir=step_dir, stdout_text=stdout_text)
    result = {
        'name': name,
        'command': argv,
        'started_at_utc': started,
        'finished_at_utc': finished,
        'duration_sec': duration_sec,
        'timeout_sec': timeout_sec,
        'timed_out': timed_out,
        'returncode': returncode,
        'parsed_summary': parsed_summary,
    }
    _write_text(step_dir / 'manifest.json', json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def _last_json(bundle_dir: Path, name: str) -> dict[str, Any] | None:
    path = bundle_dir / name / 'last_json.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _bundle_summary(bundle_dir: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
    scan = _last_json(bundle_dir, 'evidence_window_scan') or {}
    candidate = _last_json(bundle_dir, 'asset_candidate_best') or {}
    provider_probe = _last_json(bundle_dir, 'provider_probe_all_scopes') or {}
    production_gate = _last_json(bundle_dir, 'production_gate_all_scopes') or {}
    portfolio_status = _last_json(bundle_dir, 'portfolio_status') or {}
    warmup = _last_json(bundle_dir, 'portfolio_canary_warmup') or {}
    governor = _last_json(bundle_dir, 'provider_session_governor') or {}
    signal_scan = _last_json(bundle_dir, 'portfolio_canary_signal_scan') or {}
    signal_audit = _last_json(bundle_dir, 'signal_artifact_audit') or {}
    scan_summary = scan.get('summary') if isinstance(scan, dict) else {}
    signal_summary = signal_scan.get('summary') if isinstance(signal_scan, dict) else {}
    signal_audit_summary = signal_audit.get('summary') if isinstance(signal_audit, dict) else {}
    provider_summary = provider_probe.get('summary') if isinstance(provider_probe, dict) else {}
    governor_summary = governor.get('summary') if isinstance(governor, dict) else {}
    gate_summary = production_gate.get('summary') if isinstance(production_gate, dict) else {}
    recommended_scope = signal_scan.get('best_actionable_scope') or signal_scan.get('best_watch_scope') or signal_audit.get('best_actionable_scope') or signal_audit.get('best_watch_scope') or scan.get('recommended_scope') or scan.get('best_scope')
    scope_count = None
    if isinstance(portfolio_status, dict):
        scope_count = ((portfolio_status.get('multi_asset') or {}).get('asset_count')) or ((scan_summary or {}).get('scope_count'))
    return {
        'portfolio_canary_warmup': next((r.get('parsed_summary') for r in results if r.get('name') == 'portfolio_canary_warmup'), None),
        'evidence_window_scan': next((r.get('parsed_summary') for r in results if r.get('name') == 'evidence_window_scan'), None),
        'provider_probe_all_scopes': next((r.get('parsed_summary') for r in results if r.get('name') == 'provider_probe_all_scopes'), None),
        'production_gate_all_scopes': next((r.get('parsed_summary') for r in results if r.get('name') == 'production_gate_all_scopes'), None),
        'portfolio_status': next((r.get('parsed_summary') for r in results if r.get('name') == 'portfolio_status'), None),
        'asset_candidate_best': next((r.get('parsed_summary') for r in results if r.get('name') == 'asset_candidate_best'), None),
        'provider_session_governor': next((r.get('parsed_summary') for r in results if r.get('name') == 'provider_session_governor'), None),
        'portfolio_canary_signal_scan': next((r.get('parsed_summary') for r in results if r.get('name') == 'portfolio_canary_signal_scan'), None),
        'signal_artifact_audit': next((r.get('parsed_summary') for r in results if r.get('name') == 'signal_artifact_audit'), None),
        'portfolio_canary': {
            'recommended_scope': recommended_scope,
            'scan_severity': scan.get('severity') if isinstance(scan, dict) and scan else signal_scan.get('severity'),
            'scan_ok': scan.get('ok') if isinstance(scan, dict) and scan else signal_scan.get('ok'),
            'provider_ready_scopes': (
                (provider_summary or {}).get('provider_ready_scopes')
                or (scan_summary or {}).get('provider_ready_scopes')
                or (gate_summary or {}).get('provider_ready_count')
                or (governor_summary or {}).get('provider_ready_scopes')
            ),
            'scope_count': scope_count,
            'ready_for_all_scopes': production_gate.get('ready_for_all_scopes') if isinstance(production_gate, dict) else None,
            'candidate_latest_action': candidate.get('candidate', {}).get('action') if isinstance(candidate.get('candidate'), dict) else None,
            'candidate_ok': candidate.get('ok') if isinstance(candidate, dict) else None,
            'best_scope_window_state': (recommended_scope or {}).get('window_state') if isinstance(recommended_scope, dict) else None,
            'signal_scan_recommended_action': (signal_summary or {}).get('recommended_action') if isinstance(signal_summary, dict) else None,
            'signal_scan_actionable_scopes': (signal_summary or {}).get('actionable_scopes') if isinstance(signal_summary, dict) else None,
            'signal_scan_watch_scopes': (signal_summary or {}).get('watch_scopes') if isinstance(signal_summary, dict) else None,
            'signal_scan_hold_scopes': (signal_summary or {}).get('hold_scopes') if isinstance(signal_summary, dict) else None,
            'signal_scan_cp_meta_missing_scopes': (signal_summary or {}).get('cp_meta_missing_scopes') if isinstance(signal_summary, dict) else None,
            'signal_scan_regime_block_scopes': (signal_summary or {}).get('regime_block_scopes') if isinstance(signal_summary, dict) else None,
            'signal_scan_threshold_block_scopes': (signal_summary or {}).get('threshold_block_scopes') if isinstance(signal_summary, dict) else None,
            'signal_scan_topk_suppressed_scopes': (signal_summary or {}).get('topk_suppressed_scopes') if isinstance(signal_summary, dict) else None,
            'signal_scan_healthy_waiting_signal': (signal_summary or {}).get('healthy_waiting_signal') if isinstance(signal_summary, dict) else None,
            'signal_scan_dominant_nontrade_reason': (signal_summary or {}).get('dominant_nontrade_reason') if isinstance(signal_summary, dict) else None,
            'signal_scan_best_watch_scope_tag': (signal_summary or {}).get('best_watch_scope_tag') if isinstance(signal_summary, dict) else None,
            'signal_scan_best_hold_scope_tag': (signal_summary or {}).get('best_hold_scope_tag') if isinstance(signal_summary, dict) else None,
            'signal_audit_recommended_action': (signal_audit_summary or {}).get('recommended_action') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_full_scope_count': (signal_audit_summary or {}).get('full_scope_count') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_actionable_scopes': (signal_audit_summary or {}).get('actionable_scopes') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_watch_scopes': (signal_audit_summary or {}).get('watch_scopes') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_hold_scopes': (signal_audit_summary or {}).get('hold_scopes') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_missing_artifact_scopes': (signal_audit_summary or {}).get('missing_artifact_scopes') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_stale_artifact_scopes': (signal_audit_summary or {}).get('stale_artifact_scopes') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_cp_meta_missing_scopes': (signal_audit_summary or {}).get('cp_meta_missing_scopes') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_regime_block_scopes': (signal_audit_summary or {}).get('regime_block_scopes') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_threshold_block_scopes': (signal_audit_summary or {}).get('threshold_block_scopes') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_topk_suppressed_scopes': (signal_audit_summary or {}).get('topk_suppressed_scopes') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_dominant_nontrade_reason': (signal_audit_summary or {}).get('dominant_nontrade_reason') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_best_watch_scope_tag': (signal_audit_summary or {}).get('best_watch_scope_tag') if isinstance(signal_audit_summary, dict) else None,
            'signal_audit_best_hold_scope_tag': (signal_audit_summary or {}).get('best_hold_scope_tag') if isinstance(signal_audit_summary, dict) else None,
            'warmup_ok': warmup.get('ok') if isinstance(warmup, dict) else None,
            'governor_mode': (governor.get('summary') or {}).get('governor_mode') if isinstance(governor, dict) else None,
            'governor_sleep_between_scopes_ms': (governor.get('summary') or {}).get('sleep_between_scopes_ms') if isinstance(governor, dict) else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Gera um ZIP read-only do portfolio canary: warmup + gate all-scopes em modo cached + evidence scan + signal scan + asset candidate do melhor scope.')
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--config', default=None)
    parser.add_argument('--out-dir', default=None, help='Default: runs/debug/bundles')
    parser.add_argument('--timeout-sec', type=int, default=1800)
    parser.add_argument('--no-warmup', action='store_true', help='Não executa o warmup seguro de todos os scopes antes do bundle.')
    parser.add_argument('--include-standalone-provider-probe', action='store_true', help='Inclui provider-probe --all-scopes como etapa separada (mais lento). Por padrão o bundle usa o provider embutido no evidence-window-scan e no production-gate.')
    parser.add_argument('--sample-candles', type=int, default=3)
    parser.add_argument('--market-context-max-age-sec', type=int, default=None)
    parser.add_argument('--min-dataset-rows', type=int, default=100)
    parser.add_argument('--top-n', type=int, default=3)
    parser.add_argument('--passive-provider-probe', action='store_true')
    parser.add_argument('--active-provider-scan', action='store_true', help='Força evidence-window-scan com active-provider-probe. Por padrão o bundle usa scan governado/cached para reduzir timeout quando o provider está degradado.')
    parser.add_argument('--prepare-best-scope', action='store_true', help='Opcional: roda asset prepare do melhor scope antes do asset candidate (safe, sem ordens).')
    parser.add_argument('--skip-best-scope-candidate', action='store_true')
    parser.add_argument('--probe-provider-in-production-gate', action='store_true', help='Por padrão o production-gate do bundle usa artifacts/provider já capturados para evitar timeout; ative esta flag só se quiser forçar probe remoto dentro do production-gate.')
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    config_path = Path(args.config).resolve() if args.config else _default_config(repo_root)
    python_exe = _find_python(repo_root)
    env = _build_env(repo_root, config_path)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (repo_root / 'runs' / 'debug' / 'bundles')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    bundle_dir = out_dir / f'{timestamp}_portfolio_canary_bundle'
    bundle_dir.mkdir(parents=True, exist_ok=True)

    def runtime_cmd(*cmd_args: str) -> list[str]:
        cmd = [python_exe, '-m', 'natbin.runtime_app', '--repo-root', str(repo_root)]
        if config_path is not None:
            cmd.extend(['--config', str(config_path)])
        cmd.extend(cmd_args)
        return cmd

    probe_cmd = ['provider-probe', '--all-scopes', '--json', '--sample-candles', str(args.sample_candles)]
    if args.passive_provider_probe:
        probe_cmd.append('--passive')
    gate_cmd = ['production-gate', '--all-scopes', '--json', '--sample-candles', str(args.sample_candles)]
    if args.probe_provider_in_production_gate:
        gate_cmd.insert(2, '--probe-provider')
    scan_cmd = ['evidence-window-scan', '--all-scopes', '--json', '--sample-candles', str(args.sample_candles), '--top-n', str(args.top_n)]
    if args.active_provider_scan and not args.passive_provider_probe:
        scan_cmd.append('--active-provider-probe')

    warmup_cmd = [python_exe, _tool_script(repo_root, 'portfolio_canary_warmup.py'), '--repo-root', str(repo_root)]
    if config_path is not None:
        warmup_cmd.extend(['--config', str(config_path)])
    warmup_cmd.extend(['--all-scopes', '--json', '--timeout-sec', str(min(args.timeout_sec, 900))])

    commands: list[tuple[str, list[str], int]] = []
    if not args.no_warmup:
        commands.append(('portfolio_canary_warmup', warmup_cmd, min(args.timeout_sec, 3600)))
    commands.extend([
        ('status', runtime_cmd('status', '--json'), min(args.timeout_sec, 300)),
        ('portfolio_status', runtime_cmd('portfolio', 'status', '--json'), min(args.timeout_sec, 600)),
        ('portfolio_plan', runtime_cmd('portfolio', 'plan', '--json'), min(args.timeout_sec, 600)),
    ])
    if args.include_standalone_provider_probe:
        commands.append(('provider_probe_all_scopes', runtime_cmd(*probe_cmd), min(args.timeout_sec, 900)))
    signal_scan_cmd = [python_exe, str((repo_root / 'scripts' / 'tools' / 'portfolio_canary_signal_proof.py').resolve()), '--repo-root', str(repo_root)]
    if config_path is not None:
        signal_scan_cmd.extend(['--config', str(config_path)])
    signal_scan_cmd.extend(['--all-scopes', '--json'])
    signal_artifact_cmd = [python_exe, str((repo_root / 'scripts' / 'tools' / 'portfolio_signal_artifact_audit.py').resolve()), '--repo-root', str(repo_root)]
    if config_path is not None:
        signal_artifact_cmd.extend(['--config', str(config_path)])
    signal_artifact_cmd.extend(['--all-scopes', '--json'])
    commands.extend([
        ('provider_session_governor', [python_exe, str((repo_root / 'scripts' / 'tools' / 'provider_session_governor.py').resolve()), '--repo-root', str(repo_root), '--config', str(config_path), '--all-scopes', '--active-provider-probe', '--json'], min(args.timeout_sec, 900)),
        ('production_gate_all_scopes', runtime_cmd(*gate_cmd), min(args.timeout_sec, 360)),
        ('evidence_window_scan', runtime_cmd(*scan_cmd), min(args.timeout_sec, 420)),
        ('portfolio_canary_signal_scan', signal_scan_cmd, min(args.timeout_sec, 900)),
        ('signal_artifact_audit', signal_artifact_cmd, min(args.timeout_sec, 240)),
    ])

    started = _now_utc()
    started_monotonic = time.monotonic()
    results: list[dict[str, Any]] = []
    for name, argv, timeout_sec in commands:
        results.append(_run_one(name=name, argv=argv, cwd=repo_root, env=env, timeout_sec=timeout_sec, output_dir=bundle_dir))

    scan_json = _last_json(bundle_dir, 'evidence_window_scan') or {}
    signal_scan_json = _last_json(bundle_dir, 'portfolio_canary_signal_scan') or {}
    best_scope = None
    for candidate_source in (
        signal_scan_json.get('best_actionable_scope'),
        signal_scan_json.get('best_watch_scope'),
        scan_json.get('recommended_scope'),
        scan_json.get('best_scope'),
    ):
        if isinstance(candidate_source, dict) and isinstance(candidate_source.get('scope'), dict):
            best_scope = dict(candidate_source.get('scope') or {})
            break
    if isinstance(best_scope, dict) and not args.skip_best_scope_candidate:
        asset = str(best_scope.get('asset') or '')
        interval_sec = int(best_scope.get('interval_sec') or 0)
        if asset and interval_sec > 0:
            if args.prepare_best_scope:
                results.append(_run_one(name='asset_prepare_best', argv=runtime_cmd('asset', 'prepare', '--asset', asset, '--interval-sec', str(interval_sec), '--json'), cwd=repo_root, env=env, timeout_sec=min(args.timeout_sec, 900), output_dir=bundle_dir))
            results.append(_run_one(name='asset_candidate_best', argv=runtime_cmd('asset', 'candidate', '--asset', asset, '--interval-sec', str(interval_sec), '--json'), cwd=repo_root, env=env, timeout_sec=min(args.timeout_sec, 900), output_dir=bundle_dir))

    finished = _now_utc()
    duration_sec = round(time.monotonic() - started_monotonic, 3)
    bundle_summary = _bundle_summary(bundle_dir, results)
    _write_text(bundle_dir / 'bundle_summary.json', json.dumps(bundle_summary, ensure_ascii=False, indent=2) + '\n')
    manifest = {
        'kind': 'portfolio_canary_bundle',
        'repo_root': str(repo_root),
        'config_path': str(config_path) if config_path else None,
        'python_executable': python_exe,
        'started_at_utc': started,
        'finished_at_utc': finished,
        'duration_sec': duration_sec,
        'bundle_dir': str(bundle_dir),
        'commands': results,
        'bundle_summary': bundle_summary,
        'dangerous': False,
        'note': 'Bundle read-only por padrão. warmup usa asset prepare (sem ordens) e asset candidate executa observe_once com execution_disabled.',
    }
    _write_text(bundle_dir / 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    zip_path = out_dir / f'{timestamp}_portfolio_canary_bundle.zip'
    _zip_dir(bundle_dir, zip_path)

    print(json.dumps({
        'ok': True,
        'kind': 'portfolio_canary_bundle_result',
        'dangerous': False,
        'zip_path': str(zip_path),
        'bundle_dir': str(bundle_dir),
        'commands': [{'name': r['name'], 'returncode': r['returncode'], 'timed_out': r['timed_out'], 'parsed_summary': r.get('parsed_summary')} for r in results],
        'bundle_summary': bundle_summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
