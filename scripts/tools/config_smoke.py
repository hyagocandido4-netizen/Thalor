#!/usr/bin/env python
"""Smoke test: configuration v2 (Package M foundation).

CI-friendly (no network). It validates:

* the typed config loader can parse the repo default config even with legacy .env
* a ResolvedConfig can be produced
* effective-config artifacts can be written under runs/config
* repo_root/config/base.yaml resolution works when CWD differs
"""

from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> None:
    from natbin.config.effective_dump import write_effective_config_latest, write_effective_config_snapshot
    from natbin.config.loader import load_resolved_config
    from natbin.runtime_app import build_context

    repo_root = ROOT
    cfg = load_resolved_config(config_path=repo_root / "config.yaml", repo_root=repo_root)
    assert cfg.asset, "asset missing"
    assert int(cfg.interval_sec) > 0, "interval_sec invalid"

    latest = write_effective_config_latest(cfg, repo_root=repo_root)
    snap = write_effective_config_snapshot(cfg, repo_root=repo_root, day="2099-12-31", cycle_id="smoke")

    assert latest.exists(), f"latest not written: {latest}"
    assert snap.exists(), f"snapshot not written: {snap}"

    print("[smoke][OK] config v2 loader ok")
    print(f"[smoke][OK] effective_config_latest={latest}")
    print(f"[smoke][OK] effective_config_snapshot={snap}")

    with tempfile.TemporaryDirectory(prefix="thalor_cfg_smoke_") as td:
        tmp = Path(td)
        (tmp / "config").mkdir(parents=True, exist_ok=True)
        (tmp / "config" / "base.yaml").write_text(
            "\n".join(
                [
                    "runtime:",
                    "  profile: smoke",
                    "assets:",
                    "  - asset: GBPUSD-OTC",
                    "    interval_sec: 60",
                    "    timezone: UTC",
                    "data:",
                    "  dataset_path: data/custom_dataset.csv",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp / ".env").write_text(
            "\n".join(
                [
                    "IQ_EMAIL=smoke@example.com",
                    "IQ_PASSWORD=smoke-secret",
                    "IQ_BALANCE_MODE=PRACTICE",
                    "THALOR__BROKER__BALANCE_MODE=REAL",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            os.chdir(repo_root)
            cfg2 = load_resolved_config(repo_root=tmp)
            assert cfg2.asset == "GBPUSD-OTC", cfg2.asset
            assert int(cfg2.interval_sec) == 60, cfg2.interval_sec
            assert cfg2.timezone == "UTC", cfg2.timezone
            assert cfg2.broker.email == "smoke@example.com", cfg2.broker.email
            assert cfg2.broker.balance_mode == "REAL", cfg2.broker.balance_mode
            print("[smoke][OK] repo_root + config/base.yaml + legacy .env compatibility ok")

            ctx = build_context(repo_root=tmp)
            assert ctx.repo_root == str(tmp.resolve())
            assert ctx.resolved_config["asset"] == "GBPUSD-OTC"
            assert Path(ctx.scoped_paths["market_context"]).is_absolute(), ctx.scoped_paths["market_context"]
            # Windows CI may represent temp paths using 8.3 short names.
            # Compare using paths anchored to ctx.repo_root (the canonical
            # representation chosen by build_context) instead of relying on
            # raw string containment.
            runs_root = (Path(ctx.repo_root) / "runs").resolve()
            mc_path = Path(ctx.scoped_paths["market_context"]).resolve()
            assert mc_path.is_relative_to(runs_root), f"market_context not under repo_root runs: {mc_path} vs {runs_root}"
            print("[smoke][OK] runtime_app build_context anchored to repo_root ok")
        finally:
            os.chdir(old_cwd)


if __name__ == "__main__":  # pragma: no cover
    main()
