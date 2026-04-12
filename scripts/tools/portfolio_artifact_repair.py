
from __future__ import annotations

import argparse
from pathlib import Path

from _th_closure_helpers import dump_json, repo_python, run_python_script, scope_tag_to_parts
from natbin.ops.canary_closure_core import choose_repair_scope_tags, classify_secondary_cp_meta_debt, summarize_signal_audit


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Safe canary artifact repair (prepare+candidate) for scopes needing refresh.")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--config", required=True)
    p.add_argument("--all-scopes", action="store_true", default=True)
    p.add_argument("--max-scopes", type=int, default=0, help="0 = all selected repair scopes.")
    p.add_argument("--timeout-sec", type=int, default=900)
    p.add_argument("--post-audit", action="store_true", default=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", default=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    audit_before = run_python_script(
        repo_root,
        "scripts/tools/portfolio_signal_artifact_audit.py",
        ["--config", args.config, "--all-scopes", "--json"],
        name="signal_artifact_audit_before",
        timeout_sec=args.timeout_sec,
    )
    repair_tags = choose_repair_scope_tags(audit_before.payload)
    if args.max_scopes and args.max_scopes > 0:
        repair_tags = repair_tags[: args.max_scopes]

    scope_results = []
    for tag in repair_tags:
        asset, interval_sec = scope_tag_to_parts(tag)
        prepare = {
            "name": "asset_prepare",
            "scope_tag": tag,
            "skipped": args.dry_run,
        }
        candidate = {
            "name": "asset_candidate",
            "scope_tag": tag,
            "skipped": args.dry_run,
        }
        if not args.dry_run:
            prepare_result = run_python_script(
                repo_root,
                "scripts/tools/invoke_runtime_app.py",
                ["--config", args.config, "asset", "prepare", "--asset", asset, "--interval-sec", str(interval_sec), "--json"],
                name=f"asset_prepare:{tag}",
                timeout_sec=args.timeout_sec,
            )
            prepare.update(prepare_result.to_summary())
            candidate_result = run_python_script(
                repo_root,
                "scripts/tools/invoke_runtime_app.py",
                ["--config", args.config, "asset", "candidate", "--asset", asset, "--interval-sec", str(interval_sec), "--json"],
                name=f"asset_candidate:{tag}",
                timeout_sec=args.timeout_sec,
            )
            candidate.update(candidate_result.to_summary())
        scope_results.append({"scope_tag": tag, "prepare": prepare, "candidate": candidate})

    audit_after = None
    if args.post_audit:
        audit_after = run_python_script(
            repo_root,
            "scripts/tools/portfolio_signal_artifact_audit.py",
            ["--config", args.config, "--all-scopes", "--json"],
            name="signal_artifact_audit_after",
            timeout_sec=args.timeout_sec,
        )

    payload = {
        "kind": "portfolio_artifact_repair",
        "ok": True,
        "severity": "ok",
        "repo_root": str(repo_root),
        "config_path": str((repo_root / args.config).resolve()) if not Path(args.config).is_absolute() else args.config,
        "dry_run": args.dry_run,
        "selected_repair_scopes": repair_tags,
        "summary_before": summarize_signal_audit(audit_before.payload),
        "summary_after": summarize_signal_audit(audit_after.payload) if audit_after else None,
        "scope_results": scope_results,
        "actions": [
            "repair_artifacts_and_rescan",
            "keep_canary_top1_single_position",
        ],
    }
    if payload["summary_after"]:
        before = payload["summary_before"]
        after = payload["summary_after"]
        remaining_repair_scopes = choose_repair_scope_tags(audit_after.payload)
        cp_meta_debt = classify_secondary_cp_meta_debt(audit_after.payload)
        payload["remaining_repair_scopes"] = remaining_repair_scopes
        payload["closure_debt"] = cp_meta_debt.to_payload() if cp_meta_debt is not None else None
        payload["severity"] = "warn" if remaining_repair_scopes else "ok"
        payload["ok"] = after["missing_artifact_scopes"] == 0
        payload["reduced_stale_artifacts"] = after["stale_artifact_scopes"] <= before["stale_artifact_scopes"]
        payload["reduced_cp_meta_missing"] = after["cp_meta_missing_scopes"] <= before["cp_meta_missing_scopes"]

    dump_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
