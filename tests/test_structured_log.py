from __future__ import annotations

import json
from pathlib import Path

from natbin.ops.structured_log import append_jsonl


def test_append_jsonl(tmp_path: Path):
    p = tmp_path / "a" / "b" / "log.jsonl"
    append_jsonl(p, {"x": 1, "y": "z"})
    assert p.exists()

    line = p.read_text(encoding="utf-8").strip()
    assert json.loads(line) == {"x": 1, "y": "z"}
