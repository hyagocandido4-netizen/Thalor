
from __future__ import annotations

import argparse
from pathlib import Path

from _th_closure_helpers import dump_json, run_python_script
from natbin.ops.canary_closure_core import classify_closure


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Consolidated closure report for the portfolio canary.")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--config", required=True)
    p.add_argument("--all-scopes", action="store_true", default=True)
    p.set_defaults(active_provider_probe=True)
    p.add_argument("--active-provider-probe", dest="active_provider_probe", action="store_true")
    p.add_argument("--passive-provider-probe", dest="active_provider_probe", action="store_false")
    p.add_argument("--timeout-sec", type=int, default=900)
    p.add_argument("--json", action="store_true", default=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    provider_args = ["--config", args.config, "--all-scopes", "--json"]
    if args.active_provider_probe:
        provider_args.insert(-1, "--active-provider-probe")
    provider = run_python_script(
        repo_root,
        "scripts/tools/provider_stability_report.py",
        provider_args,
        name="provider_stability_report",
        timeout_sec=args.timeout_sec,
    )
    signal_scan = run_python_script(
        repo_root,
        "scripts/tools/portfolio_canary_signal_proof.py",
        ["--config", args.config, "--all-scopes", "--json"],
        name="portfolio_canary_signal_scan",
        timeout_sec=args.timeout_sec,
    )
    signal_audit = run_python_script(
        repo_root,
        "scripts/tools/portfolio_signal_artifact_audit.py",
        ["--config", args.config, "--all-scopes", "--json"],
        name="signal_artifact_audit",
        timeout_sec=args.timeout_sec,
    )

    payload = classify_closure(provider.payload, signal_scan.payload, signal_audit.payload)
    payload.update(
        {
            "repo_root": str(repo_root),
            "config_path": str((repo_root / args.config).resolve()) if not Path(args.config).is_absolute() else args.config,
            "all_scopes": True,
            "inputs": {
                "provider_stability_report": provider.to_summary(),
                "portfolio_canary_signal_scan": signal_scan.to_summary(),
                "signal_artifact_audit": signal_audit.to_summary(),
            },
        }
    )
    dump_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
