from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.runtime_app import build_runtime_app_info, to_json_dict


def main() -> int:
    info = build_runtime_app_info()
    payload = to_json_dict(info)
    assert payload["config"]["interval_sec"] > 0
    assert payload["config"]["asset"]
    assert payload["scoped_paths"]["signals_db"].endswith("live_signals.sqlite3")
    assert payload["control_paths"]["plan"].endswith("plan.json")

    cmd = [sys.executable, "-m", "natbin.runtime_app", "--repo-root", str(ROOT), "--json"]
    proc = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
    cli_payload = json.loads(proc.stdout)
    assert cli_payload["config"]["asset"] == payload["config"]["asset"]
    assert cli_payload["health"]["asset"] == payload["config"]["asset"]
    assert "control_plane" in cli_payload["notes"]
    print("runtime_app_smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
