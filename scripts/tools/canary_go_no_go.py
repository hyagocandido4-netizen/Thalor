#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float | None = None


def _repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parents[2]


def _python_executable(repo_root: Path) -> str:
    if os.name == "nt":
        candidate = repo_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = repo_root / ".venv" / "bin" / "python"
    return str(candidate if candidate.exists() else Path(sys.executable).resolve())


def _with_src_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    src = str(repo_root / "src")
    existing = env.get("PYTHONPATH", "")
    sep = os.pathsep
    env["PYTHONPATH"] = src if not existing else src + sep + existing
    return env


def extract_last_json(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    best_with_kind: dict[str, Any] | None = None
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text[i:])
        except Exception:
            i += 1
            continue
        if isinstance(obj, dict):
            best = obj
            if obj.get("kind"):
                best_with_kind = obj
        i += max(end, 1)
    return best_with_kind or best


def run_closure_report(repo_root: Path, config: str, all_scopes: bool, timeout_sec: int = 420) -> CommandResult:
    python_exe = _python_executable(repo_root)
    script = repo_root / "scripts" / "tools" / "portfolio_canary_closure_report.py"
    if not script.exists():
        raise FileNotFoundError(f"Closure report script not found: {script}")
    cmd = [python_exe, str(script), "--config", config]
    if all_scopes:
        cmd.append("--all-scopes")
    cmd.extend(["--active-provider-probe", "--json"])
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            env=_with_src_env(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return CommandResult(command=cmd, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, timed_out=False)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=cmd,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            timed_out=True,
        )


def evaluate_go_no_go(payload: dict[str, Any]) -> dict[str, Any]:
    closure_state = str(payload.get("closure_state") or "unknown")
    provider = payload.get("provider") or {}
    provider_state = str(provider.get("stability_state") or "unknown")
    provider_ready_scopes = int(provider.get("provider_ready_scopes") or 0)
    blocking_cp_meta = int(payload.get("blocking_cp_meta_missing_scopes") or 0)
    blocking_gate_fail = int(payload.get("blocking_gate_fail_closed_scopes") or 0)
    repair_scope_tags = list(payload.get("repair_scope_tags") or [])

    acceptable_states = {"healthy_waiting_signal", "actionable_scope_ready"}
    provider_acceptable = provider_state in {"stable", "degraded"}
    go = (
        payload.get("ok") is True
        and closure_state in acceptable_states
        and provider_acceptable
        and provider_ready_scopes > 0
        and blocking_cp_meta == 0
        and blocking_gate_fail == 0
        and not repair_scope_tags
    )

    if go and closure_state == "actionable_scope_ready":
        decision = "GO_ACTIONABLE"
        message = "Envelope canary está fechado e há scope acionável pronto."
    elif go and closure_state == "healthy_waiting_signal":
        decision = "GO_WAITING_SIGNAL"
        message = "Envelope canary está fechado; operação conservadora pode rodar aguardando próximo sinal elegível."
    elif provider_state == "unstable":
        decision = "NO_GO_PROVIDER_UNSTABLE"
        message = "Provider instável para o envelope canary atual."
    elif closure_state == "repair_needed":
        decision = "NO_GO_REPAIR"
        message = "Ainda há reparos bloqueantes antes de fechar o canary."
    else:
        decision = "NO_GO_UNKNOWN"
        message = "Fechamento canary ainda não atingiu estado aceitável."

    exit_code = 0 if go else 2
    return {
        "kind": "canary_go_no_go",
        "ok": go,
        "decision": decision,
        "message": message,
        "exit_code": exit_code,
        "closure_state": closure_state,
        "recommended_action": payload.get("recommended_action"),
        "provider_state": provider_state,
        "provider_ready_scopes": provider_ready_scopes,
        "blocking_cp_meta_missing_scopes": blocking_cp_meta,
        "blocking_gate_fail_closed_scopes": blocking_gate_fail,
        "repair_scope_tags": repair_scope_tags,
        "closure_debts": payload.get("closure_debts") or [],
        "best_watch_scope_tag": (payload.get("signal_scan") or {}).get("best_watch_scope_tag"),
        "best_hold_scope_tag": (payload.get("signal_scan") or {}).get("best_hold_scope_tag"),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Final GO/NO-GO decisor para o envelope canary do Thalor.")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--config", required=True)
    parser.add_argument("--all-scopes", action="store_true", default=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=420)
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = _repo_root(args.repo_root)
    result = run_closure_report(repo_root=repo_root, config=args.config, all_scopes=args.all_scopes, timeout_sec=args.timeout_sec)
    payload = extract_last_json(result.stdout)
    if result.timed_out or result.returncode != 0 or not isinstance(payload, dict):
        out = {
            "kind": "canary_go_no_go",
            "ok": False,
            "decision": "NO_GO_TOOLING_ERROR",
            "message": "Não foi possível obter um closure_report válido.",
            "exit_code": 3,
            "closure_report_returncode": result.returncode,
            "timed_out": result.timed_out,
            "stderr_tail": (result.stderr or "")[-2000:],
            "stdout_tail": (result.stdout or "")[-2000:],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 3

    verdict = evaluate_go_no_go(payload)
    verdict["repo_root"] = str(repo_root)
    verdict["config_path"] = str((repo_root / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config).resolve())
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(f"[{verdict['decision']}] {verdict['message']}")
    return int(verdict["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
