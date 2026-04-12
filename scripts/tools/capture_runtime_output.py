from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
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


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _sanitize_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.strip())
    return cleaned.strip("._") or "capture"


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


def _zip_dir(source_dir: Path, zip_path: Path) -> None:
    _ensure_parent(zip_path)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(source_dir)))


import zipfile  # after function defs to keep imports grouped logically


def main() -> int:
    parser = argparse.ArgumentParser(description="Captura stdout/stderr/exit code de um comando runtime_app e gera um ZIP.")
    parser.add_argument("--repo-root", default=None, help="Raiz do repositório. Default: detectado pelo script.")
    parser.add_argument("--config", default=None, help="Arquivo de config. Default: live_controlled_practice.yaml se existir.")
    parser.add_argument("--label", default="runtime_capture", help="Rótulo usado no nome do ZIP.")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Timeout do comando alvo em segundos.")
    parser.add_argument("--out-dir", default=None, help="Diretório base de saída. Default: runs/debug/captures")
    parser.add_argument("--propagate-exit-code", action="store_true", help="Retorna o exit code do comando alvo.")
    parser.add_argument("runtime_args", nargs=argparse.REMAINDER, help="Argumentos do runtime_app após --")
    args = parser.parse_args()

    runtime_args = list(args.runtime_args)
    if runtime_args and runtime_args[0] == "--":
        runtime_args = runtime_args[1:]
    if not runtime_args:
        parser.error("Informe o comando do runtime_app após --. Ex.: -- practice-preflight --json")

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    config_path = Path(args.config).resolve() if args.config else _default_config(repo_root)
    python_exe = _find_python(repo_root)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else (repo_root / "runs" / "debug" / "captures")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = _sanitize_label(args.label or "runtime_capture")
    capture_dir = out_dir / f"{timestamp}_{label}"
    capture_dir.mkdir(parents=True, exist_ok=True)

    command = [python_exe, "-m", "natbin.runtime_app", "--repo-root", str(repo_root)]
    if config_path is not None:
        command.extend(["--config", str(config_path)])
    command.extend(runtime_args)

    env = _build_env(repo_root=repo_root, config_path=config_path)

    started_monotonic = time.monotonic()
    started_at = _now_utc()
    timed_out = False
    target_exit_code = None
    stdout_text = ""
    stderr_text = ""

    try:
        proc = subprocess.run(
            command,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=max(1, int(args.timeout_sec)),
        )
        target_exit_code = int(proc.returncode)
        stdout_text = _safe_text(proc.stdout)
        stderr_text = _safe_text(proc.stderr)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        target_exit_code = 124
        stdout_text = _safe_text(exc.stdout)
        stderr_text = _safe_text(exc.stderr)
    finished_at = _now_utc()
    duration_sec = round(time.monotonic() - started_monotonic, 3)

    _write_text(capture_dir / "stdout.txt", stdout_text)
    _write_text(capture_dir / "stderr.txt", stderr_text)
    _write_text(capture_dir / "exit_code.txt", f"{target_exit_code}\n")
    _write_text(capture_dir / "command.txt", " ".join(command) + "\n")
    parsed_summary = write_json_summary(base_dir=capture_dir, stdout_text=stdout_text)

    manifest = {
        "kind": "runtime_capture",
        "label": label,
        "repo_root": str(repo_root),
        "config_path": str(config_path) if config_path else None,
        "python_executable": python_exe,
        "command": command,
        "timeout_sec": int(args.timeout_sec),
        "timed_out": timed_out,
        "target_exit_code": target_exit_code,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "duration_sec": duration_sec,
        "capture_dir": str(capture_dir),
        "stdout_path": str(capture_dir / "stdout.txt"),
        "stderr_path": str(capture_dir / "stderr.txt"),
        "exit_code_path": str(capture_dir / "exit_code.txt"),
        "parsed_summary": parsed_summary,
    }
    _write_text(capture_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    zip_path = out_dir / f"{timestamp}_{label}.zip"
    _zip_dir(capture_dir, zip_path)

    result = {
        "ok": True,
        "kind": "runtime_capture_result",
        "zip_path": str(zip_path),
        "capture_dir": str(capture_dir),
        "target_exit_code": target_exit_code,
        "timed_out": timed_out,
        "duration_sec": duration_sec,
        "command": command,
        "parsed_summary": parsed_summary,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.propagate_exit_code:
        return int(target_exit_code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
