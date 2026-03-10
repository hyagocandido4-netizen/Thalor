from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, MutableMapping


def _env_truthy(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    val = env.get(key)
    if val is None:
        return bool(default)
    s = str(val).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    val = env.get(key)
    if val is None:
        return float(default)
    try:
        return float(val)
    except Exception:
        return float(default)


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


@dataclass(frozen=True)
class CpRegConfig:
    """CPREG schedule configuration.

    CPREG is a *time-of-day* + *slot-aware* regulator for CP_ALPHA.

    Environment variables supported (legacy-compatible):
      - CPREG_ENABLE: enable schedule (truthy)
      - CPREG_ALPHA_START: alpha at start of day
      - CPREG_ALPHA_END: alpha after ramp_end
      - CPREG_WARMUP_FRAC: fraction of day kept at alpha_start (default 0.08)
      - CPREG_RAMP_END_FRAC: fraction of day where ramp finishes (default 0.40)
      - CPREG_SLOT2_MULT: multiplier applied when slot>=2 (default 1.0)

    Notes:
      - When CPREG is disabled, runtime should fall back to CP_ALPHA.
      - clamp_min/clamp_max are safety rails for alpha.
    """

    enabled: bool
    alpha_start: float
    alpha_end: float
    warmup_frac: float
    ramp_end_frac: float
    slot2_mult: float
    clamp_min: float = 0.001
    clamp_max: float = 0.50


def cpreg_config_from_env(env: Mapping[str, str] | None = None) -> CpRegConfig:
    env = env or os.environ

    enabled = _env_truthy(env, "CPREG_ENABLE", False)
    alpha_start = _env_float(env, "CPREG_ALPHA_START", 0.06)
    alpha_end = _env_float(env, "CPREG_ALPHA_END", 0.09)

    warmup_frac = _env_float(env, "CPREG_WARMUP_FRAC", 0.50)
    ramp_end_frac = _env_float(env, "CPREG_RAMP_END_FRAC", 0.90)

    slot2_mult = _env_float(env, "CPREG_SLOT2_MULT", 0.85)

    # Basic hygiene.
    warmup_frac = _clamp(float(warmup_frac), 0.0, 1.0)
    ramp_end_frac = _clamp(float(ramp_end_frac), 0.0, 1.0)

    return CpRegConfig(
        enabled=bool(enabled),
        alpha_start=float(alpha_start),
        alpha_end=float(alpha_end),
        warmup_frac=float(warmup_frac),
        ramp_end_frac=float(ramp_end_frac),
        slot2_mult=float(slot2_mult),
    )


def compute_cp_alpha(dt_local: datetime, *, executed_today: int, cfg: CpRegConfig) -> float:
    """Compute CP_ALPHA for the given local datetime.

    executed_today is the number of trades already executed for the day.
    The next trade slot is executed_today+1.
    """

    # Time-of-day fraction.
    day_sec = int(dt_local.hour) * 3600 + int(dt_local.minute) * 60 + int(dt_local.second)
    frac_day = day_sec / 86400.0

    # Piecewise schedule.
    a0 = float(cfg.alpha_start)
    a1 = float(cfg.alpha_end)

    w = float(cfg.warmup_frac)
    r = float(cfg.ramp_end_frac)

    if r <= w:
        # Degenerate schedule: treat as constant alpha_end.
        alpha = a1
    elif frac_day <= w:
        alpha = a0
    elif frac_day >= r:
        alpha = a1
    else:
        t = (frac_day - w) / (r - w)
        alpha = a0 + t * (a1 - a0)

    # Slot-aware multiplier (slot >= 2).
    slot = int(executed_today) + 1
    if slot >= 2:
        alpha *= float(cfg.slot2_mult)

    return _clamp(float(alpha), float(cfg.clamp_min), float(cfg.clamp_max))


def compute_cp_alpha_from_env(dt_local: datetime, *, executed_today: int, env: Mapping[str, str] | None = None) -> float | None:
    cfg = cpreg_config_from_env(env)
    if not cfg.enabled:
        return None
    return compute_cp_alpha(dt_local, executed_today=executed_today, cfg=cfg)


def maybe_apply_cp_alpha_env(
    dt_local: datetime,
    *,
    executed_today: int,
    env: MutableMapping[str, str] | None = None,
) -> float | None:
    """If CPREG is enabled, compute CP_ALPHA and write it to env['CP_ALPHA'].

    Returns the applied alpha, or None when CPREG is disabled.
    """

    env = env or os.environ
    alpha = compute_cp_alpha_from_env(dt_local, executed_today=executed_today, env=env)
    if alpha is None:
        return None
    env["CP_ALPHA"] = f"{alpha:.4f}"
    return alpha
