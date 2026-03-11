from __future__ import annotations

"""Effective-config dump helpers.

These helpers write the effective (resolved) configuration for a specific
scope into ``runs/config``.

This is intended for auditability: every cycle can record *exactly* which
settings were in effect when a decision was computed.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from .models import ResolvedConfig
from ..security.redaction import collect_sensitive_values, sanitize_payload


def _scope_tag(asset: str, interval_sec: int) -> str:
    safe = (
        str(asset)
        .replace("/", "_")
        .replace(":", "_")
        .replace(" ", "_")
    )
    return f"{safe}_{int(interval_sec)}s"


def effective_config_dir(*, out_dir: str | Path = "runs") -> Path:
    return Path(out_dir) / "config"


def effective_config_latest_path(*, asset: str, interval_sec: int, out_dir: str | Path = "runs") -> Path:
    tag = _scope_tag(asset, interval_sec)
    return effective_config_dir(out_dir=out_dir) / f"effective_config_latest_{tag}.json"


def effective_config_snapshot_path(
    *,
    day: str,
    asset: str,
    interval_sec: int,
    cycle_id: str,
    out_dir: str | Path = "runs",
) -> Path:
    tag = _scope_tag(asset, interval_sec)
    day_tag = str(day).replace("-", "")
    cid = str(cycle_id).replace(":", "").replace("-", "")
    return effective_config_dir(out_dir=out_dir) / f"effective_config_{day_tag}_{tag}_{cid}.json"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def write_effective_config_latest(
    cfg: ResolvedConfig,
    *,
    repo_root: str | Path = ".",
    out_dir: str | Path = "runs",
) -> Path:
    root = Path(repo_root).resolve()
    path = root / effective_config_latest_path(asset=cfg.asset, interval_sec=cfg.interval_sec, out_dir=out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = cfg.as_dict()
    if bool(getattr(cfg.security, 'redact_control_artifacts', True)):
        payload = sanitize_payload(
            payload,
            sensitive_values=collect_sensitive_values(payload, redact_email=bool(getattr(cfg.security, 'redact_email', True))),
            redact_email=bool(getattr(cfg.security, 'redact_email', True)),
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def write_effective_config_snapshot(
    cfg: ResolvedConfig,
    *,
    day: str,
    cycle_id: str | None = None,
    repo_root: str | Path = ".",
    out_dir: str | Path = "runs",
) -> Path:
    root = Path(repo_root).resolve()
    if cycle_id is None:
        cycle_id = _now_utc().strftime("%H%M%S")
    path = root / effective_config_snapshot_path(
        day=day,
        asset=cfg.asset,
        interval_sec=cfg.interval_sec,
        cycle_id=cycle_id,
        out_dir=out_dir,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = cfg.as_dict()
    if bool(getattr(cfg.security, 'redact_control_artifacts', True)):
        payload = sanitize_payload(
            payload,
            sensitive_values=collect_sensitive_values(payload, redact_email=bool(getattr(cfg.security, 'redact_email', True))),
            redact_email=bool(getattr(cfg.security, 'redact_email', True)),
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
