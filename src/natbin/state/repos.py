from __future__ import annotations

"""Runtime repositories for durable Thalor state.

Package C extracts persistence/state access out of the observer script while
preserving runtime behaviour. The goal is to make SQLite/state interactions
explicit, testable, and reusable by both the live observer and maintenance
commands.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import sqlite3

from .migrations import ensure_executed_state_db, ensure_signals_v2
from ..runtime_perf import apply_runtime_sqlite_pragmas

TRADE_ACTIONS = {"CALL", "PUT"}


@dataclass(frozen=True)
class SignalKey:
    day: str
    asset: str
    interval_sec: int
    ts: int

    @classmethod
    def from_row(cls, row: dict[str, Any], *, default_interval: int = 300) -> "SignalKey":
        day = str(row.get("day") or "")
        asset = str(row.get("asset") or "")
        try:
            interval_sec = int(row.get("interval_sec") or default_interval)
        except Exception:
            interval_sec = int(default_interval)
        try:
            ts = int(row.get("ts") or 0)
        except Exception:
            ts = 0
        return cls(day=day, asset=asset, interval_sec=interval_sec, ts=ts)


def preserve_existing_trade(existing_action: str | None, incoming_action: str | None) -> bool:
    """Return True when an already-emitted trade row must remain immutable."""
    existing = str(existing_action or "").upper()
    return existing in TRADE_ACTIONS


def signals_db_path(path: str | Path = "runs/live_signals.sqlite3") -> Path:
    return Path(path)


def state_db_path(path: str | Path = "runs/live_topk_state.sqlite3") -> Path:
    return Path(path)


class SignalsRepository:
    def __init__(self, db_path: str | Path = "runs/live_signals.sqlite3", *, default_interval: int = 300) -> None:
        self.path = signals_db_path(db_path)
        self.default_interval = int(default_interval)

    def _connect(self, *, ensure_schema: bool = True) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path))
        apply_runtime_sqlite_pragmas(con)
        con.row_factory = sqlite3.Row
        if ensure_schema:
            ensure_signals_v2(con, default_interval=self.default_interval)
        return con

    def table_present(self) -> bool:
        if not self.path.exists():
            return False
        con = self._connect(ensure_schema=False)
        try:
            row = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals_v2' LIMIT 1"
            ).fetchone()
            return row is not None
        finally:
            con.close()

    def write_row(self, row: dict[str, Any]) -> dict[str, Any]:
        key = SignalKey.from_row(row, default_interval=self.default_interval)
        con = self._connect()
        try:
            existing = con.execute(
                "SELECT action FROM signals_v2 WHERE day=? AND asset=? AND interval_sec=? AND ts=? LIMIT 1",
                (key.day, key.asset, int(key.interval_sec), key.ts),
            ).fetchone()
            if existing is not None and preserve_existing_trade(existing["action"], row.get("action")):
                return {"written": False, "preserved_trade": True, "key": key}
            cols = list(row.keys())
            placeholders = ",".join(["?"] * len(cols))
            sql = f"INSERT OR REPLACE INTO signals_v2 ({','.join(cols)}) VALUES ({placeholders})"
            con.execute(sql, [row[c] for c in cols])
            con.commit()
            return {"written": True, "preserved_trade": False, "key": key}
        finally:
            con.close()

    def fetch_trade_rows(self, asset: str, interval_sec: int, day: str, *, ts: int | None = None) -> list[sqlite3.Row]:
        if not self.path.exists():
            return []
        con = self._connect(ensure_schema=False)
        try:
            if not self.table_present():
                return []
            sql = (
                "SELECT asset, interval_sec, day, ts, action, conf, score "
                "FROM signals_v2 WHERE asset=? AND interval_sec=? AND day=? AND action IN ('CALL','PUT')"
            )
            params: list[Any] = [str(asset), int(interval_sec), str(day)]
            if ts is not None:
                sql += " AND ts=?"
                params.append(int(ts))
            sql += " ORDER BY ts"
            return list(con.execute(sql, tuple(params)).fetchall())
        except Exception:
            return []
        finally:
            con.close()

    def distinct_recent_days(self, days: int) -> list[str]:
        if not self.path.exists():
            return []
        con = self._connect(ensure_schema=False)
        try:
            if not self.table_present():
                return []
            rows = con.execute(
                "SELECT DISTINCT day FROM signals_v2 WHERE day IS NOT NULL AND day<>'' ORDER BY day DESC LIMIT ?",
                (int(max(1, days)),),
            ).fetchall()
            return [str(r[0]) for r in rows if r and r[0] is not None]
        finally:
            con.close()

    def fetch_trade_rows_for_days(self, days: Iterable[str]) -> list[sqlite3.Row]:
        days_list = [str(d) for d in days if str(d).strip()]
        if not self.path.exists() or not days_list:
            return []
        con = self._connect(ensure_schema=False)
        try:
            if not self.table_present():
                return []
            marks = ",".join("?" * len(days_list))
            try:
                sql = f"""
                    SELECT
                      COALESCE(NULLIF(asset, ''), 'UNKNOWN') AS asset,
                      COALESCE(interval_sec, {int(self.default_interval)}) AS interval_sec,
                      day,
                      ts,
                      action,
                      conf,
                      score
                    FROM signals_v2
                    WHERE action IN ('CALL','PUT')
                      AND day IN ({marks})
                    ORDER BY day ASC, ts ASC
                """
                return list(con.execute(sql, tuple(days_list)).fetchall())
            except Exception:
                sql = f"""
                    SELECT
                      COALESCE(NULLIF(asset, ''), 'UNKNOWN') AS asset,
                      {int(self.default_interval)} AS interval_sec,
                      day,
                      ts,
                      action,
                      conf,
                      score
                    FROM signals_v2
                    WHERE action IN ('CALL','PUT')
                      AND day IN ({marks})
                    ORDER BY day ASC, ts ASC
                """
                return list(con.execute(sql, tuple(days_list)).fetchall())
        finally:
            con.close()


class ExecutedStateRepository:
    def __init__(self, db_path: str | Path = "runs/live_topk_state.sqlite3", *, default_interval: int = 300) -> None:
        self.path = state_db_path(db_path)
        self.default_interval = int(default_interval)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path))
        apply_runtime_sqlite_pragmas(con)
        ensure_executed_state_db(con, default_interval=self.default_interval)
        return con

    def count_day(self, asset: str, interval_sec: int, day: str) -> int:
        con = self._connect()
        try:
            cur = con.execute(
                "SELECT COUNT(*) FROM executed WHERE asset=? AND interval_sec=? AND day=?",
                (str(asset), int(interval_sec), str(day)),
            )
            return int(cur.fetchone()[0] or 0)
        finally:
            con.close()

    def last_ts(self, asset: str, interval_sec: int, day: str) -> int | None:
        con = self._connect()
        try:
            cur = con.execute(
                "SELECT MAX(ts) FROM executed WHERE asset=? AND interval_sec=? AND day=?",
                (str(asset), int(interval_sec), str(day)),
            )
            row = cur.fetchone()
            if not row:
                return None
            v = row[0]
            return int(v) if v is not None else None
        finally:
            con.close()

    def exists(self, asset: str, interval_sec: int, day: str, ts: int) -> bool:
        con = self._connect()
        try:
            cur = con.execute(
                "SELECT 1 FROM executed WHERE asset=? AND interval_sec=? AND day=? AND ts=? LIMIT 1",
                (str(asset), int(interval_sec), str(day), int(ts)),
            )
            return cur.fetchone() is not None
        finally:
            con.close()

    def upsert_execution(self, asset: str, interval_sec: int, day: str, ts: int, action: str, conf: float, score: float) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO executed(asset, interval_sec, day, ts, action, conf, score) VALUES(?,?,?,?,?,?,?)",
                (str(asset), int(interval_sec), str(day), int(ts), str(action), float(conf), float(score)),
            )
            con.commit()
        finally:
            con.close()

    def insert_ignore_trade_rows(self, rows: Iterable[sqlite3.Row | dict[str, Any]]) -> tuple[int, int]:
        rows_list = list(rows)
        if not rows_list:
            return 0, 0
        con = self._connect()
        try:
            before = con.total_changes
            existing = 0
            for r in rows_list:
                if isinstance(r, sqlite3.Row):
                    asset = str(r["asset"] or "UNKNOWN")
                    interval_sec = int(r["interval_sec"] or self.default_interval)
                    day = str(r["day"] or "")
                    ts = int(r["ts"] or 0)
                    action = str(r["action"] or "").upper()
                    conf = float(r["conf"] or 0.0)
                    score = r["score"]
                else:
                    asset = str(r.get("asset") or "UNKNOWN")
                    interval_sec = int(r.get("interval_sec") or self.default_interval)
                    day = str(r.get("day") or "")
                    ts = int(r.get("ts") or 0)
                    action = str(r.get("action") or "").upper()
                    conf = float(r.get("conf") or 0.0)
                    score = r.get("score")
                prev = con.total_changes
                con.execute(
                    "INSERT OR IGNORE INTO executed(asset, interval_sec, day, ts, action, conf, score) VALUES(?,?,?,?,?,?,?)",
                    (asset, int(interval_sec), day, ts, action, conf, score),
                )
                if con.total_changes == prev:
                    existing += 1
            con.commit()
            inserted = int(con.total_changes - before)
            return inserted, int(existing)
        finally:
            con.close()


class RuntimeTradeLedger:
    def __init__(
        self,
        *,
        signals_db: str | Path = "runs/live_signals.sqlite3",
        state_db: str | Path = "runs/live_topk_state.sqlite3",
        default_interval: int = 300,
    ) -> None:
        self.default_interval = int(default_interval)
        self.signals = SignalsRepository(signals_db, default_interval=self.default_interval)
        self.state = ExecutedStateRepository(state_db, default_interval=self.default_interval)

    def heal_state_from_signals(self, asset: str, interval_sec: int, day: str, *, ts: int | None = None, log: bool = False) -> int:
        rows = self.signals.fetch_trade_rows(asset, interval_sec, day, ts=ts)
        inserted, _existing = self.state.insert_ignore_trade_rows(rows)
        if log and inserted > 0:
            print(f"[P36] state_heal_from_signals asset={asset} interval_sec={int(interval_sec)} day={day} inserted={inserted}")
        return inserted

    def executed_today_count(self, asset: str, interval_sec: int, day: str) -> int:
        rows = self.signals.fetch_trade_rows(asset, interval_sec, day)
        if rows:
            self.heal_state_from_signals(asset, interval_sec, day)
            return int(len(rows))
        return self.state.count_day(asset, interval_sec, day)

    def last_executed_ts(self, asset: str, interval_sec: int, day: str) -> int | None:
        rows = self.signals.fetch_trade_rows(asset, interval_sec, day)
        if rows:
            self.heal_state_from_signals(asset, interval_sec, day)
            return max(int(r["ts"] or 0) for r in rows)
        return self.state.last_ts(asset, interval_sec, day)

    def already_executed(self, asset: str, interval_sec: int, day: str, ts: int) -> bool:
        rows = self.signals.fetch_trade_rows(asset, interval_sec, day, ts=int(ts))
        if rows:
            self.heal_state_from_signals(asset, interval_sec, day, ts=int(ts))
            return True
        return self.state.exists(asset, interval_sec, day, int(ts))

    def mark_executed(self, asset: str, interval_sec: int, day: str, ts: int, action: str, conf: float, score: float) -> None:
        self.state.upsert_execution(asset, interval_sec, day, ts, action, conf, score)
