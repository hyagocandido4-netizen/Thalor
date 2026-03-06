from __future__ import annotations

"""Structured logging helpers (JSONL).

The project historically relied on transcript-style logs. Package P adds a
best-effort JSONL channel that can be ingested by log pipelines without regex.

This module is dependency-free and intentionally tolerant to runtime errors.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload2 = dict(payload)
        payload2.setdefault('at_utc', _utc_now_iso())
        line = json.dumps(payload2, ensure_ascii=False, sort_keys=False, default=str)
        with p.open('a', encoding='utf-8') as f:
            f.write(line)
            f.write('\n')
    except Exception:
        # Never crash runtime due to logging.
        return
