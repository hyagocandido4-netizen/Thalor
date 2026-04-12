from __future__ import annotations

import subprocess
from pathlib import Path

from _launcher_common import (
    build_env,
    default_repo_root,
    parse_passthrough_args,
    print_command,
    python_command,
    resolve_repo_python,
    run_module,
)

# Some newer tooling was added as standalone scripts first and only later
# bridged into runtime_app/control-plane. Route those names directly here so
# the .cmd entrypoint stays stable even if control/app registration lags.
_DIRECT_SCRIPT_MAP = {
    'provider-stability-report': 'provider_stability_report.py',
    'provider_stability_report': 'provider_stability_report.py',
    'portfolio-canary-signal-scan': 'portfolio_canary_signal_scan.py',
    'portfolio_canary_signal_scan': 'portfolio_canary_signal_scan.py',
    'provider-session-governor': 'provider_session_governor.py',
    'provider_session_governor': 'provider_session_governor.py',
}


def _run_script(repo_root: Path, script_name: str, script_args: list[str], *, explicit_python: str | None = None, verbose: bool = False) -> int:
    python_exe = resolve_repo_python(repo_root, explicit_python)
    script_path = (repo_root / 'scripts' / 'tools' / script_name).resolve()
    env = build_env(repo_root)
    cmd = python_command(python_exe, str(script_path), *script_args)
    if verbose:
        print_command(Path(script_name).stem, cmd)
    completed = subprocess.run(cmd, env=env, cwd=str(repo_root), check=False)
    return int(completed.returncode)


def main() -> int:
    ns, remaining = parse_passthrough_args('Wrapper sem PowerShell para natbin.runtime_app.')
    repo_root = Path(ns.repo_root).resolve() if ns.repo_root else default_repo_root(__file__)

    forwarded_common: list[str] = ['--repo-root', str(repo_root)]
    if ns.config:
        forwarded_common.extend(['--config', ns.config])

    if remaining:
        routed = _DIRECT_SCRIPT_MAP.get(str(remaining[0]))
        if routed:
            return _run_script(
                repo_root,
                routed,
                [*forwarded_common, *remaining[1:]],
                explicit_python=ns.python,
                verbose=bool(ns.verbose_wrapper),
            )

    module_args: list[str] = ['--repo-root', str(repo_root)]
    if ns.config:
        module_args.extend(['--config', ns.config])
    module_args.extend(remaining)

    if ns.verbose_wrapper:
        python_exe = resolve_repo_python(repo_root, ns.python)
        print_command('runtime_app', python_command(python_exe, '-m', 'natbin.runtime_app', *module_args))
    return run_module(repo_root, 'natbin.runtime_app', module_args, explicit_python=ns.python)


if __name__ == '__main__':
    raise SystemExit(main())
