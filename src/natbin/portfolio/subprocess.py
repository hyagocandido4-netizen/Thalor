from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..runtime.cycle import repo_python_executable


@dataclass(frozen=True)
class SubprocessOutcome:
    name: str
    argv: list[str]
    cwd: str
    returncode: int
    duration_sec: float
    stdout_tail: str
    stderr_tail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tail(s: str | None, limit: int = 1600) -> str:
    if not s:
        return ''
    s = str(s)
    return s if len(s) <= limit else s[-limit:]


def run_python_module(
    repo_root: str | Path,
    *,
    name: str,
    module: str,
    args: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    timeout_sec: int = 180,
) -> SubprocessOutcome:
    root = Path(repo_root).resolve()
    py = repo_python_executable(root)
    argv = [py, '-m', str(module), *list(args)]
    merged_env = dict(**{k: v for k, v in (env or {}).items()})

    t0 = time.perf_counter()
    cp = subprocess.run(
        argv,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=int(timeout_sec),
        env={**dict(os.environ), **merged_env} if merged_env else None,
    )
    dur = round(time.perf_counter() - t0, 3)
    return SubprocessOutcome(
        name=str(name),
        argv=[str(x) for x in argv],
        cwd=str(root),
        returncode=int(cp.returncode or 0),
        duration_sec=float(dur),
        stdout_tail=_tail(cp.stdout),
        stderr_tail=_tail(cp.stderr),
    )
