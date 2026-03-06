from __future__ import annotations

"""Runtime cycle planning/execution helpers.

Package M makes the Python plan authoritative. PowerShell wrappers remain
bootstrap-only and are no longer part of the canonical auto-cycle plan.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time
from typing import Iterable


OK = "ok"
TIMEOUT = "timeout"
NONZERO_EXIT = "nonzero_exit"
INTERRUPTED = "interrupted"
EXCEPTION = "exception"


@dataclass(frozen=True)
class StepCommand:
    name: str
    argv: list[str]
    timeout_sec: int
    cwd: str


@dataclass(frozen=True)
class StepOutcome:
    name: str
    kind: str
    returncode: int | None
    duration_sec: float
    timed_out: bool
    interrupted: bool
    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True)
class CycleReport:
    mode: str
    repo_root: str
    steps: list[dict]
    outcomes: list[dict] | None = None
    ok: bool | None = None


def _as_int(v: str | None, default: int) -> int:
    try:
        if v is None or str(v).strip() == "":
            return int(default)
        return int(float(str(v).strip().replace(",", ".")))
    except Exception:
        return int(default)


def repo_python_executable(repo_root: Path) -> str:
    if os.name == 'nt':
        candidates = [repo_root / '.venv' / 'Scripts' / 'python.exe', repo_root / '.venv' / 'bin' / 'python']
        for cand in candidates:
            if cand.exists():
                return str(cand)
        return sys.executable

    cand = repo_root / '.venv' / 'bin' / 'python'
    if cand.exists() and os.access(cand, os.X_OK):
        return str(cand)
    return sys.executable


def classify_outcome_kind(*, returncode: int | None, timed_out: bool = False, interrupted: bool = False) -> str:
    if interrupted:
        return INTERRUPTED
    if timed_out:
        return TIMEOUT
    if returncode in (None, 0):
        return OK
    return NONZERO_EXIT


def _python_module_step(repo_root: Path, name: str, module: str, timeout_env: str, timeout_default: int) -> StepCommand:
    py = repo_python_executable(repo_root)
    timeout_sec = _as_int(os.getenv(timeout_env), timeout_default)
    return StepCommand(name=name, argv=[py, '-m', module], timeout_sec=timeout_sec, cwd=str(repo_root))


def _python_cli_step(repo_root: Path, name: str, module: str, extra_args: Iterable[str], timeout_env: str, timeout_default: int) -> StepCommand:
    py = repo_python_executable(repo_root)
    timeout_sec = _as_int(os.getenv(timeout_env), timeout_default)
    return StepCommand(name=name, argv=[py, '-m', module, *extra_args], timeout_sec=timeout_sec, cwd=str(repo_root))


def build_auto_cycle_plan(repo_root: Path | str, *, topk: int = 3, lookback_candles: int = 2000) -> list[StepCommand]:
    repo_root = Path(repo_root).resolve()
    plan: list[StepCommand] = [
        _python_module_step(repo_root, "collect_recent", "natbin.collect_recent", "COLLECT_RECENT_TIMEOUT_SEC", 120),
        _python_module_step(repo_root, "make_dataset", "natbin.make_dataset", "MAKE_DATASET_TIMEOUT_SEC", 120),
        _python_module_step(repo_root, "refresh_daily_summary", "natbin.refresh_daily_summary", "REFRESH_DAILY_SUMMARY_TIMEOUT_SEC", 90),
        _python_module_step(repo_root, "refresh_market_context", "natbin.refresh_market_context", "REFRESH_MARKET_CONTEXT_TIMEOUT_SEC", 60),
        _python_module_step(repo_root, "auto_volume", "natbin.auto_volume", "AUTO_VOLUME_TIMEOUT_SEC", 60),
        _python_module_step(repo_root, "auto_isoblend", "natbin.auto_isoblend", "AUTO_ISOBLEND_TIMEOUT_SEC", 60),
        _python_module_step(repo_root, "auto_hourthr", "natbin.auto_hourthr", "AUTO_HOURTHR_TIMEOUT_SEC", 60),
    ]

    extra_args = ['--repo-root', str(repo_root), '--lookback-candles', str(int(lookback_candles))]
    if int(topk) > 0:
        extra_args.extend(['--topk', str(int(topk))])
    plan.append(
        _python_cli_step(
            repo_root,
            'observe_loop_once',
            'natbin.runtime.observe_once',
            extra_args,
            'OBSERVE_LOOP_TIMEOUT_SEC',
            180,
        )
    )
    return plan


def _tail_text(s: str | None, limit: int = 1200) -> str:
    if not s:
        return ""
    s = str(s)
    if len(s) <= limit:
        return s
    return s[-limit:]


def run_step(step: StepCommand) -> StepOutcome:
    t0 = time.perf_counter()
    try:
        cp = subprocess.run(
            step.argv,
            cwd=step.cwd,
            capture_output=True,
            text=True,
            timeout=step.timeout_sec,
        )
        kind = classify_outcome_kind(returncode=cp.returncode)
        return StepOutcome(
            name=step.name,
            kind=kind,
            returncode=cp.returncode,
            duration_sec=round(time.perf_counter() - t0, 3),
            timed_out=False,
            interrupted=False,
            stdout_tail=_tail_text(cp.stdout),
            stderr_tail=_tail_text(cp.stderr),
        )
    except subprocess.TimeoutExpired as e:
        return StepOutcome(
            name=step.name,
            kind=TIMEOUT,
            returncode=None,
            duration_sec=round(time.perf_counter() - t0, 3),
            timed_out=True,
            interrupted=False,
            stdout_tail=_tail_text(e.stdout),
            stderr_tail=_tail_text(e.stderr),
        )
    except KeyboardInterrupt:
        return StepOutcome(
            name=step.name,
            kind=INTERRUPTED,
            returncode=None,
            duration_sec=round(time.perf_counter() - t0, 3),
            timed_out=False,
            interrupted=True,
            stdout_tail="",
            stderr_tail="",
        )
    except Exception as e:  # pragma: no cover - defensive
        return StepOutcome(
            name=step.name,
            kind=EXCEPTION,
            returncode=None,
            duration_sec=round(time.perf_counter() - t0, 3),
            timed_out=False,
            interrupted=False,
            stdout_tail="",
            stderr_tail=_tail_text(str(e)),
        )


def run_plan(steps: Iterable[StepCommand], *, stop_on_failure: bool = True) -> list[StepOutcome]:
    outcomes: list[StepOutcome] = []
    for step in steps:
        out = run_step(step)
        outcomes.append(out)
        if stop_on_failure and out.kind != OK:
            break
    return outcomes


def report_from_plan(repo_root: Path | str, plan: Iterable[StepCommand], outcomes: list[StepOutcome] | None = None) -> CycleReport:
    steps = [asdict(s) for s in plan]
    out_dicts = [asdict(o) for o in outcomes] if outcomes is not None else None
    ok = None if outcomes is None else all(o.kind == OK for o in outcomes)
    return CycleReport(mode="auto_cycle", repo_root=str(Path(repo_root).resolve()), steps=steps, outcomes=out_dicts, ok=ok)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Plan or run one Thalor auto-loop cycle from Python.")
    p.add_argument("--repo-root", default=".", help="Repo root (default: current directory)")
    p.add_argument("--topk", type=int, default=3, help="TopK override for observe loop")
    p.add_argument("--lookback-candles", type=int, default=2000)
    p.add_argument("--run", action="store_true", help="Execute the planned steps sequentially")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a human plan")
    p.add_argument("--no-stop-on-failure", action="store_true", help="Continue after a step failure")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    repo_root = Path(ns.repo_root).resolve()
    plan = build_auto_cycle_plan(repo_root, topk=ns.topk, lookback_candles=ns.lookback_candles)

    if not ns.run:
        rep = report_from_plan(repo_root, plan)
        if ns.json:
            print(json.dumps(asdict(rep), ensure_ascii=False, indent=2))
        else:
            print("runtime cycle plan:")
            for i, step in enumerate(plan, start=1):
                print(f"{i:02d}. {step.name} timeout={step.timeout_sec}s")
                print("    " + " ".join(step.argv))
        return 0

    outcomes = run_plan(plan, stop_on_failure=not ns.no_stop_on_failure)
    rep = report_from_plan(repo_root, plan, outcomes)
    if ns.json:
        print(json.dumps(asdict(rep), ensure_ascii=False, indent=2))
    else:
        print(f"runtime cycle ok={rep.ok}")
        for out in outcomes:
            print(f"- {out.name}: {out.kind} ({out.duration_sec:.3f}s)")
            if out.stderr_tail:
                print(f"  stderr: {out.stderr_tail}")
    return 0 if rep.ok else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
