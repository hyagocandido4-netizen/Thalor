from __future__ import annotations

"""Central runtime contracts for durable Thalor artifacts.

This module is intentionally small and dependency-light. It exists to make the
runtime storage contracts explicit before larger refactors move persistence logic
out of the observer/scheduler code.

Package B goal:
- stop scattering schema definitions across multiple files
- give CI/smoke tests one place to validate durable runtime tables
- prepare upcoming migrations/repositories without changing bot behaviour
"""

from dataclasses import dataclass
from typing import Mapping
import sqlite3

RUNTIME_CONTRACTS_VERSION = "packageB-v1"
SIGNALS_V2_SCHEMA_VERSION = 3
EXECUTED_STATE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class TableContract:
    name: str
    schema_version: int
    columns: Mapping[str, str]
    primary_key: tuple[str, ...]
    indexes: tuple[tuple[str, str], ...] = ()

    def required_columns(self) -> set[str]:
        return set(self.columns.keys())


SIGNALS_V2_CONTRACT = TableContract(
    name="signals_v2",
    schema_version=SIGNALS_V2_SCHEMA_VERSION,
    columns={
        "dt_local": "TEXT NOT NULL",
        "day": "TEXT NOT NULL",
        "asset": "TEXT NOT NULL",
        "interval_sec": "INTEGER NOT NULL",
        "ts": "INTEGER NOT NULL",
        "proba_up": "REAL NOT NULL",
        "conf": "REAL NOT NULL",
        "score": "REAL",
        "gate_mode": "TEXT",
        "gate_mode_requested": "TEXT",
        "gate_fail_closed": "INTEGER",
        "gate_fail_detail": "TEXT",
        "regime_ok": "INTEGER NOT NULL",
        "thresh_on": "TEXT",
        "threshold": "REAL NOT NULL",
        "k": "INTEGER",
        "rank_in_day": "INTEGER",
        "executed_today": "INTEGER",
        "budget_left": "INTEGER",
        "action": "TEXT NOT NULL",
        "reason": "TEXT NOT NULL",
        "blockers": "TEXT",
        "close": "REAL",
        "payout": "REAL",
        "ev": "REAL",
        "model_version": "TEXT",
        "train_rows": "INTEGER",
        "train_end_ts": "INTEGER",
        "best_source": "TEXT",
        "tune_dir": "TEXT",
        "feat_hash": "TEXT",
        "gate_version": "TEXT",
        "meta_model": "TEXT",
        "market_context_stale": "INTEGER",
        "market_context_fail_closed": "INTEGER",
        "cp_bootstrap_fallback": "TEXT",
        "cp_bootstrap_fallback_active": "INTEGER",
        "cp_available": "INTEGER",
    },
    primary_key=("day", "asset", "interval_sec", "ts"),
    indexes=(("idx_signals_v2_ts", "CREATE INDEX IF NOT EXISTS idx_signals_v2_ts ON signals_v2(ts)"),),
)


EXECUTED_STATE_CONTRACT = TableContract(
    name="executed",
    schema_version=EXECUTED_STATE_SCHEMA_VERSION,
    columns={
        "asset": "TEXT NOT NULL",
        "interval_sec": "INTEGER NOT NULL",
        "day": "TEXT NOT NULL",
        "ts": "INTEGER NOT NULL",
        "action": "TEXT NOT NULL",
        "conf": "REAL NOT NULL",
        "score": "REAL",
    },
    primary_key=("asset", "interval_sec", "day", "ts"),
    indexes=(("idx_exe_asset_interval_day", "CREATE INDEX IF NOT EXISTS idx_exe_asset_interval_day ON executed(asset, interval_sec, day)"),),
)


def table_info(con: sqlite3.Connection, table: str) -> list[sqlite3.Row | tuple]:
    return list(con.execute(f"PRAGMA table_info({table})").fetchall())


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in table_info(con, table)}


def table_pk(con: sqlite3.Connection, table: str) -> tuple[str, ...]:
    info = table_info(con, table)
    rows = sorted([r for r in info if r[5]], key=lambda x: x[5])
    return tuple(r[1] for r in rows)


def contract_matches(con: sqlite3.Connection, contract: TableContract) -> tuple[bool, list[str]]:
    issues: list[str] = []
    cols = table_columns(con, contract.name)
    pk = table_pk(con, contract.name)

    missing = sorted(contract.required_columns() - cols)
    if missing:
        issues.append(f"missing_columns:{','.join(missing)}")

    if pk != contract.primary_key:
        issues.append(f"pk_mismatch:{pk!r}!={contract.primary_key!r}")

    return (len(issues) == 0, issues)


def contracts_manifest() -> dict[str, object]:
    return {
        "runtime_contracts_version": RUNTIME_CONTRACTS_VERSION,
        "tables": {
            SIGNALS_V2_CONTRACT.name: {
                "schema_version": SIGNALS_V2_CONTRACT.schema_version,
                "primary_key": list(SIGNALS_V2_CONTRACT.primary_key),
                "columns": dict(SIGNALS_V2_CONTRACT.columns),
            },
            EXECUTED_STATE_CONTRACT.name: {
                "schema_version": EXECUTED_STATE_CONTRACT.schema_version,
                "primary_key": list(EXECUTED_STATE_CONTRACT.primary_key),
                "columns": dict(EXECUTED_STATE_CONTRACT.columns),
            },
        },
    }
