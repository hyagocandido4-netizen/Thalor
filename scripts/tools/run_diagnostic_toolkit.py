from __future__ import annotations

import argparse
from pathlib import Path

from _launcher_common import default_repo_root, print_command, python_command, resolve_repo_python, run_module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Executa o toolkit diagnóstico sem depender de PowerShell assinado.')
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--config', default='config/live_controlled_practice.yaml')
    parser.add_argument('--python', default=None)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--probe-broker', action='store_true')
    parser.add_argument('--probe-provider', action='store_true')
    parser.add_argument('--stop-on-failure', action='store_true')
    parser.add_argument('--include-support-bundle', action='store_true')
    parser.add_argument('--all-scopes', action='store_true')
    parser.add_argument('--verbose-wrapper', action='store_true')
    return parser.parse_args()


def build_steps(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    shared = ['--json']
    if args.dry_run:
        shared.append('--dry-run')
    if args.all_scopes:
        shared.append('--all-scopes')

    diag = ['diag-suite', *shared, '--include-practice', '--include-provider-probe']
    if args.probe_provider and not args.dry_run:
        diag.append('--active-provider-probe')
    if args.include_support_bundle:
        diag.append('--include-support-bundle')
    if args.probe_broker and not args.dry_run:
        diag.append('--probe-broker')

    transport = ['transport-smoke', *shared, '--operation', 'windows_launcher']
    module = ['module-smoke', '--json']
    if args.dry_run:
        module.append('--dry-run')
    redaction = ['redaction-audit', '--json']
    if args.dry_run:
        redaction.append('--dry-run')
    preflight = ['practice-preflight', '--json']
    if args.dry_run:
        preflight.append('--dry-run')
    if args.probe_broker and not args.dry_run:
        preflight.append('--probe-broker')
    if args.probe_provider and not args.dry_run:
        preflight.append('--probe-provider')

    return [
        ('diag-suite', diag),
        ('transport-smoke', transport),
        ('module-smoke', module),
        ('redaction-audit', redaction),
        ('practice-preflight', preflight),
    ]


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve() if args.repo_root else default_repo_root(__file__)
    python_exe = resolve_repo_python(repo_root, args.python)

    summary: list[tuple[str, int]] = []
    for name, step_args in build_steps(args):
        module_args = ['--repo-root', str(repo_root)]
        if args.config:
            module_args.extend(['--config', args.config])
        module_args.extend(step_args)
        if args.verbose_wrapper:
            print_command(name, python_command(python_exe, '-m', 'natbin.runtime_app', *module_args))
        print(f'\n=== {name} ===')
        code = run_module(repo_root, 'natbin.runtime_app', module_args, explicit_python=python_exe)
        summary.append((name, code))
        label = 'OK' if code == 0 else 'FAIL'
        print(f'[{label}] {name} (exit={code})')
        if args.stop_on_failure and code != 0:
            break

    print('\nResumo do toolkit:')
    for name, code in summary:
        print(f' - {name}: {"OK" if code == 0 else "FAIL"}')
    return 0 if all(code == 0 for _, code in summary) else 2


if __name__ == '__main__':
    raise SystemExit(main())
