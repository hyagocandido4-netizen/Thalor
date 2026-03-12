from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    cfg2 = importlib.import_module("natbin.config2")
    settings = importlib.import_module("natbin.settings")
    cfg_legacy = importlib.import_module("natbin.config.legacy")
    cfg_settings = importlib.import_module("natbin.config.settings")

    cfg = cfg2.load_cfg()
    sc = cfg2.scope()
    env = cfg2.export_env()

    assert isinstance(cfg, dict), "config2.load_cfg must return dict"
    assert cfg["asset"] == sc.asset, "scope asset mismatch"
    assert int(cfg["interval_sec"]) == int(sc.interval_sec), "scope interval mismatch"
    assert str(cfg["timezone"]) == str(sc.timezone), "scope timezone mismatch"

    assert settings.ASSET == cfg["asset"], "settings asset mismatch"
    assert int(settings.INTERVAL_SEC) == int(cfg["interval_sec"]), "settings interval mismatch"
    assert str(settings.TIMEZONE) == str(cfg["timezone"]), "settings timezone mismatch"

    assert env["ASSET"] == cfg["asset"], "env export asset mismatch"
    assert int(env["INTERVAL_SEC"]) == int(cfg["interval_sec"]), "env export interval mismatch"

    legacy_cfg = cfg_legacy.load_cfg()
    assert legacy_cfg["asset"] == cfg["asset"], "legacy config asset mismatch"
    assert int(legacy_cfg["interval_sec"]) == int(cfg["interval_sec"]), "legacy config interval mismatch"
    assert cfg_settings.ASSET == cfg["asset"], "config.settings asset mismatch"
    assert int(cfg_settings.INTERVAL_SEC) == int(cfg["interval_sec"]), "config.settings interval mismatch"

    print("[smoke][OK] config2/settings/legacy bridges ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
