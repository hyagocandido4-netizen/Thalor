from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


def default_repo_root(script_path: str | Path) -> Path:
    return Path(script_path).resolve().parents[2]


def discover_default_config(repo_root: Path) -> str | None:
    candidates = [
        repo_root / 'config' / 'live_controlled_practice.yaml',
        repo_root / 'config' / 'live_controlled_real.yaml',
        repo_root / 'config' / 'base.yaml',
        repo_root / 'config.yaml',
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                return str(candidate.resolve().relative_to(repo_root.resolve()))
            except Exception:
                return str(candidate.resolve())

    env_cfg = os.environ.get('THALOR_CONFIG_PATH') or os.environ.get('THALOR_CONFIG')
    env_text = str(env_cfg or '').strip()
    if not env_text:
        return None

    env_path = Path(env_text)
    if not env_path.is_absolute():
        env_path = (repo_root / env_path).resolve()
    if env_path.exists():
        try:
            return str(env_path.relative_to(repo_root.resolve()))
        except Exception:
            return str(env_path)
    return env_text


def resolve_repo_python(repo_root: Path, explicit_python: str | None = None) -> str:
    if explicit_python:
        return explicit_python
    if os.name == 'nt':
        candidates = [
            repo_root / '.venv' / 'Scripts' / 'python.exe',
            repo_root / '.venv' / 'bin' / 'python',
        ]
    else:
        candidates = [
            repo_root / '.venv' / 'bin' / 'python',
            repo_root / '.venv' / 'Scripts' / 'python.exe',
        ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        if os.name != 'nt' and candidate.suffix.lower() == '.exe':
            continue
        return str(candidate)
    py_cmd = shutil.which('py')
    if py_cmd:
        return py_cmd
    python_cmd = shutil.which('python') or shutil.which('python3')
    if python_cmd:
        return python_cmd
    return sys.executable


def build_env(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    src_path = str(repo_root / 'src')
    existing = env.get('PYTHONPATH', '')
    if existing:
        sep = os.pathsep
        entries: list[str] = [src_path]
        entries.extend(item for item in existing.split(sep) if item)
        seen: set[str] = set()
        normalized: list[str] = []
        for item in entries:
            if item not in seen:
                normalized.append(item)
                seen.add(item)
        env['PYTHONPATH'] = sep.join(normalized)
    else:
        env['PYTHONPATH'] = src_path
    return env


def python_command(python_exe: str, *extra: str) -> list[str]:
    exe_name = Path(python_exe).name.lower()
    if exe_name in {'py', 'py.exe'}:
        return [python_exe, '-3.12', *extra]
    return [python_exe, *extra]


def run_module(repo_root: Path, module: str, module_args: Iterable[str], explicit_python: str | None = None) -> int:
    python_exe = resolve_repo_python(repo_root, explicit_python)
    env = build_env(repo_root)
    cmd = python_command(python_exe, '-m', module, *list(module_args))
    completed = subprocess.run(cmd, env=env, cwd=str(repo_root), check=False)
    return int(completed.returncode)


def parse_passthrough_args(description: str) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description=description, add_help=False)
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--config', default=None)
    parser.add_argument('--python', default=None)
    parser.add_argument('--wrapper-help', action='store_true')
    parser.add_argument('--verbose-wrapper', action='store_true')
    ns, remaining = parser.parse_known_args()
    if ns.wrapper_help:
        help_parser = argparse.ArgumentParser(description=description)
        help_parser.add_argument('--repo-root', default=None)
        help_parser.add_argument('--config', default=None)
        help_parser.add_argument('--python', default=None)
        help_parser.add_argument('--verbose-wrapper', action='store_true')
        help_parser.print_help()
        raise SystemExit(0)
    return ns, remaining


def print_command(prefix: str, command: list[str]) -> None:
    rendered = ' '.join(_quote_piece(piece) for piece in command)
    print(f'[{prefix}] {rendered}')


def _quote_piece(value: str) -> str:
    if not value or any(ch.isspace() for ch in value) or '"' in value:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value
