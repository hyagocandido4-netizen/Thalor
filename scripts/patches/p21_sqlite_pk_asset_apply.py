#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P21 — Fix SQLite PK for multi-asset: signals_v2 PRIMARY KEY(day, asset, ts)

Problem:
- signals_v2 currently uses PRIMARY KEY(day, ts)
- In multi-asset runs, different assets share the same (day, ts) -> INSERT OR REPLACE overwrites rows
  => collisions and silent data loss.

Fix:
- Update schema to PRIMARY KEY(day, asset, ts)
- Add automatic migration inside ensure_signals_v2():
  - detect old PK via PRAGMA table_info pk order
  - rename old table
  - create new table with correct PK
  - copy rows (asset NULL -> 'UNKNOWN')
  - drop old table

Safe:
- .bak_<timestamp> backup of observe_signal_topk_perday.py
- py_compile check

Run (repo root):
  .\\.venv\\Scripts\\python.exe .\\scripts\\patches\\p21_sqlite_pk_asset_apply.py
"""
from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def find_repo_root() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "src" / "natbin").exists():
        return cwd
    here = Path(__file__).resolve()
    for p in [here.parent] + list(here.parents):
        if (p / "src" / "natbin").exists():
            return p
    raise SystemExit("P21: não encontrei src/natbin. Rode a partir do root do repo.")


MIGRATION_MARKER = "# P21: migrate PK from (day, ts) to (day, asset, ts)"


def main() -> None:
    root = find_repo_root()
    path = root / "src" / "natbin" / "observe_signal_topk_perday.py"
    if not path.exists():
        raise SystemExit(f"P21: não achei {path}")

    text = path.read_text(encoding="utf-8")

    if MIGRATION_MARKER in text and "PRIMARY KEY(day, asset, ts)" in text:
        print("[P21] skip: already patched")
        return

    changed = False

    # 1) Update CREATE TABLE PK
    if "PRIMARY KEY(day, ts)" in text:
        text = text.replace("PRIMARY KEY(day, ts)", "PRIMARY KEY(day, asset, ts)")
        changed = True

    # 2) Replace cols=... PRAGMA block with info+pk_cols + migration
    old_line = '    cols = {r[1] for r in con.execute("PRAGMA table_info(signals_v2)").fetchall()}\n'
    if old_line not in text:
        raise SystemExit("[P21] não achei a linha cols = {.. PRAGMA table_info(signals_v2) ..}. Arquivo mudou.")

    migration_block = '''    info = con.execute("PRAGMA table_info(signals_v2)").fetchall()
    cols = {r[1] for r in info}
    pk_cols = [r[1] for r in sorted([r for r in info if r[5]], key=lambda x: x[5])]

    # P21: migrate PK from (day, ts) to (day, asset, ts) (multi-asset safe)
    if pk_cols == ["day", "ts"]:
        con.execute("ALTER TABLE signals_v2 RENAME TO signals_v2_old")

        con.execute(
            """
            CREATE TABLE signals_v2 (
              day TEXT NOT NULL,
              ts INTEGER NOT NULL,
              dt_local TEXT,
              action TEXT,
              price REAL,
              conf REAL,
              iso_score REAL,
              threshold REAL,
              executed_today INTEGER,
              close REAL,
              payout REAL,
              ev REAL,
              asset TEXT NOT NULL,

              model_version TEXT,
              train_rows INTEGER,
              train_end_ts INTEGER,
              best_source TEXT,
              tune_dir TEXT,
              feat_hash TEXT,
              gate_version TEXT,
              meta_model TEXT,

              gate_mode TEXT,
              score REAL,
              regime_ok INTEGER,
              thresh_on TEXT,
              k INTEGER,
              rank_in_day INTEGER,

              PRIMARY KEY(day, asset, ts)
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_signals_v2_ts ON signals_v2(ts)")

        old_cols = [r[1] for r in con.execute("PRAGMA table_info(signals_v2_old)").fetchall()]
        new_cols = [r[1] for r in con.execute("PRAGMA table_info(signals_v2)").fetchall()]
        common = [c for c in new_cols if c in old_cols]
        if common:
            select_expr = []
            for c in common:
                if c == "asset":
                    select_expr.append("COALESCE(asset,'UNKNOWN')")
                else:
                    select_expr.append(c)
            con.execute(
                f"INSERT OR IGNORE INTO signals_v2 ({','.join(common)}) "
                f"SELECT {','.join(select_expr)} FROM signals_v2_old"
            )

        con.execute("DROP TABLE signals_v2_old")
        con.commit()

        info = con.execute("PRAGMA table_info(signals_v2)").fetchall()
        cols = {r[1] for r in info}
'''

    text = text.replace(old_line, migration_block)
    changed = True

    if not changed:
        print("[P21] skip: no changes")
        return

    backup = path.with_suffix(path.suffix + f".bak_{stamp()}")
    shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8", newline="\n")

    try:
        py_compile.compile(str(path), doraise=True)
    except Exception as e:
        print("[P21] ERROR: py_compile falhou:", e)
        print("[P21] Backup em:", backup)
        raise SystemExit(2)

    print(f"[P21] OK {path}")
    print(f"[P21] backup: {backup}")
    print("[P21] Teste sugerido:")
    print("  - rode observe com 2 assets no mesmo dia; agora não deve sobrescrever sinais no sqlite.")


if __name__ == "__main__":
    main()
