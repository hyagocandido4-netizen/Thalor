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
    for rel in ("config/live_controlled_practice.yaml", "config/live_controlled_real.yaml", "config/base.yaml"):
        path = repo_root / rel
        if path.exists():
            return path
    return None


def _find_python(repo_root: Path) -> str:
    for candidate in (repo_root / ".venv" / "Scripts" / "python.exe", repo_root / ".venv" / "bin" / "python"):
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
        "parsed_summary": parsed_summary,
    }
    _write_text(step_dir / "manifest.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def _last_json(bundle_dir: Path, name: str) -> dict[str, Any] | None:
    path = bundle_dir / name / "last_json.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa a evidência operacional de PRACTICE e gera um ZIP único.")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--out-dir", default=None, help="Default: runs/debug/bundles")
    parser.add_argument("--timeout-sec", type=int, default=2400)
    parser.add_argument("--soak-cycles", type=int, default=6)
    parser.add_argument("--force-soak", action="store_true", help="Força novo soak dentro do practice-round.")
    parser.add_argument("--limit-orders", type=int, default=20)
    parser.add_argument("--limit-incidents", type=int, default=20)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    config_path = Path(args.config).resolve() if args.config else _default_config(repo_root)
    python_exe = _find_python(repo_root)
    env = _build_env(repo_root, config_path)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (repo_root / "runs" / "debug" / "bundles")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = out_dir / f"{timestamp}_practice_evidence_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    def runtime_cmd(*cmd_args: str) -> list[str]:
        cmd = [python_exe, "-m", "natbin.runtime_app", "--repo-root", str(repo_root)]
        if config_path is not None:
            cmd.extend(["--config", str(config_path)])
        cmd.extend(cmd_args)
        return cmd

    practice_round_cmd = ["practice-round", "--json", "--soak-cycles", str(args.soak_cycles)]
    if args.force_soak:
        practice_round_cmd.append("--force-soak")

    commands: list[tuple[str, list[str], int]] = [
        ("status", runtime_cmd("status", "--json"), min(args.timeout_sec, 300)),
        ("practice_preflight", runtime_cmd("practice-preflight", "--heal-soak", "--soak-cycles", str(args.soak_cycles), "--json"), args.timeout_sec),
        ("practice_round", runtime_cmd(*practice_round_cmd), args.timeout_sec),
        ("orders", runtime_cmd("orders", "--limit", str(args.limit_orders), "--json"), min(args.timeout_sec, 600)),
        ("reconcile", runtime_cmd("reconcile", "--json"), min(args.timeout_sec, 900)),
        ("incidents", runtime_cmd("incidents", "status", "--limit", str(args.limit_incidents), "--json"), min(args.timeout_sec, 600)),
    ]

    started = _now_utc()
    started_monotonic = time.monotonic()
    results = []
    for name, argv, timeout_sec in commands:
        results.append(_run_one(name=name, argv=argv, cwd=repo_root, env=env, timeout_sec=timeout_sec, output_dir=bundle_dir))

    finished = _now_utc()
    duration_sec = round(time.monotonic() - started_monotonic, 3)
    round_json = _last_json(bundle_dir, "practice_round") or {}
    preflight_json = _last_json(bundle_dir, "practice_preflight") or {}
    bundle_summary = {
        "practice_preflight": next((r.get("parsed_summary") for r in results if r.get("name") == "practice_preflight"), None),
        "practice_round": next((r.get("parsed_summary") for r in results if r.get("name") == "practice_round"), None),
        "orders": next((r.get("parsed_summary") for r in results if r.get("name") == "orders"), None),
        "reconcile": next((r.get("parsed_summary") for r in results if r.get("name") == "reconcile"), None),
        "incidents": next((r.get("parsed_summary") for r in results if r.get("name") == "incidents"), None),
        "practice_evidence": {
            "preflight_ready_for_long_practice": bool(preflight_json.get("ready_for_long_practice")),
            "round_ok": bool(round_json.get("round_ok")),
            "round_severity": str(round_json.get("severity") or "unknown") if round_json else "unknown",
            "blocked_reason": round_json.get("blocked_reason"),
        },
    }
    _write_text(bundle_dir / "bundle_summary.json", json.dumps(bundle_summary, ensure_ascii=False, indent=2) + "\n")
    manifest = {
        "kind": "practice_evidence_bundle",
        "repo_root": str(repo_root),
        "config_path": str(config_path) if config_path else None,
        "python_executable": python_exe,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "duration_sec": duration_sec,
        "bundle_dir": str(bundle_dir),
        "commands": results,
        "bundle_summary": bundle_summary,
        "dangerous": True,
        "note": "Este bundle pode submeter operações em PRACTICE porque executa practice-round.",
    }
    _write_text(bundle_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    zip_path = out_dir / f"{timestamp}_practice_evidence_bundle.zip"
    _zip_dir(bundle_dir, zip_path)

    print(json.dumps({
        "ok": True,
        "kind": "practice_evidence_bundle_result",
        "dangerous": True,
        "zip_path": str(zip_path),
        "bundle_dir": str(bundle_dir),
        "commands": [{"name": r["name"], "returncode": r["returncode"], "timed_out": r["timed_out"], "parsed_summary": r.get("parsed_summary")} for r in results],
        "bundle_summary": bundle_summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
