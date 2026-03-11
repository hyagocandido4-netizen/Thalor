from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


@dataclass
class CommandSpec:
    name: str
    cmd: list[str]
    optional: bool = False


@dataclass
class CommandResult:
    name: str
    returncode: int
    duration_sec: float
    started_at_utc: str
    finished_at_utc: str
    cmd: list[str]
    optional: bool


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


QUICK_SPECS = [
    CommandSpec("selfcheck_repo", ["scripts/tools/selfcheck_repo.py"]),
    CommandSpec("pytest", ["-m", "pytest", "-q"]),
    CommandSpec("smoke_runtime_app", ["scripts/ci/smoke_runtime_app.py"]),
    CommandSpec("smoke_execution_layer", ["scripts/ci/smoke_execution_layer.py"]),
]

FULL_ONLY_SPECS = [
    CommandSpec("release_hygiene_smoke", ["scripts/tools/release_hygiene_smoke.py"]),
    CommandSpec("broker_adapter_contract_smoke", ["scripts/tools/broker_adapter_contract_smoke.py"]),
    CommandSpec("runtime_execution_integration_smoke", ["scripts/tools/runtime_execution_integration_smoke.py"]),
    CommandSpec("runtime_hardening_smoke", ["scripts/tools/runtime_hardening_smoke.py"]),
    CommandSpec("portfolio_risk_smoke", ["scripts/tools/portfolio_risk_smoke.py"]),
    CommandSpec("intelligence_pack_smoke", ["scripts/tools/intelligence_pack_smoke.py"]),
    CommandSpec("security_hardening_smoke", ["scripts/tools/security_hardening_smoke.py"]),
    CommandSpec("productization_smoke", ["scripts/tools/productization_smoke.py"]),
    CommandSpec("incident_ops_smoke", ["scripts/tools/incident_ops_smoke.py"]),
]

SOAK_SPECS = [
    CommandSpec("runtime_soak", ["scripts/tools/runtime_soak.py"], optional=True),
]


def build_specs(preset: str, include_soak: bool) -> list[CommandSpec]:
    specs = list(QUICK_SPECS)
    if preset == "full":
        specs.extend(FULL_ONLY_SPECS)
    if include_soak:
        specs.extend(SOAK_SPECS)
    return specs


def ensure_path(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    src_path = repo_root / "src"
    existing = env.get("PYTHONPATH", "")
    parts = [str(src_path)]
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def run_command(python_exe: str, spec: CommandSpec, repo_root: Path, env: dict[str, str]) -> CommandResult:
    started = utc_now_iso()
    started_perf = time.perf_counter()
    full_cmd = [python_exe, *spec.cmd]
    print(f"\n=== RUN {spec.name} ===")
    print(" ".join(full_cmd))
    proc = subprocess.run(full_cmd, cwd=repo_root, env=env, check=False)
    duration = time.perf_counter() - started_perf
    finished = utc_now_iso()
    return CommandResult(
        name=spec.name,
        returncode=proc.returncode,
        duration_sec=round(duration, 3),
        started_at_utc=started,
        finished_at_utc=finished,
        cmd=full_cmd,
        optional=spec.optional,
    )


def write_report(repo_root: Path, preset: str, include_soak: bool, python_exe: str, results: Sequence[CommandResult]) -> Path:
    out_dir = repo_root / "runs" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"local_test_suite_{timestamp}.json"
    payload = {
        "generated_at_utc": utc_now_iso(),
        "preset": preset,
        "include_soak": include_soak,
        "python_exe": python_exe,
        "all_passed": all((r.returncode == 0 or r.optional) for r in results),
        "results": [asdict(r) for r in results],
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return report_path


def summarize(results: Iterable[CommandResult]) -> tuple[int, int]:
    passed = 0
    failed = 0
    for result in results:
        if result.returncode == 0:
            passed += 1
        elif result.optional:
            # optional failures don't fail the whole suite, but still count visibly.
            failed += 1
        else:
            failed += 1
    return passed, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the recommended local Thalor test suite.")
    parser.add_argument("--repo-root", default=".", help="Repository root path.")
    parser.add_argument("--python", dest="python_exe", default=sys.executable, help="Python executable to use.")
    parser.add_argument("--preset", choices=["quick", "full"], default="full", help="Suite preset.")
    parser.add_argument("--include-soak", action="store_true", help="Also run the longer soak script.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not (repo_root / "pyproject.toml").exists():
        print(f"[ERROR] repo root does not look valid: {repo_root}")
        return 2

    specs = build_specs(args.preset, args.include_soak)
    env = ensure_path(repo_root)

    print("Thalor local test suite")
    print(f"repo_root={repo_root}")
    print(f"python={args.python_exe}")
    print(f"preset={args.preset}")
    print(f"include_soak={args.include_soak}")

    results: list[CommandResult] = []
    for spec in specs:
        result = run_command(args.python_exe, spec, repo_root, env)
        results.append(result)
        status = "OK" if result.returncode == 0 else ("WARN" if spec.optional else "FAIL")
        print(f"[{status}] {result.name} ({result.duration_sec:.3f}s)")
        if result.returncode != 0 and not spec.optional:
            print("Stopping early because a required command failed.")
            break

    report_path = write_report(repo_root, args.preset, args.include_soak, args.python_exe, results)
    passed, failed = summarize(results)
    print("\n=== SUMMARY ===")
    print(f"passed={passed}")
    print(f"failed={failed}")
    print(f"report={report_path}")

    hard_failures = [r for r in results if r.returncode != 0 and not r.optional]
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
