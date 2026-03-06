from __future__ import annotations

"""Settings sources for :class:`natbin.config.models.ThalorConfig`.

We support two YAML layouts:

* Legacy: repo root ``config.yaml`` with keys like ``data.asset`` and
  ``best.threshold``.
* Modern: ``config/base.yaml`` style (future packages), matching the model
  shape directly.

We also support *compat* env vars for existing deployments:
* IQ_EMAIL, IQ_PASSWORD, IQ_BALANCE_MODE
* ASSET, INTERVAL_SEC, TIMEZONE

Modern env vars should use THALOR__ prefix.
"""

import os
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

try:
    from dotenv import dotenv_values  # type: ignore
except Exception:  # pragma: no cover
    dotenv_values = None


def _read_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        return {}
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _normalize_gate_mode(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    # Historic values used in repo config.yaml
    if s in {"cp", "cp_meta", "cp_meta_iso"}:
        return "cp_meta_iso"
    if s in {"meta", "meta_iso"}:
        return "meta_iso"
    return s


def legacy_yaml_to_model_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert legacy config.yaml dict into the ThalorConfig model shape."""
    out: dict[str, Any] = {}
    data = raw.get("data") or {}
    phase2 = raw.get("phase2") or {}
    best = raw.get("best") or {}

    asset = data.get("asset")
    interval_sec = data.get("interval_sec")
    timezone = data.get("timezone")
    db_path = data.get("db_path")
    dataset_path = phase2.get("dataset_path")

    assets = []
    if asset is not None:
        assets.append(
            {
                "asset": str(asset),
                "interval_sec": int(interval_sec or 300),
                "timezone": str(timezone or "America/Sao_Paulo"),
                "topk_k": int(best.get("k") or 3),
            }
        )
    if assets:
        out["assets"] = assets

    out["data"] = {}
    if db_path is not None:
        out["data"]["db_path"] = str(db_path)
    if dataset_path is not None:
        out["data"]["dataset_path"] = str(dataset_path)
    if data.get("max_batch") is not None:
        out["data"]["max_batch"] = int(data.get("max_batch"))
    if data.get("interval_sec") is not None:
        out["data"]["lookback_candles"] = 2000

    # Decision defaults from legacy "best" block
    dec: dict[str, Any] = {}
    if best.get("threshold") is not None:
        dec["threshold"] = float(best.get("threshold"))
    if best.get("thresh_on") is not None:
        dec["thresh_on"] = str(best.get("thresh_on"))
    gm = _normalize_gate_mode(best.get("gate_mode"))
    if gm is not None:
        dec["gate_mode"] = gm
    if best.get("meta_model") is not None:
        dec["meta_model"] = str(best.get("meta_model"))
    if best.get("k") is not None:
        dec.setdefault("rolling_minutes", 360)
    if dec:
        out["decision"] = dec
    return out


def yaml_source_from_settings(config_path: Path) -> dict[str, Any]:
    """Read config from YAML (legacy or modern)."""
    raw = _read_yaml(config_path)
    if not raw:
        return {}
    # Modern layout (future) is expected to have top-level "assets" or "runtime".
    if "assets" in raw or "runtime" in raw or "broker" in raw:
        return raw
    # Legacy layout.
    return legacy_yaml_to_model_dict(raw)


def _read_dotenv_values(env_path: Path | None = None) -> dict[str, str]:
    if dotenv_values is None:
        return {}
    path = Path(".env") if env_path is None else Path(env_path)
    try:
        raw = dotenv_values(path)
    except Exception:
        raw = {}
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if key is None or value is None:
            continue
        out[str(key)] = str(value)
    return out


def modern_dotenv_env_map(env_path: Path | None = None) -> dict[str, str]:
    vals = _read_dotenv_values(env_path)
    return {str(k): str(v) for k, v in vals.items() if str(k).upper().startswith("THALOR__")}


def _compat_env_values(env_path: Path | None = None) -> dict[str, str]:
    # Combine process env + .env (if present) without overriding explicit process env.
    env = dict(os.environ)
    for key, value in _read_dotenv_values(env_path).items():
        if key not in env:
            env[key] = value
    return env


def compat_env_source(env_path: Path | None = None) -> dict[str, Any]:
    """Compatibility source for historical env vars (.env / IQ_* etc.)."""
    env = _compat_env_values(env_path)
    out: dict[str, Any] = {}

    # Broker legacy keys.
    broker: dict[str, Any] = {}
    if env.get("IQ_EMAIL"):
        broker["email"] = str(env.get("IQ_EMAIL"))
    if env.get("IQ_PASSWORD"):
        broker["password"] = str(env.get("IQ_PASSWORD"))
    if env.get("IQ_BALANCE_MODE"):
        broker["balance_mode"] = str(env.get("IQ_BALANCE_MODE")).upper()
    if broker:
        out["broker"] = broker

    # Scope legacy keys.
    asset = env.get("ASSET")
    interval = env.get("INTERVAL_SEC")
    tz = env.get("TIMEZONE")
    if asset or interval or tz:
        out.setdefault("assets", [])
        out["assets"].append(
            {
                "asset": str(asset or "EURUSD-OTC"),
                "interval_sec": int(float(str(interval or 300).replace(",", "."))),
                "timezone": str(tz or "America/Sao_Paulo"),
            }
        )

    return out


def build_source_trace(*, config_path: Path, env_path: Path | None = None) -> list[str]:
    trace: list[str] = []
    if config_path.exists():
        trace.append(f"yaml:{config_path.as_posix()}")
    # Compat keys
    env = _compat_env_values(env_path)
    if env.get("IQ_EMAIL") or env.get("IQ_PASSWORD"):
        trace.append("compat_env:IQ_*")
    if env.get("ASSET") or env.get("INTERVAL_SEC") or env.get("TIMEZONE"):
        trace.append("compat_env:scope")
    # Modern keys
    if any(k.startswith("THALOR__") for k in env.keys()):
        trace.append("env:THALOR__*")
    if (env_path or Path(".env")).exists():
        trace.append(f"dotenv:{(env_path or Path('.env')).as_posix()}")
    return trace
