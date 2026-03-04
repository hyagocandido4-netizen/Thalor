#!/usr/bin/env python
"""Repo self-checks (fast, no network).

Run:
  python scripts/tools/selfcheck_repo.py

This script is intended to be CI-friendly (Windows runner) and also usable locally.
It verifies:
  - gate_meta API surface exists
  - observe_signal_topk_perday can be imported
  - observe_loop_auto.ps1 helper functions exist
  - Git ignore rules for secrets & heavy artifacts are in place

Exit code 0 on success, non-zero on failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _ok(msg: str) -> None:
    print(f"[selfcheck][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[selfcheck][FAIL] {msg}")
    raise SystemExit(2)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    root = here.parents[2]
    if not (root / "src" / "natbin").exists():
        _fail(f"repo root not found from {here}")
    return root


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _git_available(cwd: Path) -> bool:
    try:
        cp = _run_git(["--version"], cwd)
        return cp.returncode == 0
    except FileNotFoundError:
        return False


def _git_check_ignored(path: str, should_be_ignored: bool, cwd: Path) -> None:
    # `git check-ignore -q` returns:
    #   0 => ignored
    #   1 => not ignored
    cp = _run_git(["check-ignore", "-q", path], cwd)
    ignored = cp.returncode == 0
    if ignored != should_be_ignored:
        if should_be_ignored:
            _fail(f"{path} is NOT ignored by gitignore (it should be)")
        else:
            _fail(f"{path} IS ignored by gitignore (it should NOT be)")


def main() -> None:
    root = _repo_root()

    # 1) gate_meta API surface
    try:
        from natbin import gate_meta  # noqa: F401
        from natbin.gate_meta import (  # noqa: F401
            GATE_VERSION,
            META_FEATURES,
            compute_scores,
            train_base_cal_iso_meta,
        )
    except Exception as e:  # pragma: no cover
        _fail(f"gate_meta API broken: {e}")
    _ok("gate_meta API ok")

    # 1b) runtime contracts / migrations surface
    try:
        from natbin.runtime_contracts import (  # noqa: F401
            RUNTIME_CONTRACTS_VERSION,
            SIGNALS_V2_CONTRACT,
            EXECUTED_STATE_CONTRACT,
            contracts_manifest,
        )
        from natbin.runtime_migrations import (  # noqa: F401
            ensure_signals_v2 as _ensure_signals_v2_contract,
            ensure_executed_state_db as _ensure_executed_contract,
        )
    except Exception as e:  # pragma: no cover
        _fail(f"runtime contracts API broken: {e}")
    _ok("runtime contracts API ok")

    # 1c) runtime repositories surface
    try:
        from natbin.runtime_repos import (  # noqa: F401
            SignalsRepository,
            ExecutedStateRepository,
            RuntimeTradeLedger,
            preserve_existing_trade,
        )
    except Exception as e:  # pragma: no cover
        _fail(f"runtime repositories API broken: {e}")
    _ok("runtime repositories API ok")

    # 1d) autos policy layer surface
    try:
        from natbin.autos.summary_loader import SummaryScanResult, collect_checked_summaries  # noqa: F401
        from natbin.autos.volume_policy import build_payload as _build_auto_volume_payload  # noqa: F401
        from natbin.autos.isoblend_policy import compute_meta_iso_blend  # noqa: F401
        from natbin.autos.hour_policy import compute_hour_threshold  # noqa: F401
    except Exception as e:  # pragma: no cover
        _fail(f"autos policy layer API broken: {e}")
    _ok("autos policy layer API ok")

    # 1e) runtime observability surface
    try:
        from natbin.runtime_observability import (  # noqa: F401
            append_incident_event,
            build_incident_from_decision,
            build_decision_snapshot,
            write_detailed_decision_snapshot,
            write_latest_decision_snapshot,
        )
    except Exception as e:  # pragma: no cover
        _fail(f"runtime observability API broken: {e}")
    _ok("runtime observability API ok")

    # 1f) runtime scope/perf surface
    try:
        from natbin.runtime_scope import (  # noqa: F401
            RuntimeScope,
            build_scope,
            decision_latest_path as _scope_decision_latest_path,
            effective_env_path as _scope_effective_env_path,
            live_signals_csv_path as _scope_live_signals_csv_path,
            loop_status_path as _scope_loop_status_path,
            market_context_path as _scope_market_context_path,
            transcript_log_path as _scope_transcript_log_path,
        )
        from natbin.runtime_perf import (  # noqa: F401
            apply_runtime_sqlite_pragmas,
            load_json_cached,
            write_text_if_changed,
        )
    except Exception as e:  # pragma: no cover
        _fail(f"runtime scope/perf API broken: {e}")
    _ok("runtime scope/perf API ok")

    # 1g) runtime cycle API surface
    try:
        from natbin.runtime_cycle import (  # noqa: F401
            StepCommand,
            StepOutcome,
            build_auto_cycle_plan,
            classify_outcome_kind,
            run_plan,
            run_step,
        )
    except Exception as e:  # pragma: no cover
        _fail(f"runtime cycle API broken: {e}")
    _ok("runtime cycle API ok")

    # 1h) runtime quota API surface
    try:
        from natbin.runtime_quota import (  # noqa: F401
            OPEN as QUOTA_OPEN,
            MAX_K_REACHED,
            PACING_QUOTA_REACHED,
            QuotaSnapshot,
            build_quota_snapshot,
            compute_quota_day_context,
            pacing_allowed,
        )
    except Exception as e:  # pragma: no cover
        _fail(f"runtime quota API broken: {e}")
    _ok("runtime quota API ok")

    # 1h) runtime daemon API surface
    try:
        from natbin.runtime_daemon import (  # noqa: F401
            SleepPlan,
            acquire_lock,
            classify_report_ok,
            compute_day_reset_sleep,
            compute_next_candle_sleep,
            run_daemon,
            run_once,
        )
    except Exception as e:  # pragma: no cover
        _fail(f"runtime daemon API broken: {e}")
    _ok("runtime daemon API ok")

    # 2) observe import
    try:
        from natbin import observe_signal_topk_perday  # noqa: F401
    except Exception as e:  # pragma: no cover
        _fail(f"observe_signal_topk_perday import failed: {e}")
    _ok("observe_signal_topk_perday import ok")


    # 2b) python daemon wrapper exists
    ps1_py = root / "scripts" / "scheduler" / "observe_loop_auto_py.ps1"
    if not ps1_py.exists():
        _fail("scripts/scheduler/observe_loop_auto_py.ps1 not found")
    txt_py = ps1_py.read_text(encoding="utf-8", errors="replace")
    if "natbin.runtime_daemon" not in txt_py:
        _fail("observe_loop_auto_py.ps1 does not call natbin.runtime_daemon")
    if "QuotaAwareSleep" not in txt_py:
        _fail("observe_loop_auto_py.ps1 missing QuotaAwareSleep switch")
    _ok("observe_loop_auto_py.ps1 wrapper ok")

    # 3) observe_loop_auto.ps1 helpers
    ps1 = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        _fail("scripts/scheduler/observe_loop_auto.ps1 not found")
    txt = ps1.read_text(encoding="utf-8", errors="replace")
    for fn in ("AsStr", "AsInt", "AsFloat"):
        if (f"function {fn}" not in txt) and (f"function\t{fn}" not in txt):
            _fail(f"observe_loop_auto.ps1 missing helper function: {fn}")
    _ok("observe_loop_auto.ps1 helpers ok")



    # .env.example should exist (settings.py references it and CI docs expect it)
    if not (root / ".env.example").exists():
        _fail(".env.example is missing (create it or restore from template)")

    # 4) Secret & artifact hygiene (gitignore)
    if not _git_available(root):
        print("[selfcheck][WARN] git not available; skipping gitignore checks")
    else:
        _git_check_ignored(".env", True, root)
        _git_check_ignored(".env.example", False, root)
        # For ignored directories, use a trailing slash so `git check-ignore`
        # works even when the directory is absent in a clean CI checkout.
        _git_check_ignored("runs/", True, root)
        _git_check_ignored("data/", True, root)
        _ok("gitignore hygiene ok")


    # envutil import completeness (ensures env_* used are imported)
    try:
        _check_envutil_imports(root)
        _ok("envutil imports ok")
    except SystemExit:
        raise
    except Exception as e:
        _fail(f"envutil imports check failed: {e}")

    # pt-BR decimal comma safety (autos common parser)
    try:
        from natbin.autos.common import as_float as _as_float
        v = _as_float("0,07", 0.0)
        if abs(v - 0.07) > 1e-9:
            _fail(f"autos.common.as_float does not parse comma decimals: got {v}")
        _ok("autos common locale float parse ok")
    except SystemExit:
        raise
    except Exception as e:
        _fail(f"autos common locale parse check failed: {e}")

    print("[selfcheck] ALL OK")



# --- envutil import check (auto) ---

def _check_envutil_imports(repo_root: Path) -> None:
    import re

    env_call_re = re.compile(r"\b(env_(?:float|int|bool|str))\s*\(")
    rel_line = re.compile(r"^\s*from\s+\.envutil\s+import\s+(.+?)\s*$", re.M)
    abs_line = re.compile(r"^\s*from\s+natbin\.envutil\s+import\s+(.+?)\s*$", re.M)

    def parse_list(s: str) -> set[str]:
        s = s.split("#", 1)[0].strip().strip("() ")
        parts = [p.strip() for p in s.split(",") if p.strip()]
        out = set()
        for p in parts:
            out.add(p.split()[0])
        return out

    src = repo_root / "src" / "natbin"
    if not src.exists():
        return

    offenders = []
    for py in src.rglob("*.py"):
        if py.name == "envutil.py":
            continue
        try:
            t = py.read_text(encoding="utf-8")
        except Exception:
            continue

        used = set(env_call_re.findall(t))
        if not used:
            continue

        imported: set[str] = set()
        for m in rel_line.finditer(t):
            imported |= parse_list(m.group(1))
        for m in abs_line.finditer(t):
            imported |= parse_list(m.group(1))

        missing = used - imported
        if missing:
            offenders.append((py, ", ".join(sorted(used)), ", ".join(sorted(imported)), ", ".join(sorted(missing))))

    if offenders:
        lines = ["[selfcheck][FAIL] envutil imports incomplete:"]
        for py, used, imported, missing in offenders[:25]:
            lines.append(f"  - {py}\n      used={used}\n      imported={imported}\n      missing={missing}")
        raise SystemExit("\n".join(lines))


if __name__ == "__main__":
    # Ensure src/ is on sys.path when running from repo root
    # (CI sets PYTHONPATH in workflow, but locally this helps.)
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    main()