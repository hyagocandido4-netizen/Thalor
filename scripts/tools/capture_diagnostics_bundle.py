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


def _default_config(repo_root: Path) -> Path | None:
    candidates = [
        repo_root / "config" / "live_controlled_practice.yaml",
        repo_root / "config" / "live_controlled_real.yaml",
        repo_root / "config" / "base.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _find_python(repo_root: Path) -> str:
    candidates = [
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("python") or sys.executable


def _build_env(repo_root: Path, config_path: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    src = repo_root / "src"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(src) if not existing_pythonpath else f"{src}{os.pathsep}{existing_pythonpath}"
    env["THALOR_REPO_ROOT"] = str(repo_root)
    if config_path is not None:
        env["THALOR_CONFIG"] = str(config_path)
        env["THALOR_CONFIG_PATH"] = str(config_path)
    return env


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _zip_dir(source_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(source_dir)))


def _run_one(*, name: str, argv: list[str], cwd: Path, env: dict[str, str], timeout_sec: int, output_dir: Path) -> dict[str, Any]:
    started = _now_utc()
    started_monotonic = time.monotonic()
    timed_out = False
    stdout_text = ""
    stderr_text = ""
    returncode = None
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_sec)),
        )
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
    _write_text(step_dir / "stdout.txt", stdout_text)
    _write_text(step_dir / "stderr.txt", stderr_text)
    _write_text(step_dir / "command.txt", " ".join(argv) + "\n")
    _write_text(step_dir / "exit_code.txt", f"{returncode}\n")
    parsed_summary = write_json_summary(base_dir=step_dir, stdout_text=stdout_text)

    result = {
        "name": name,
        "command": argv,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "duration_sec": duration_sec,
        "timeout_sec": timeout_sec,
        "timed_out": timed_out,
        "returncode": returncode,
        "stdout_path": str(step_dir / "stdout.txt"),
        "stderr_path": str(step_dir / "stderr.txt"),
        "exit_code_path": str(step_dir / "exit_code.txt"),
        "parsed_summary": parsed_summary,
    }
    _write_text(step_dir / "manifest.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def _latest_json(output_dir: Path, name: str) -> dict[str, Any] | None:
    path = output_dir / name / "last_json.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _summary_for(results: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((r.get("parsed_summary") for r in results if r.get("name") == name), None)


def _practice_verdict(*, bundle_dir: Path, include_post_soak_diag: bool) -> dict[str, Any]:
    practice_preflight = _latest_json(bundle_dir, "practice_preflight") or {}
    diag_post = _latest_json(bundle_dir, "diag_suite_post") or {}
    doctor_post = _latest_json(bundle_dir, "doctor_post") or {}
    diag_pre = _latest_json(bundle_dir, "diag_suite") or {}
    doctor_pre = _latest_json(bundle_dir, "doctor") or {}

    final_ready = bool(practice_preflight.get("ready_for_long_practice"))
    final_severity = str(practice_preflight.get("severity") or "unknown") if practice_preflight else "unknown"
    final_ok = bool(practice_preflight.get("ok")) if practice_preflight else False

    verdict: dict[str, Any] = {
        "final_gate": "practice_preflight",
        "ready_for_long_practice": final_ready,
        "ok": final_ok,
        "severity": final_severity,
        "doctor_ready_for_practice": bool((doctor_post or doctor_pre).get("ready_for_practice")),
        "doctor_ready_for_real": bool((doctor_post or doctor_pre).get("ready_for_real")),
        "diag_ready_for_practice": bool((diag_post or diag_pre).get("ready_for_practice")),
        "diag_severity": str((diag_post or diag_pre).get("severity") or "unknown") if (diag_post or diag_pre) else "unknown",
    }
    if include_post_soak_diag and diag_post:
        verdict["post_soak_diag_suite"] = {
            "ok": bool(diag_post.get("ok")),
            "severity": str(diag_post.get("severity") or "unknown"),
            "ready_for_practice": bool(diag_post.get("ready_for_practice")),
            "blockers": list(diag_post.get("blockers") or []),
            "warnings": list(diag_post.get("warnings") or []),
        }
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="Roda uma bateria diagnóstica e gera um ZIP com todos os logs.")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--out-dir", default=None, help="Default: runs/debug/bundles")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Timeout padrão por comando.")
    parser.add_argument("--include-heal-soak", action="store_true", help="Inclui practice-preflight com --heal-soak --soak-cycles N.")
    parser.add_argument("--soak-cycles", type=int, default=6)
    parser.add_argument("--include-post-soak-diag", action="store_true", help="Quando usado com --include-heal-soak, roda um diag-suite final depois do preflight para capturar o estado pós-soak.")
    parser.add_argument("--include-post-soak-doctor", action="store_true", help="Quando usado com --include-heal-soak, roda um doctor final depois do preflight.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    config_path = Path(args.config).resolve() if args.config else _default_config(repo_root)
    python_exe = _find_python(repo_root)
    env = _build_env(repo_root=repo_root, config_path=config_path)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else (repo_root / "runs" / "debug" / "bundles")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = out_dir / f"{timestamp}_diagnostic_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    def runtime_cmd(*cmd_args: str) -> list[str]:
        cmd = [python_exe, "-m", "natbin.runtime_app", "--repo-root", str(repo_root)]
        if config_path is not None:
            cmd.extend(["--config", str(config_path)])
        cmd.extend(cmd_args)
        return cmd

    commands: list[tuple[str, list[str], int]] = [
        ("status", runtime_cmd("status", "--json"), min(args.timeout_sec, 300)),
        ("breaker_status", runtime_cmd("ops", "breaker", "status", "--json"), min(args.timeout_sec, 300)),
        ("doctor", runtime_cmd("doctor", "--heal-breaker", "--heal-market-context", "--heal-control-freshness", "--json"), min(args.timeout_sec, 900)),
        ("diag_suite", runtime_cmd("diag-suite", "--include-practice", "--include-provider-probe", "--active-provider-probe", "--json"), args.timeout_sec),
    ]

    if args.include_heal_soak:
        commands.append(("practice_preflight", runtime_cmd("practice-preflight", "--heal-soak", "--soak-cycles", str(args.soak_cycles), "--json"), args.timeout_sec))
        if args.include_post_soak_diag:
            commands.append(("diag_suite_post", runtime_cmd("diag-suite", "--include-practice", "--include-provider-probe", "--active-provider-probe", "--json"), args.timeout_sec))
        if args.include_post_soak_doctor:
            commands.append(("doctor_post", runtime_cmd("doctor", "--heal-breaker", "--heal-market-context", "--heal-control-freshness", "--json"), min(args.timeout_sec, 900)))
    else:
        commands.append(("practice_preflight", runtime_cmd("practice-preflight", "--json"), args.timeout_sec))

    started = _now_utc()
    started_monotonic = time.monotonic()
    results = []
    for name, argv, timeout_sec in commands:
        results.append(_run_one(name=name, argv=argv, cwd=repo_root, env=env, timeout_sec=timeout_sec, output_dir=bundle_dir))

    finished = _now_utc()
    duration_sec = round(time.monotonic() - started_monotonic, 3)
    bundle_summary = {
        "status": _summary_for(results, "status"),
        "breaker_status": _summary_for(results, "breaker_status"),
        "doctor": _summary_for(results, "doctor"),
        "diag_suite": _summary_for(results, "diag_suite"),
        "practice_preflight": _summary_for(results, "practice_preflight"),
        "diag_suite_post": _summary_for(results, "diag_suite_post"),
        "doctor_post": _summary_for(results, "doctor_post"),
        "practice_verdict": _practice_verdict(bundle_dir=bundle_dir, include_post_soak_diag=bool(args.include_post_soak_diag)),
    }
    _write_text(bundle_dir / "bundle_summary.json", json.dumps(bundle_summary, ensure_ascii=False, indent=2) + "\n")
    summary = {
        "kind": "diagnostic_bundle",
        "repo_root": str(repo_root),
        "config_path": str(config_path) if config_path else None,
        "python_executable": python_exe,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "duration_sec": duration_sec,
        "bundle_dir": str(bundle_dir),
        "commands": results,
        "bundle_summary": bundle_summary,
    }
    _write_text(bundle_dir / "manifest.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    zip_path = out_dir / f"{timestamp}_diagnostic_bundle.zip"
    _zip_dir(bundle_dir, zip_path)

    print(json.dumps({
        "ok": True,
        "kind": "diagnostic_bundle_result",
        "zip_path": str(zip_path),
        "bundle_dir": str(bundle_dir),
        "commands": [{"name": r["name"], "returncode": r["returncode"], "timed_out": r["timed_out"], "parsed_summary": r.get("parsed_summary")} for r in results],
        "bundle_summary": bundle_summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
