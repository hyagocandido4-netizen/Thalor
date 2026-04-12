
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from _th_closure_helpers import CommandResult, dump_json, run_python_script


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Capture a bounded canary-closure bundle.")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--config", required=True)
    p.add_argument("--repair-first", action="store_true", default=True)
    p.add_argument("--active-provider-probe", action="store_true")
    p.add_argument("--timeout-sec", type=int, default=1200)
    p.add_argument("--json", action="store_true", default=True)
    return p


def write_result(root: Path, result: CommandResult) -> dict[str, object]:
    out_dir = root / result.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "command.txt").write_text(" ".join(result.argv), encoding="utf-8")
    (out_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    (out_dir / "exit_code.txt").write_text(str(result.returncode), encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(result.to_summary(), indent=2), encoding="utf-8")
    if result.payload is not None:
        (out_dir / "last_json.json").write_text(json.dumps(result.payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return result.to_summary()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = repo_root / "runs" / "debug" / "bundles" / f"{ts}_canary_closure_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    commands: list[CommandResult] = []

    if args.repair_first:
        commands.append(
            run_python_script(
                repo_root,
                "scripts/tools/portfolio_artifact_repair.py",
                ["--config", args.config, "--all-scopes", "--json"],
                name="portfolio_artifact_repair",
                timeout_sec=args.timeout_sec,
            )
        )

    provider_args = ["--config", args.config, "--all-scopes", "--json"]
    if args.active_provider_probe:
        provider_args.insert(-1, "--active-provider-probe")

    commands.extend(
        [
            run_python_script(
                repo_root,
                "scripts/tools/provider_stability_report.py",
                provider_args,
                name="provider_stability_report",
                timeout_sec=args.timeout_sec,
            ),
            run_python_script(
                repo_root,
                "scripts/tools/portfolio_signal_artifact_audit.py",
                ["--config", args.config, "--all-scopes", "--json"],
                name="signal_artifact_audit",
                timeout_sec=args.timeout_sec,
            ),
            run_python_script(
                repo_root,
                "scripts/tools/portfolio_canary_signal_proof.py",
                ["--config", args.config, "--all-scopes", "--json"],
                name="portfolio_canary_signal_scan",
                timeout_sec=args.timeout_sec,
            ),
            run_python_script(
                repo_root,
                "scripts/tools/portfolio_canary_closure_report.py",
                ["--config", args.config, "--all-scopes", "--json"],
                name="canary_closure_report",
                timeout_sec=args.timeout_sec,
            ),
        ]
    )

    command_summaries = []
    for result in commands:
        command_summaries.append(write_result(bundle_dir, result))

    closure_payload = next((r.payload for r in commands if r.name == "canary_closure_report"), None) or {}
    bundle_summary = {
        "kind": "canary_closure_bundle",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str((repo_root / args.config).resolve()) if not Path(args.config).is_absolute() else args.config,
        "repair_first": args.repair_first,
        "commands": command_summaries,
        "closure_state": closure_payload.get("closure_state"),
        "recommended_action": closure_payload.get("recommended_action"),
        "provider_state": ((closure_payload.get("provider") or {}).get("stability_state")),
        "provider_ready_scopes": ((closure_payload.get("provider") or {}).get("provider_ready_scopes")),
        "signal_actionable_scopes": ((closure_payload.get("signal_scan") or {}).get("actionable_scopes")),
        "signal_watch_scopes": ((closure_payload.get("signal_scan") or {}).get("watch_scopes")),
        "signal_hold_scopes": ((closure_payload.get("signal_scan") or {}).get("hold_scopes")),
        "stale_artifact_scopes": ((closure_payload.get("signal_audit") or {}).get("stale_artifact_scopes")),
        "cp_meta_missing_scopes": ((closure_payload.get("signal_audit") or {}).get("cp_meta_missing_scopes")),
        "dominant_nontrade_reason": ((closure_payload.get("signal_audit") or {}).get("dominant_nontrade_reason")),
        "repair_scope_tags": list(closure_payload.get("repair_scope_tags") or []),
        "closure_debts": list(closure_payload.get("closure_debts") or []),
        "blocking_cp_meta_missing_scopes": closure_payload.get("blocking_cp_meta_missing_scopes"),
        "blocking_gate_fail_closed_scopes": closure_payload.get("blocking_gate_fail_closed_scopes"),
        "ok": bool(closure_payload.get("ok")),
        "severity": closure_payload.get("severity"),
    }
    (bundle_dir / "bundle_summary.json").write_text(json.dumps(bundle_summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (bundle_dir / "manifest.json").write_text(
        json.dumps(
            {
                "kind": "canary_closure_bundle_manifest",
                "bundle_dir": str(bundle_dir),
                "command_count": len(commands),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    zip_path = bundle_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_dir))

    dump_json(
        {
            "kind": "canary_closure_bundle_result",
            "ok": True,
            "dangerous": False,
            "zip_path": str(zip_path),
            "bundle_dir": str(bundle_dir),
            "bundle_summary": bundle_summary,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
