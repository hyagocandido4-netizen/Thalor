from __future__ import annotations

import importlib
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _ok(msg: str) -> None:
    print(f"[hotfix-h1-h6][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[hotfix-h1-h6][FAIL] {msg}")
    raise SystemExit(2)


def main() -> None:
    cfg_loader = importlib.import_module("natbin.config.loader")
    compat = importlib.import_module("natbin.config.compat_runtime")
    cfg_legacy = importlib.import_module("natbin.config.legacy")
    cfg_settings = importlib.import_module("natbin.config.settings")
    models = importlib.import_module("natbin.portfolio.models")
    control_repo = importlib.import_module("natbin.state.control_repo")
    intelligence_runtime = importlib.import_module("natbin.intelligence.runtime")
    iq_mod = importlib.import_module("natbin.brokers.iqoption")

    cfg = cfg_loader.load_resolved_config(repo_root=ROOT)
    for field_name in ["security", "notifications", "intelligence"]:
        if getattr(cfg, field_name, None) is None:
            _fail(f"resolved config missing {field_name}")
    _ok("resolved config keeps security/notifications/intelligence")

    legacy_cfg = cfg_legacy.load_cfg()
    if legacy_cfg["asset"] != cfg_settings.ASSET:
        _fail("legacy/settings asset mismatch")
    _ok("legacy/settings bridge ok")

    candidate = models.CandidateDecision(
        scope_tag="EURUSD-OTC_300s",
        asset="EURUSD-OTC",
        interval_sec=300,
        day="2026-03-12",
        ts=1773209700,
        action="CALL",
        score=0.10,
        conf=0.20,
        ev=0.30,
        reason=None,
        blockers=None,
        decision_path=None,
        raw={},
        intelligence_score=0.90,
    )
    if candidate.rank_value(weight=1.0, prefer_ev=True) != 0.9:
        _fail("CandidateDecision rank must prioritize intelligence_score")
    _ok("candidate rank uses intelligence_score")

    paths = control_repo.control_artifact_paths(repo_root=ROOT, asset="EURUSD-OTC", interval_sec=300)
    for name in ["guard", "lifecycle", "security", "release", "alerts", "incidents"]:
        if name not in paths:
            _fail(f"control artifact missing {name}")
    _ok("control artifact registry includes hardening artifacts")

    with tempfile.TemporaryDirectory() as td:
        iq = iq_mod.IQOptionAdapter(repo_root=td, account_mode="PRACTICE")
        if iq.broker_name() != "iqoption":
            _fail("IQOptionAdapter broker_name mismatch")
        health = iq.healthcheck()
        if health.broker_name != "iqoption":
            _fail("IQOptionAdapter healthcheck contract mismatch")
    _ok("IQOptionAdapter constructor contract ok")

    print("[hotfix-h1-h6] ALL OK")


if __name__ == "__main__":
    main()
