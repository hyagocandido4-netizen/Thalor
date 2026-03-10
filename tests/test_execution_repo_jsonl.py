from __future__ import annotations

import json
from pathlib import Path

from natbin.state.execution_repo import ExecutionRepository


def test_execution_repo_writes_jsonl(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir(parents=True, exist_ok=True)

    db_path = runs / "runtime_execution.sqlite3"
    repo = ExecutionRepository(db_path)

    # Event payload can be any dict that is JSON-serializable.
    repo.add_event(
        event_id="evt_001",
        event_type="unit_test_event",
        created_at_utc="2026-03-09T00:00:00Z",
        intent_id="intent_123",
        broker_name="paper",
        account_mode="paper",
        external_order_id="ext_abc",
        payload={"hello": "world"},
    )

    log_path = runs / "logs" / "execution_events.jsonl"
    assert log_path.exists(), f"expected JSONL log at {log_path}"

    last = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    obj = json.loads(last)

    assert obj["event_id"] == "evt_001"
    assert obj["intent_id"] == "intent_123"
    assert obj["event_type"] == "unit_test_event"
    assert obj["payload"]["hello"] == "world"
