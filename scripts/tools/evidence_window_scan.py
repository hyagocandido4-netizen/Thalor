from __future__ import annotations

from pathlib import Path

from _launcher_common import default_repo_root, discover_default_config, parse_passthrough_args, print_command, python_command, resolve_repo_python, run_module


def main() -> int:
    ns, remaining = parse_passthrough_args('Wrapper sem PowerShell para evidence-window-scan via natbin.runtime_app.')
    repo_root = Path(ns.repo_root).resolve() if ns.repo_root else default_repo_root(__file__)
    module_args: list[str] = ['--repo-root', str(repo_root)]
    config_value = ns.config or discover_default_config(repo_root)
    if config_value:
        module_args.extend(['--config', config_value])
    module_args.extend(['evidence-window-scan'])
    module_args.extend(remaining)
    if ns.verbose_wrapper:
        python_exe = resolve_repo_python(repo_root, ns.python)
        print_command('evidence_window_scan', python_command(python_exe, '-m', 'natbin.runtime_app', *module_args))
    return run_module(repo_root, 'natbin.runtime_app', module_args, explicit_python=ns.python)


if __name__ == '__main__':
    raise SystemExit(main())
