from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass(frozen=True)
class RuntimeAppConfig:
    asset: str
    interval_sec: int
    timezone: str
    dataset_path: str
    config_path: str


@dataclass(frozen=True)
class RuntimeAppCapabilities:
    runtime_cycle: bool
    runtime_daemon: bool
    runtime_quota: bool
    runtime_scope: bool
    runtime_repos: bool
    runtime_observability: bool


@dataclass(frozen=True)
class RuntimeAppInfo:
    config: RuntimeAppConfig
    capabilities: RuntimeAppCapabilities
    scoped_paths: Dict[str, str]
    notes: Dict[str, str]


def _load_yaml_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists() or yaml is None:
        return {}
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


def load_runtime_app_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> RuntimeAppConfig:
    path = Path(config_path)
    cfg = _load_yaml_config(path)
    data = cfg.get("data") or {}
    phase2 = cfg.get("phase2") or {}
    asset = str(data.get("asset") or os.getenv("ASSET") or "EURUSD-OTC")
    interval_sec = int(data.get("interval_sec") or os.getenv("INTERVAL_SEC") or 300)
    timezone = str(data.get("timezone") or os.getenv("TIMEZONE") or "America/Sao_Paulo")
    dataset_path = str(phase2.get("dataset_path") or "data/dataset_phase2.csv")
    return RuntimeAppConfig(
        asset=asset,
        interval_sec=interval_sec,
        timezone=timezone,
        dataset_path=dataset_path,
        config_path=str(path),
    )


def detect_capabilities() -> RuntimeAppCapabilities:
    def _has(mod_name: str) -> bool:
        try:
            __import__(mod_name)
            return True
        except Exception:
            return False

    return RuntimeAppCapabilities(
        runtime_cycle=_has("natbin.runtime_cycle"),
        runtime_daemon=_has("natbin.runtime_daemon"),
        runtime_quota=_has("natbin.runtime_quota"),
        runtime_scope=_has("natbin.runtime_scope"),
        runtime_repos=_has("natbin.runtime_repos"),
        runtime_observability=_has("natbin.runtime_observability"),
    )


def _sanitize_scope_part(value: str) -> str:
    out = []
    for ch in value:
        out.append(ch if (ch.isalnum() or ch in "-_") else "_")
    return "".join(out)


def derive_scoped_paths(config: RuntimeAppConfig) -> Dict[str, str]:
    asset_s = _sanitize_scope_part(config.asset)
    iv_s = f"{int(config.interval_sec)}s"
    base_runs = Path("runs")
    return {
        "effective_env": str(base_runs / f"effective_env_{asset_s}_{iv_s}.json"),
        "market_context": str(base_runs / f"market_context_{asset_s}_{iv_s}.json"),
        "status": str(base_runs / f"observe_loop_auto_status_{asset_s}_{iv_s}.json"),
        "signals_db": str(base_runs / "live_signals.sqlite3"),
        "state_db": str(base_runs / "live_topk_state.sqlite3"),
        "log_dir": str(base_runs / "logs"),
        "decision_dir": str(base_runs / "decisions"),
        "incidents_dir": str(base_runs / "incidents"),
    }


def build_runtime_app_info(config_path: str | Path = DEFAULT_CONFIG_PATH) -> RuntimeAppInfo:
    config = load_runtime_app_config(config_path)
    capabilities = detect_capabilities()
    scoped_paths = derive_scoped_paths(config)
    notes = {
        "design": "Package L introduces a Python app-shell view of the runtime without changing the live execution path.",
        "commit_recommendation": "After Package L is green locally and the bot still loops cleanly, create a milestone commit/tag before Package M.",
        "scheduler_status": "PowerShell remains the primary operational entrypoint; the Python daemon/app shell is additive.",
    }
    return RuntimeAppInfo(config=config, capabilities=capabilities, scoped_paths=scoped_paths, notes=notes)


def to_json_dict(info: RuntimeAppInfo) -> Dict[str, Any]:
    return {
        "config": asdict(info.config),
        "capabilities": asdict(info.capabilities),
        "scoped_paths": dict(info.scoped_paths),
        "notes": dict(info.notes),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Describe the Thalor runtime app-shell state.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a text summary")
    args = parser.parse_args(argv)

    info = build_runtime_app_info(args.config)
    if args.json:
        print(json.dumps(to_json_dict(info), indent=2, ensure_ascii=False))
    else:
        print(f"asset={info.config.asset} interval_sec={info.config.interval_sec} timezone={info.config.timezone}")
        print(f"dataset_path={info.config.dataset_path}")
        print("capabilities=" + ", ".join([k for k, v in asdict(info.capabilities).items() if v]))
        for key, value in info.scoped_paths.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
