from __future__ import annotations

"""Settings sources for :class:`natbin.config.models.ThalorConfig`.

We support two YAML layouts:

* Legacy: repo root ``config.yaml`` with keys like ``data.asset`` and
  ``best.threshold``.
* Modern: ``config/base.yaml`` style (upcoming packages), matching the model
  shape directly.

We also support *compat* env vars for existing deployments:
* IQ_EMAIL, IQ_PASSWORD, IQ_BALANCE_MODE
* ASSET, INTERVAL_SEC, TIMEZONE

Modern env vars should use THALOR__ prefix.
"""

import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

try:
    from dotenv import dotenv_values  # type: ignore
except Exception:  # pragma: no cover
    dotenv_values = None


@dataclass(frozen=True)
class ResolvedYamlConfig:
    data: dict[str, Any]
    source_paths: tuple[Path, ...] = ()


# Repo-local `.env` should remain useful for secrets / transport / deployment
# posture, but it should *not* silently change trading behaviour unless the
# operator explicitly opts into that legacy mode.
_DOTENV_BEHAVIOR_BLOCKED_ROOTS = frozenset(
    {
        "assets",
        "autos",
        "decision",
        "execution",
        "failsafe",
        "intelligence",
        "multi_asset",
        "quota",
        "runtime",
        "runtime_overrides",
    }
)


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
    """Normalize gate_mode into the *requested* mode used by scoring.

    Historic configs sometimes used composite labels like ``cp_meta_iso`` or
    ``meta_iso``. The scorer expects a stable small set: ``cp``, ``meta``,
    ``iso`` or ``conf``.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if s in {"cp", "cp_meta", "cp_meta_iso", "cp-meta-iso"}:
        return "cp"
    if s in {"meta", "meta_iso", "meta-iso"}:
        return "meta"
    if s == "iso":
        return "iso"
    if s == "conf":
        return "conf"
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
    if best.get("cp_alpha") is not None:
        dec["cp_alpha"] = float(best.get("cp_alpha"))

    cpreg: dict[str, Any] = {}
    if best.get("cpreg_enable") is not None:
        cpreg["enabled"] = bool(best.get("cpreg_enable"))
    if best.get("cpreg_alpha_start") is not None:
        cpreg["alpha_start"] = float(best.get("cpreg_alpha_start"))
    if best.get("cpreg_alpha_end") is not None:
        cpreg["alpha_end"] = float(best.get("cpreg_alpha_end"))
    if best.get("cpreg_warmup_frac") is not None:
        cpreg["warmup_frac"] = float(best.get("cpreg_warmup_frac"))
    if best.get("cpreg_ramp_end_frac") is not None:
        cpreg["ramp_end_frac"] = float(best.get("cpreg_ramp_end_frac"))
    if best.get("cpreg_slot2_mult") is not None:
        cpreg["slot2_mult"] = float(best.get("cpreg_slot2_mult"))
    if cpreg:
        dec["cpreg"] = cpreg

    # Optional tuning pointer / bounds (used by the legacy observer runtime).
    if best.get("tune_dir") is not None:
        dec["tune_dir"] = str(best.get("tune_dir"))
    if isinstance(best.get("bounds"), dict):
        dec["bounds"] = dict(best.get("bounds") or {})

    if best.get("k") is not None:
        dec.setdefault("rolling_minutes", 360)
    if dec:
        out["decision"] = dec
    return out



def _is_modern_yaml_dict(raw: Mapping[str, Any]) -> bool:
    strong_modern_markers = {
        "assets",
        "runtime",
        "broker",
        "decision",
        "quota",
        "autos",
        "observability",
        "dashboard",
        "network",
        "monte_carlo",
        "production",
        "failsafe",
        "multi_asset",
        "intelligence",
        "execution",
        "security",
        "notifications",
        "runtime_overrides",
    }
    if any(str(key) in strong_modern_markers for key in raw.keys()):
        return True
    if any(str(key) in {"phase2", "best"} for key in raw.keys()):
        return False
    data = raw.get("data")
    if isinstance(data, Mapping):
        legacy_data_markers = {"asset", "interval_sec", "timezone"}
        if any(str(key) in legacy_data_markers for key in data.keys()):
            return False
        return True
    return False



def _normalize_yaml_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(raw)
    if _is_modern_yaml_dict(payload):
        return payload
    return legacy_yaml_to_model_dict(payload)



def _deep_merge_model_dicts(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = deepcopy(dict(base))
    for key, value in dict(override).items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge_model_dicts(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged



def _normalize_extends_entries(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (str, Path)):
        return [str(raw)]
    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        out: list[str] = []
        for item in raw:
            if isinstance(item, (str, Path)) and str(item).strip():
                out.append(str(item))
                continue
            raise TypeError("config extends entries must be strings or paths")
        return out
    raise TypeError("config extends must be a path or a list of paths")



def _dedupe_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    ordered: list[Path] = []
    for item in paths:
        path = Path(item).resolve()
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return tuple(ordered)



def _load_yaml_config_recursive(
    config_path: Path,
    *,
    stack: tuple[Path, ...] = (),
    required: bool,
) -> ResolvedYamlConfig:
    path = Path(config_path).resolve()
    if path in stack:
        cycle = " -> ".join([*(p.name for p in stack), path.name])
        raise ValueError(f"config extends cycle detected: {cycle}")
    if not path.exists():
        if required:
            raise FileNotFoundError(f"extended config not found: {path}")
        return ResolvedYamlConfig(data={}, source_paths=())

    raw = _read_yaml(path)
    if not raw:
        return ResolvedYamlConfig(data={}, source_paths=(path,))

    payload = dict(raw)
    extends_entries = _normalize_extends_entries(payload.pop("extends", None))

    merged: dict[str, Any] = {}
    source_paths: list[Path] = []
    for entry in extends_entries:
        parent_path = Path(entry)
        if not parent_path.is_absolute():
            parent_path = (path.parent / parent_path).resolve()
        parent = _load_yaml_config_recursive(parent_path, stack=(*stack, path), required=True)
        merged = _deep_merge_model_dicts(merged, parent.data)
        source_paths.extend(parent.source_paths)

    merged = _deep_merge_model_dicts(merged, _normalize_yaml_payload(payload))
    source_paths.append(path)
    return ResolvedYamlConfig(data=merged, source_paths=_dedupe_paths(source_paths))



def resolve_yaml_config_source(config_path: Path) -> ResolvedYamlConfig:
    """Read config from YAML, resolving optional `extends` chains first.

    Parent configs are merged recursively using deep-merge semantics for nested
    mappings; scalar values and lists are replaced by the child config.
    """

    return _load_yaml_config_recursive(Path(config_path), required=False)



def yaml_source_from_settings(config_path: Path) -> dict[str, Any]:
    """Read config from YAML (legacy or modern)."""

    return resolve_yaml_config_source(config_path).data



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



def _truthy_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}



def dotenv_allows_behavior() -> bool:
    return _truthy_flag(os.getenv("THALOR_DOTENV_ALLOW_BEHAVIOR"))



def _modern_env_key_segments(key: str) -> tuple[str, ...]:
    parts = [str(part).strip() for part in str(key).split("__") if str(part).strip()]
    if len(parts) < 2 or parts[0].upper() != "THALOR":
        return ()
    return tuple(part.lower() for part in parts[1:])



def _dotenv_key_is_behavioral(key: str) -> bool:
    segments = _modern_env_key_segments(key)
    if not segments:
        return False
    return segments[0] in _DOTENV_BEHAVIOR_BLOCKED_ROOTS



def modern_dotenv_env_map(
    env_path: Path | None = None,
    *,
    allow_behavior: bool | None = None,
) -> dict[str, str]:
    vals = _read_dotenv_values(env_path)
    allow = dotenv_allows_behavior() if allow_behavior is None else bool(allow_behavior)
    out: dict[str, str] = {}
    for key, value in vals.items():
        name = str(key)
        if not name.upper().startswith("THALOR__"):
            continue
        if not allow and _dotenv_key_is_behavioral(name):
            continue
        out[name] = str(value)
    return out



def _compat_env_values(env_path: Path | None = None) -> dict[str, str]:
    # Combine process env + .env (if present) without overriding explicit process env.
    env = dict(os.environ)
    for key, value in _read_dotenv_values(env_path).items():
        if key not in env:
            env[key] = value
    return env



def _compat_source_from_mapping(env: Mapping[str, str | None]) -> dict[str, Any]:
    out: dict[str, Any] = {}

    broker: dict[str, Any] = {}
    if env.get('IQ_EMAIL'):
        broker['email'] = str(env.get('IQ_EMAIL'))
    if env.get('IQ_PASSWORD'):
        broker['password'] = str(env.get('IQ_PASSWORD'))
    if env.get('IQ_BALANCE_MODE'):
        broker['balance_mode'] = str(env.get('IQ_BALANCE_MODE')).upper()
    if broker:
        out['broker'] = broker

    asset = env.get('ASSET')
    interval = env.get('INTERVAL_SEC')
    tz = env.get('TIMEZONE')
    if asset or interval or tz:
        out.setdefault('assets', [])
        out['assets'].append(
            {
                'asset': str(asset or 'EURUSD-OTC'),
                'interval_sec': int(float(str(interval or 300).replace(',', '.'))),
                'timezone': str(tz or 'America/Sao_Paulo'),
            }
        )

    transport: dict[str, Any] = {}
    if env.get('TRANSPORT_ENABLED') is not None:
        transport['enabled'] = str(env.get('TRANSPORT_ENABLED'))
    if env.get('TRANSPORT_ENDPOINT'):
        transport['endpoint'] = str(env.get('TRANSPORT_ENDPOINT'))
    if env.get('TRANSPORT_ENDPOINTS'):
        transport['endpoints'] = str(env.get('TRANSPORT_ENDPOINTS'))
    if env.get('TRANSPORT_ENDPOINT_FILE'):
        transport['endpoint_file'] = str(env.get('TRANSPORT_ENDPOINT_FILE'))
    if env.get('TRANSPORT_ENDPOINTS_FILE'):
        transport['endpoints_file'] = str(env.get('TRANSPORT_ENDPOINTS_FILE'))
    if env.get('TRANSPORT_NO_PROXY'):
        transport['no_proxy'] = str(env.get('TRANSPORT_NO_PROXY'))
    if env.get('TRANSPORT_STRUCTURED_LOG_PATH'):
        transport['structured_log_path'] = str(env.get('TRANSPORT_STRUCTURED_LOG_PATH'))
    if transport:
        out.setdefault('network', {})
        out['network']['transport'] = transport

    request_metrics: dict[str, Any] = {}
    if env.get('REQUEST_METRICS_ENABLED') is not None:
        request_metrics['enabled'] = str(env.get('REQUEST_METRICS_ENABLED'))
    if env.get('REQUEST_METRICS_LOG_PATH'):
        request_metrics['structured_log_path'] = str(env.get('REQUEST_METRICS_LOG_PATH'))
    if env.get('REQUEST_METRICS_STRUCTURED_LOG_PATH'):
        request_metrics['structured_log_path'] = str(env.get('REQUEST_METRICS_STRUCTURED_LOG_PATH'))
    if env.get('REQUEST_METRICS_TIMEZONE'):
        request_metrics['timezone'] = str(env.get('REQUEST_METRICS_TIMEZONE'))
    if env.get('REQUEST_METRICS_EMIT_REQUEST_EVENTS') is not None:
        request_metrics['emit_request_events'] = str(env.get('REQUEST_METRICS_EMIT_REQUEST_EVENTS'))
    if env.get('REQUEST_METRICS_EMIT_SUMMARY_EVERY_REQUESTS') is not None:
        request_metrics['emit_summary_every_requests'] = str(env.get('REQUEST_METRICS_EMIT_SUMMARY_EVERY_REQUESTS'))
    if request_metrics:
        out.setdefault('observability', {})
        out['observability']['request_metrics'] = request_metrics
    return out



def compat_process_env_source() -> dict[str, Any]:
    """Compatibility source backed only by the real process environment."""

    return _compat_source_from_mapping(dict(os.environ))



def compat_dotenv_source(env_path: Path | None = None) -> dict[str, Any]:
    """Compatibility source backed only by the repo `.env` file."""

    return _compat_source_from_mapping(_read_dotenv_values(env_path))



def compat_env_source(env_path: Path | None = None) -> dict[str, Any]:
    """Compatibility source for historical env vars (.env / IQ_* etc.)."""

    return _compat_source_from_mapping(_compat_env_values(env_path))



def build_source_trace(
    *,
    config_path: Path,
    env_path: Path | None = None,
    config_paths: Sequence[Path] | None = None,
) -> list[str]:
    trace: list[str] = []
    yaml_paths = config_paths if config_paths is not None else [config_path]
    for path in _dedupe_paths(yaml_paths):
        if path.exists():
            trace.append(f"yaml:{path.as_posix()}")
    # Compat keys
    env = _compat_env_values(env_path)
    if env.get("IQ_EMAIL") or env.get("IQ_PASSWORD"):
        trace.append("compat_env:IQ_*")
    if env.get("ASSET") or env.get("INTERVAL_SEC") or env.get("TIMEZONE"):
        trace.append("compat_env:scope")
    if any(env.get(key) for key in ("TRANSPORT_ENABLED", "TRANSPORT_ENDPOINT", "TRANSPORT_ENDPOINTS", "TRANSPORT_ENDPOINT_FILE", "TRANSPORT_ENDPOINTS_FILE", "TRANSPORT_NO_PROXY", "TRANSPORT_STRUCTURED_LOG_PATH")):
        trace.append("compat_env:transport")
    if any(env.get(key) for key in ("REQUEST_METRICS_ENABLED", "REQUEST_METRICS_LOG_PATH", "REQUEST_METRICS_STRUCTURED_LOG_PATH", "REQUEST_METRICS_TIMEZONE")):
        trace.append("compat_env:request_metrics")
    # Modern keys
    process_modern = any(str(k).startswith("THALOR__") for k in os.environ.keys())
    dotenv_modern = modern_dotenv_env_map(env_path=env_path)
    if process_modern or dotenv_modern:
        trace.append("env:THALOR__*")
    if (env_path or Path(".env")).exists():
        trace.append(f"dotenv:{(env_path or Path('.env')).as_posix()}")
    return trace
