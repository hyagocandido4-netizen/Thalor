from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..state.control_repo import RuntimeControlRepository
from .failsafe import RuntimeFailsafe, FailsafeSnapshot, CircuitBreakerSnapshot


@dataclass(frozen=True)
class PrecheckDecision:
    blocked: bool
    reason: str | None
    snapshot: FailsafeSnapshot | None
    breaker: CircuitBreakerSnapshot | None = None
    next_wake_utc: str | None = None


def _market_open_from_context(market_context: dict[str, Any] | None) -> bool | None:
    if not market_context:
        return None
    v = market_context.get("market_open")
    if isinstance(v, str):
        vv = v.strip().lower()
        if vv in {"1", "true", "yes", "y", "open"}:
            return True
        if vv in {"0", "false", "no", "n", "closed"}:
            return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return None


def _market_stale_from_context(market_context: dict[str, Any] | None) -> bool:
    if not market_context:
        return True
    v = market_context.get("stale")
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y"}
    return bool(v)


def run_precheck(
    failsafe: RuntimeFailsafe,
    *,
    asset: str,
    interval_sec: int,
    control_repo: RuntimeControlRepository,
    market_context: dict[str, Any] | None,
    quota_hard_block: bool = False,
    quota_reason: str | None = None,
    env: dict[str, str] | None = None,
    now_utc: datetime | None = None,
    enforce_market_context: bool = True,
) -> PrecheckDecision:
    """Bridge the M4 failsafe kernel into runtime prechecks.

    This function is intentionally additive and API-compatible with the actual
    M4 `RuntimeFailsafe` implementation. It loads the breaker state from the
    control repository, evaluates the failsafe precheck, and persists breaker
    state if it changed during the evaluation (for example, OPEN -> HALF_OPEN).
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    breaker = control_repo.load_breaker(asset, int(interval_sec))
    before = breaker.as_dict()

    market_open = None
    market_context_stale = False
    if enforce_market_context:
        market_open = _market_open_from_context(market_context)
        market_context_stale = _market_stale_from_context(market_context)

    snap = failsafe.precheck(
        now_utc=now_utc,
        breaker=breaker,
        market_open=market_open,
        market_context_stale=market_context_stale,
        quota_hard_block=bool(quota_hard_block),
        quota_reason=quota_reason,
        env=env or {},
    )

    if breaker.as_dict() != before:
        control_repo.save_breaker(breaker)

    return PrecheckDecision(
        blocked=not snap.ready_to_trade,
        reason=snap.blocked_reason,
        snapshot=snap,
        breaker=breaker,
        next_wake_utc=None,
    )
