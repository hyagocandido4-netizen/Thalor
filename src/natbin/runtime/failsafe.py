from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

CircuitState = Literal["closed", "open", "half_open"]


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    failures_to_open: int = 3
    cooldown_minutes: int = 15
    half_open_trials: int = 1


@dataclass
class CircuitBreakerSnapshot:
    asset: str
    interval_sec: int
    state: CircuitState = "closed"
    failures: int = 0
    last_failure_utc: Optional[datetime] = None
    opened_until_utc: Optional[datetime] = None
    half_open_trials_used: int = 0
    reason: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "asset": self.asset,
            "interval_sec": self.interval_sec,
            "state": self.state,
            "failures": self.failures,
            "last_failure_utc": self.last_failure_utc.isoformat() if self.last_failure_utc else None,
            "opened_until_utc": self.opened_until_utc.isoformat() if self.opened_until_utc else None,
            "half_open_trials_used": self.half_open_trials_used,
            "reason": self.reason,
        }


@dataclass
class FailsafeSnapshot:
    global_fail_closed: bool
    kill_switch_active: bool
    kill_switch_reason: Optional[str]
    drain_mode_active: bool
    drain_mode_reason: Optional[str]
    circuit_state: CircuitState
    circuit_reason: Optional[str]
    quota_hard_block: bool
    quota_reason: Optional[str]
    market_context_ok: bool
    market_context_reason: Optional[str]
    ready_to_trade: bool
    blocked_reason: Optional[str]

    def as_dict(self) -> dict:
        return {
            "global_fail_closed": self.global_fail_closed,
            "kill_switch_active": self.kill_switch_active,
            "kill_switch_reason": self.kill_switch_reason,
            "drain_mode_active": self.drain_mode_active,
            "drain_mode_reason": self.drain_mode_reason,
            "circuit_state": self.circuit_state,
            "circuit_reason": self.circuit_reason,
            "quota_hard_block": self.quota_hard_block,
            "quota_reason": self.quota_reason,
            "market_context_ok": self.market_context_ok,
            "market_context_reason": self.market_context_reason,
            "ready_to_trade": self.ready_to_trade,
            "blocked_reason": self.blocked_reason,
        }


@dataclass
class RuntimeFailsafe:
    kill_switch_file: Path = Path("runs/KILL_SWITCH")
    kill_switch_env_var: str = "THALOR_KILL_SWITCH"
    drain_mode_file: Path = Path("runs/DRAIN_MODE")
    drain_mode_env_var: str = "THALOR_DRAIN_MODE"
    global_fail_closed: bool = True
    market_context_fail_closed: bool = True
    policy: CircuitBreakerPolicy = field(default_factory=CircuitBreakerPolicy)

    def is_kill_switch_active(self, env: dict[str, str] | None = None) -> tuple[bool, Optional[str]]:
        env = env or {}
        if str(env.get(self.kill_switch_env_var, "")).strip().lower() in {"1", "true", "yes"}:
            return True, f"env:{self.kill_switch_env_var}"
        if self.kill_switch_file.exists():
            return True, f"file:{self.kill_switch_file}"
        return False, None

    def is_drain_mode_active(self, env: dict[str, str] | None = None) -> tuple[bool, Optional[str]]:
        env = env or {}
        if str(env.get(self.drain_mode_env_var, "")).strip().lower() in {"1", "true", "yes"}:
            return True, f"env:{self.drain_mode_env_var}"
        if self.drain_mode_file.exists():
            return True, f"file:{self.drain_mode_file}"
        return False, None

    def evaluate_circuit(self, snap: CircuitBreakerSnapshot, now_utc: datetime) -> CircuitBreakerSnapshot:
        if snap.state == "open" and snap.opened_until_utc and now_utc >= snap.opened_until_utc:
            snap.state = "half_open"
            snap.half_open_trials_used = 0
        return snap

    def record_failure(self, snap: CircuitBreakerSnapshot, reason: str, now_utc: datetime) -> CircuitBreakerSnapshot:
        snap.failures += 1
        snap.last_failure_utc = now_utc
        snap.reason = reason
        if snap.state in ("closed", "half_open") and snap.failures >= self.policy.failures_to_open:
            snap.state = "open"
            snap.opened_until_utc = now_utc + timedelta(minutes=self.policy.cooldown_minutes)
            snap.half_open_trials_used = 0
        return snap

    def record_success(self, snap: CircuitBreakerSnapshot) -> CircuitBreakerSnapshot:
        snap.failures = 0
        snap.reason = None
        snap.last_failure_utc = None
        snap.opened_until_utc = None
        snap.half_open_trials_used = 0
        snap.state = "closed"
        return snap

    def allow_half_open_trial(self, snap: CircuitBreakerSnapshot) -> bool:
        if snap.state != "half_open":
            return True
        if snap.half_open_trials_used < self.policy.half_open_trials:
            snap.half_open_trials_used += 1
            return True
        return False

    def precheck(
        self,
        *,
        now_utc: datetime,
        breaker: CircuitBreakerSnapshot,
        market_open: bool | None,
        market_context_stale: bool,
        quota_hard_block: bool,
        quota_reason: str | None,
        env: dict[str, str] | None = None,
    ) -> FailsafeSnapshot:
        breaker = self.evaluate_circuit(breaker, now_utc)
        kill_active, kill_reason = self.is_kill_switch_active(env)
        drain_active, drain_reason = self.is_drain_mode_active(env)

        # Keep the persisted breaker.reason as diagnostic detail, but expose a
        # canonical blocked_reason for callers. This avoids tests/consumers
        # depending on arbitrary root-cause strings such as collect_recent_timeout
        # when the effective block is the circuit itself.
        circuit_block_reason = None
        circuit_reason = None
        if breaker.state == "open":
            circuit_block_reason = "circuit_open"
            circuit_reason = breaker.reason or circuit_block_reason
        elif breaker.state == "half_open":
            if not self.allow_half_open_trial(breaker):
                circuit_block_reason = "circuit_half_open_blocked"
                circuit_reason = breaker.reason or circuit_block_reason

        market_ok = True
        market_reason = None
        if self.market_context_fail_closed and market_context_stale:
            market_ok = False
            market_reason = "market_context_stale"
        elif self.market_context_fail_closed and market_open is False:
            market_ok = False
            market_reason = "market_closed"

        blocked_reason = None
        if kill_active:
            blocked_reason = kill_reason or "kill_switch"
        elif circuit_block_reason is not None:
            blocked_reason = circuit_block_reason
        elif quota_hard_block:
            blocked_reason = quota_reason or "quota_hard_block"
        elif not market_ok:
            blocked_reason = market_reason

        return FailsafeSnapshot(
            global_fail_closed=self.global_fail_closed,
            kill_switch_active=kill_active,
            kill_switch_reason=kill_reason,
            drain_mode_active=drain_active,
            drain_mode_reason=drain_reason,
            circuit_state=breaker.state,
            circuit_reason=circuit_reason,
            quota_hard_block=quota_hard_block,
            quota_reason=quota_reason,
            market_context_ok=market_ok,
            market_context_reason=market_reason,
            ready_to_trade=blocked_reason is None,
            blocked_reason=blocked_reason,
        )
