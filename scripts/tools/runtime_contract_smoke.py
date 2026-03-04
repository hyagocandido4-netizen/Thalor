#!/usr/bin/env python
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path


def _ok(msg: str) -> None:
    print(f"[contract-smoke][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[contract-smoke][FAIL] {msg}")
    raise SystemExit(2)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.runtime_contracts import (
        RUNTIME_CONTRACTS_VERSION,
        SIGNALS_V2_CONTRACT,
        EXECUTED_STATE_CONTRACT,
        contract_matches,
        contracts_manifest,
    )
    from natbin.runtime_migrations import ensure_signals_v2, ensure_executed_state_db

    manifest = contracts_manifest()
    if manifest.get("runtime_contracts_version") != RUNTIME_CONTRACTS_VERSION:
        _fail("contracts manifest version mismatch")
    _ok("contracts manifest ok")

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "runtime.sqlite3"
        con = sqlite3.connect(str(db_path))
        try:
            ensure_signals_v2(con, default_interval=300)
            ok, issues = contract_matches(con, SIGNALS_V2_CONTRACT)
            if not ok:
                _fail(f"signals_v2 contract mismatch: {issues}")
            _ok("signals_v2 contract ok")

            ensure_executed_state_db(con, default_interval=300)
            ok, issues = contract_matches(con, EXECUTED_STATE_CONTRACT)
            if not ok:
                _fail(f"executed contract mismatch: {issues}")
            _ok("executed contract ok")
        finally:
            con.close()

    print("[contract-smoke] ALL OK")


if __name__ == "__main__":
    main()
