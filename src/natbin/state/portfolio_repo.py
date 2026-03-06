from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..runtime_perf import apply_runtime_sqlite_pragmas


@dataclass(frozen=True)
class PortfolioCycleRow:
    cycle_id: str
    started_at_utc: str
    finished_at_utc: str
    ok: int
    message: str
    payload_json: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        try:
            data['payload'] = json.loads(self.payload_json)
        except Exception:
            data['payload'] = None
        return data


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS portfolio_cycles ("
    "cycle_id TEXT PRIMARY KEY,"
    "started_at_utc TEXT NOT NULL,"
    "finished_at_utc TEXT NOT NULL,"
    "ok INTEGER NOT NULL,"
    "message TEXT NOT NULL,"
    "payload_json TEXT NOT NULL"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_cycles_started ON portfolio_cycles(started_at_utc)",
)


class PortfolioRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path))
        apply_runtime_sqlite_pragmas(con)
        return con

    def _ensure(self) -> None:
        con = self._connect()
        try:
            for sql in SCHEMA:
                con.execute(sql)
            con.commit()
        finally:
            con.close()

    def save_cycle(self, payload: dict[str, Any]) -> None:
        cycle_id = str(payload.get('cycle_id') or '')
        if not cycle_id:
            return
        started = str(payload.get('started_at_utc') or '')
        finished = str(payload.get('finished_at_utc') or '')
        ok = 1 if bool(payload.get('ok')) else 0
        msg = str(payload.get('message') or '')
        raw = json.dumps(payload, ensure_ascii=False, default=str)

        con = self._connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO portfolio_cycles(cycle_id, started_at_utc, finished_at_utc, ok, message, payload_json) VALUES(?,?,?,?,?,?)",
                (cycle_id, started, finished, int(ok), msg, raw),
            )
            con.commit()
        finally:
            con.close()

    def latest(self) -> PortfolioCycleRow | None:
        con = self._connect()
        try:
            cur = con.execute(
                "SELECT cycle_id, started_at_utc, finished_at_utc, ok, message, payload_json FROM portfolio_cycles ORDER BY started_at_utc DESC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                return None
            return PortfolioCycleRow(
                cycle_id=str(row[0]),
                started_at_utc=str(row[1]),
                finished_at_utc=str(row[2]),
                ok=int(row[3] or 0),
                message=str(row[4] or ''),
                payload_json=str(row[5] or '{}'),
            )
        finally:
            con.close()
