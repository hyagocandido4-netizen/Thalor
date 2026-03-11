"""CI smoke test for natbin.runtime_app (no broker needed).

Validates that basic CLI entrypoints still work:

  - portfolio plan
  - portfolio status

This should not hit external network or require market data.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], *, cwd: Path) -> dict:
    try:
        out = subprocess.check_output(cmd, cwd=str(cwd), text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:  # pragma: no cover
        output = getattr(e, 'output', '') or ''
        raise RuntimeError(
            f"Command failed. cmd={cmd} rc={e.returncode}\n--- output ---\n{output}"
        ) from e
    try:
        return json.loads(out)
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Failed to parse JSON output. cmd={cmd}\n--- output ---\n{out}") from e


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    cfg = repo_root / "config" / "multi_asset.yaml"
    if not cfg.exists():
        raise SystemExit(f"missing config: {cfg}")

    base = [
        sys.executable,
        "-m",
        "natbin.runtime_app",
        "portfolio",
    ]

    plan = _run(base + ["plan", "--repo-root", str(repo_root), "--config", str(cfg), "--json"], cwd=repo_root)
    assert plan.get("ok") is True, plan
    assert plan.get("phase") == "portfolio_plan", plan

    status = _run(base + ["status", "--repo-root", str(repo_root), "--config", str(cfg), "--json"], cwd=repo_root)
    assert status.get("ok") is True, status
    assert status.get("phase") == "portfolio_status", status

    print("smoke_runtime_app: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
