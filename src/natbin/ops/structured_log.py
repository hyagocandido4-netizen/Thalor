"""Structured logging helpers.

This module is intentionally tiny and dependency-free:
- append_jsonl(): appends one JSON object per line, creating parent dirs.

Design goals:
- Do not mutate the payload.
- Do not inject extra fields by default (tests rely on exact content).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_jsonl(path: str | Path, obj: Any, *, add_at_utc: bool = False) -> None:
    """Append *obj* as a single JSON line to *path*.

    The parent directory is created automatically.

    Notes
    -----
    * The function does **not** mutate *obj*.
    * By default it does **not** inject timestamps.
    * Raises any JSON encoding / I/O errors to the caller.
    """

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Ensure we don't accidentally mutate a dict passed by reference.
    payload = obj
    if isinstance(obj, dict):
        payload = dict(obj)
        if add_at_utc and 'at_utc' not in payload:
            # Local import to keep this module dependency-light.
            try:
                from ..util.clock import utc_now_iso  # type: ignore

                payload['at_utc'] = utc_now_iso()
            except Exception:
                pass

    line = json.dumps(payload, ensure_ascii=False)
    with p.open('a', encoding='utf-8') as f:
        f.write(line)
        f.write('\n')
