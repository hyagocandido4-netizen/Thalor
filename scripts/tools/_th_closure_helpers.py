
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from natbin.ops.canary_closure_core import extract_top_level_json


@dataclass
class CommandResult:
    name: str
    argv: list[str]
    returncode: int
    timed_out: bool
    duration_sec: float
    stdout: str
    stderr: str
    payload: dict[str, Any] | None

    def to_summary(self) -> dict[str, Any]:
        payload = self.payload or {}
        return {
            "name": self.name,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "duration_sec": round(self.duration_sec, 3),
            "kind": payload.get("kind"),
            "ok": payload.get("ok"),
            "severity": payload.get("severity"),
        }


def _find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "src" / "natbin").exists():
            return candidate
    return current


def repo_python(repo_root: Path) -> Path:
    if platform.system().lower().startswith("win"):
        candidate = repo_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = repo_root / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else Path(sys.executable)


def build_env(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    src = str((repo_root / "src").resolve())
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not current else src + os.pathsep + current
    return env


def run_python_script(
    repo_root: Path,
    script_rel: str,
    args: Sequence[str],
    *,
    name: str,
    timeout_sec: int = 900,
) -> CommandResult:
    import time

    py = repo_python(repo_root)
    script_path = repo_root / script_rel
    argv = [str(py), str(script_path), *args]
    env = build_env(repo_root)
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        timed_out = False
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
    duration_sec = time.perf_counter() - start
    payload = extract_top_level_json(stdout)
    return CommandResult(
        name=name,
        argv=argv,
        returncode=returncode,
        timed_out=timed_out,
        duration_sec=duration_sec,
        stdout=stdout,
        stderr=stderr,
        payload=payload,
    )


def scope_tag_to_parts(scope_tag: str) -> tuple[str, int]:
    if "_" not in scope_tag:
        raise ValueError(f"invalid scope_tag={scope_tag!r}")
    asset, suffix = scope_tag.rsplit("_", 1)
    if not suffix.endswith("s"):
        raise ValueError(f"invalid scope suffix={suffix!r}")
    interval_sec = int(suffix[:-1])
    return asset, interval_sec


def dump_json(data: dict[str, Any]) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
