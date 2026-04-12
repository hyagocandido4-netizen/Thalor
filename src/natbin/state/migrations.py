from __future__ import annotations

"""Explicit runtime migrations for durable tables.

Package B keeps behaviour unchanged while moving schema knowledge out of the
observer and reconcile scripts.
"""

import sqlite3
from ..runtime.contracts import SIGNALS_V2_CONTRACT, EXECUTED_STATE_CONTRACT


def ensure_signals_v2(con: sqlite3.Connection, *, default_interval: int = 300) -> None:
    desired_pk = list(SIGNALS_V2_CONTRACT.primary_key)
    desired_cols = dict(SIGNALS_V2_CONTRACT.columns)

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS signals_v2 (
          dt_local TEXT NOT NULL,
          day TEXT NOT NULL,
          asset TEXT NOT NULL,
          interval_sec INTEGER NOT NULL,
          ts INTEGER NOT NULL,
          proba_up REAL NOT NULL,
          conf REAL NOT NULL,
          score REAL,
          gate_mode TEXT,
          gate_mode_requested TEXT,
          gate_fail_closed INTEGER,
          gate_fail_detail TEXT,
          regime_ok INTEGER NOT NULL,
          thresh_on TEXT,
          threshold REAL NOT NULL,
          k INTEGER,
          rank_in_day INTEGER,
          executed_today INTEGER,
          budget_left INTEGER,
          action TEXT NOT NULL,
          reason TEXT NOT NULL,
          blockers TEXT,
          close REAL,
          payout REAL,
          ev REAL,
          model_version TEXT,
          train_rows INTEGER,
          train_end_ts INTEGER,
          best_source TEXT,
          tune_dir TEXT,
          feat_hash TEXT,
          gate_version TEXT,
          meta_model TEXT,
          market_context_stale INTEGER,
          market_context_fail_closed INTEGER,
          cp_bootstrap_fallback TEXT,
          cp_bootstrap_fallback_active INTEGER,
          cp_available INTEGER,
          PRIMARY KEY(day, asset, interval_sec, ts)
        )
        """
    )
    for _name, stmt in SIGNALS_V2_CONTRACT.indexes:
        con.execute(stmt)

    info = con.execute("PRAGMA table_info(signals_v2)").fetchall()
    cols = {r[1] for r in info}
    pk_cols = [r[1] for r in sorted([r for r in info if r[5]], key=lambda x: x[5])]

    needs_rebuild = ("interval_sec" not in cols) or (pk_cols != desired_pk)
    if needs_rebuild:
        con.execute("ALTER TABLE signals_v2 RENAME TO signals_v2_old")
        con.execute(
            """
            CREATE TABLE signals_v2 (
              dt_local TEXT NOT NULL,
              day TEXT NOT NULL,
              asset TEXT NOT NULL,
              interval_sec INTEGER NOT NULL,
              ts INTEGER NOT NULL,
              proba_up REAL NOT NULL,
              conf REAL NOT NULL,
              score REAL,
              gate_mode TEXT,
              gate_mode_requested TEXT,
              gate_fail_closed INTEGER,
              gate_fail_detail TEXT,
              regime_ok INTEGER NOT NULL,
              thresh_on TEXT,
              threshold REAL NOT NULL,
              k INTEGER,
              rank_in_day INTEGER,
              executed_today INTEGER,
              budget_left INTEGER,
              action TEXT NOT NULL,
              reason TEXT NOT NULL,
              blockers TEXT,
              close REAL,
              payout REAL,
              ev REAL,
              model_version TEXT,
              train_rows INTEGER,
              train_end_ts INTEGER,
              best_source TEXT,
              tune_dir TEXT,
              feat_hash TEXT,
              gate_version TEXT,
              meta_model TEXT,
              market_context_stale INTEGER,
              market_context_fail_closed INTEGER,
              PRIMARY KEY(day, asset, interval_sec, ts)
            )
            """
        )
        for _name, stmt in SIGNALS_V2_CONTRACT.indexes:
            con.execute(stmt)

        old_cols = {r[1] for r in con.execute("PRAGMA table_info(signals_v2_old)").fetchall()}
        select_expr = []
        insert_cols = []
        for c in desired_cols.keys():
            if c == "asset":
                if c in old_cols:
                    select_expr.append("COALESCE(NULLIF(asset,''),'UNKNOWN') AS asset")
                else:
                    select_expr.append("'UNKNOWN' AS asset")
                insert_cols.append(c)
            elif c == "interval_sec":
                if c in old_cols:
                    select_expr.append(f"COALESCE(interval_sec,{int(default_interval)}) AS interval_sec")
                else:
                    select_expr.append(f"{int(default_interval)} AS interval_sec")
                insert_cols.append(c)
            elif c in old_cols:
                select_expr.append(c)
                insert_cols.append(c)
        if insert_cols:
            con.execute(
                f"INSERT OR IGNORE INTO signals_v2 ({','.join(insert_cols)}) SELECT {','.join(select_expr)} FROM signals_v2_old"
            )
        con.execute("DROP TABLE signals_v2_old")
        con.commit()
        info = con.execute("PRAGMA table_info(signals_v2)").fetchall()
        cols = {r[1] for r in info}

    add_cols = {k: v.replace(" NOT NULL", "") for k, v in desired_cols.items() if k not in {"day", "asset", "interval_sec", "ts"}}
    for c, typ in add_cols.items():
        if c not in cols:
            con.execute(f"ALTER TABLE signals_v2 ADD COLUMN {c} {typ}")
    con.commit()


def ensure_executed_state_db(con: sqlite3.Connection, *, default_interval: int = 300) -> None:
    con.execute("PRAGMA journal_mode=WAL;")
    desired_pk = list(EXECUTED_STATE_CONTRACT.primary_key)
    info = con.execute("PRAGMA table_info(executed)").fetchall()
    cols = {r[1] for r in info}
    pk_cols = [r[1] for r in sorted([r for r in info if r[5]], key=lambda x: x[5])]

    if cols and (("asset" not in cols) or ("interval_sec" not in cols) or (pk_cols != desired_pk)):
        con.execute("ALTER TABLE executed RENAME TO executed_legacy")
        cols = set()

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS executed (
          asset TEXT NOT NULL,
          interval_sec INTEGER NOT NULL,
          day TEXT NOT NULL,
          ts INTEGER NOT NULL,
          action TEXT NOT NULL,
          conf REAL NOT NULL,
          score REAL,
          PRIMARY KEY(asset, interval_sec, day, ts)
        )
        """
    )

    legacy = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='executed_legacy'"
    ).fetchone()
    if legacy:
        old_cols = {r[1] for r in con.execute("PRAGMA table_info(executed_legacy)").fetchall()}
        try:
            if "interval_sec" in old_cols:
                con.execute(
                    """
                    INSERT OR IGNORE INTO executed(asset, interval_sec, day, ts, action, conf, score)
                    SELECT COALESCE(NULLIF(asset,''),'LEGACY'), COALESCE(interval_sec, ?), day, ts, action, conf, score
                    FROM executed_legacy
                    """,
                    (int(default_interval),),
                )
            else:
                con.execute(
                    """
                    INSERT OR IGNORE INTO executed(asset, interval_sec, day, ts, action, conf, score)
                    SELECT COALESCE(NULLIF(asset,''),'LEGACY'), ?, day, ts, action, conf, score
                    FROM executed_legacy
                    """,
                    (int(default_interval),),
                )
        except Exception:
            pass
        con.execute("DROP TABLE executed_legacy")

    for _name, stmt in EXECUTED_STATE_CONTRACT.indexes:
        con.execute(stmt)
    con.commit()
