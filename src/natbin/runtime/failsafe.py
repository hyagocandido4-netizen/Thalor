from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

CircuitState = Literal['closed', 'open', 'half_open']


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    failures_to_open: int = 3
    cooldown_minutes: int = 15
    half_open_trials: int = 1


@dataclass
class CircuitBreakerSnapshot:
    asset: str
    interval_sec: int
    state: CircuitState = 'closed'
    failures: int = 0
    last_failure_utc: Optional[datetime] = None
    opened_until_utc: Optional[datetime] = None
    half_open_trials_used: int = 0
    reason: Optional[str] = None
    primary_cause: Optional[str] = None
    failure_domain: Optional[str] = None
    failure_step: Optional[str] = None
    last_transport_error: Optional[str] = None
    last_transport_failure_utc: Optional[datetime] = None
    half_open_trial_in_flight: bool = False

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None = None, /, **defaults: Any) -> "CircuitBreakerSnapshot":
        data = dict(payload or {})
        data.update(defaults)
        allowed = {field.name for field in dataclass_fields(cls)}
        filtered = {key: value for key, value in data.items() if key in allowed}

        def _parse_dt(raw: Any) -> datetime | None:
            if raw in (None, ''):
                return None
            if isinstance(raw, datetime):
                return raw
            try:
                value = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            except Exception:
                return None
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)

        for key in ('last_failure_utc', 'opened_until_utc', 'last_transport_failure_utc'):
            if key in filtered:
                filtered[key] = _parse_dt(filtered.get(key))
        for key in ('interval_sec', 'failures', 'half_open_trials_used'):
            if key in filtered and filtered.get(key) not in (None, ''):
                try:
                    filtered[key] = int(filtered.get(key))
                except Exception:
                    pass
        if 'half_open_trial_in_flight' in filtered:
            filtered['half_open_trial_in_flight'] = bool(filtered.get('half_open_trial_in_flight'))
        return cls(**filtered)

    def as_dict(self) -> dict:
        return {
            'asset': self.asset,
            'interval_sec': self.interval_sec,
            'state': self.state,
            'failures': self.failures,
            'last_failure_utc': self.last_failure_utc.isoformat() if self.last_failure_utc else None,
            'opened_until_utc': self.opened_until_utc.isoformat() if self.opened_until_utc else None,
            'half_open_trials_used': self.half_open_trials_used,
            'reason': self.reason,
            'primary_cause': self.primary_cause,
            'failure_domain': self.failure_domain,
            'failure_step': self.failure_step,
            'last_transport_error': self.last_transport_error,
            'last_transport_failure_utc': self.last_transport_failure_utc.isoformat() if self.last_transport_failure_utc else None,
            'half_open_trial_in_flight': bool(self.half_open_trial_in_flight),
        }


@dataclass
class FailsafeSnapshot:
    global_fail_closed: bool
    kill_switch_active: bool
    kill_switch_reason: Optional[str]
    drain_mode_active: bool
    drain_mode_reason: Optional[str]
    drain_mode_ignored: bool
    circuit_state: CircuitState
    circuit_reason: Optional[str]
    quota_hard_block: bool
    quota_reason: Optional[str]
    market_context_ok: bool
    market_context_reason: Optional[str]
    half_open_trial_available: bool
    half_open_trials_remaining: int
    half_open_trial_in_flight: bool
    ready_to_trade: bool
    blocked_reason: Optional[str]

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None = None, /, **defaults: Any) -> "CircuitBreakerSnapshot":
        data = dict(payload or {})
        data.update(defaults)
        allowed = {field.name for field in dataclass_fields(cls)}
        filtered = {key: value for key, value in data.items() if key in allowed}

        def _parse_dt(raw: Any) -> datetime | None:
            if raw in (None, ''):
                return None
            if isinstance(raw, datetime):
                return raw
            try:
                value = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            except Exception:
                return None
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)

        for key in ('last_failure_utc', 'opened_until_utc', 'last_transport_failure_utc'):
            if key in filtered:
                filtered[key] = _parse_dt(filtered.get(key))
        for key in ('interval_sec', 'failures', 'half_open_trials_used'):
            if key in filtered and filtered.get(key) not in (None, ''):
                try:
                    filtered[key] = int(filtered.get(key))
                except Exception:
                    pass
        if 'half_open_trial_in_flight' in filtered:
            filtered['half_open_trial_in_flight'] = bool(filtered.get('half_open_trial_in_flight'))
        return cls(**filtered)

    def as_dict(self) -> dict:
        return {
            'global_fail_closed': self.global_fail_closed,
            'kill_switch_active': self.kill_switch_active,
            'kill_switch_reason': self.kill_switch_reason,
            'drain_mode_active': self.drain_mode_active,
            'drain_mode_reason': self.drain_mode_reason,
            'drain_mode_ignored': self.drain_mode_ignored,
            'circuit_state': self.circuit_state,
            'circuit_reason': self.circuit_reason,
            'quota_hard_block': self.quota_hard_block,
            'quota_reason': self.quota_reason,
            'market_context_ok': self.market_context_ok,
            'market_context_reason': self.market_context_reason,
            'half_open_trial_available': self.half_open_trial_available,
            'half_open_trials_remaining': self.half_open_trials_remaining,
            'half_open_trial_in_flight': self.half_open_trial_in_flight,
            'ready_to_trade': self.ready_to_trade,
            'blocked_reason': self.blocked_reason,
        }


@dataclass
class RuntimeFailsafe:
    kill_switch_file: Path = Path('runs/KILL_SWITCH')
    kill_switch_env_var: str = 'THALOR_KILL_SWITCH'
    drain_mode_file: Path = Path('runs/DRAIN_MODE')
    drain_mode_env_var: str = 'THALOR_DRAIN_MODE'
    global_fail_closed: bool = True
    market_context_fail_closed: bool = True
    policy: CircuitBreakerPolicy = field(default_factory=CircuitBreakerPolicy)

    def is_kill_switch_active(self, env: dict[str, str] | None = None) -> tuple[bool, Optional[str]]:
        env = env or {}
        if str(env.get(self.kill_switch_env_var, '')).strip().lower() in {'1', 'true', 'yes'}:
            return True, f'env:{self.kill_switch_env_var}'
        if self.kill_switch_file.exists():
            return True, f'file:{self.kill_switch_file}'
        return False, None

    def is_drain_mode_active(self, env: dict[str, str] | None = None) -> tuple[bool, Optional[str]]:
        env = env or {}
        if str(env.get(self.drain_mode_env_var, '')).strip().lower() in {'1', 'true', 'yes'}:
            return True, f'env:{self.drain_mode_env_var}'
        if self.drain_mode_file.exists():
            return True, f'file:{self.drain_mode_file}'
        return False, None

    def evaluate_circuit(self, snap: CircuitBreakerSnapshot, now_utc: datetime) -> CircuitBreakerSnapshot:
        if snap.state == 'open' and snap.opened_until_utc and now_utc >= snap.opened_until_utc:
            snap.state = 'half_open'
            snap.half_open_trials_used = 0
            snap.half_open_trial_in_flight = False
        return snap

    def record_failure(self, snap: CircuitBreakerSnapshot, reason: str, now_utc: datetime) -> CircuitBreakerSnapshot:
        snap.failures += 1
        snap.last_failure_utc = now_utc
        snap.reason = reason
        snap.half_open_trial_in_flight = False
        if snap.state in ('closed', 'half_open') and snap.failures >= self.policy.failures_to_open:
            snap.state = 'open'
            snap.opened_until_utc = now_utc + timedelta(minutes=self.policy.cooldown_minutes)
            snap.half_open_trials_used = 0
        return snap

    def record_success(self, snap: CircuitBreakerSnapshot) -> CircuitBreakerSnapshot:
        snap.failures = 0
        snap.reason = None
        snap.primary_cause = None
        snap.failure_domain = None
        snap.failure_step = None
        snap.last_failure_utc = None
        snap.opened_until_utc = None
        snap.half_open_trials_used = 0
        snap.half_open_trial_in_flight = False
        snap.state = 'closed'
        return snap

    def _half_open_status(self, snap: CircuitBreakerSnapshot) -> tuple[bool, int, bool]:
        if snap.state != 'half_open':
            return True, int(self.policy.half_open_trials), False
        remaining = max(0, int(self.policy.half_open_trials) - int(snap.half_open_trials_used))
        in_flight = bool(snap.half_open_trial_in_flight)
        available = remaining > 0 and not in_flight
        return available, remaining, in_flight

    def allow_half_open_trial(self, snap: CircuitBreakerSnapshot) -> bool:
        available, remaining, in_flight = self._half_open_status(snap)
        if snap.state != 'half_open':
            return True
        if not available:
            return False
        snap.half_open_trials_used += 1
        snap.half_open_trial_in_flight = True
        return True

    def finish_half_open_trial(self, snap: CircuitBreakerSnapshot) -> None:
        if snap.state == 'half_open':
            snap.half_open_trial_in_flight = False

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
        allow_drain_mode: bool = False,
    ) -> FailsafeSnapshot:
        breaker = self.evaluate_circuit(breaker, now_utc)
        kill_active, kill_reason = self.is_kill_switch_active(env)
        drain_active, drain_reason = self.is_drain_mode_active(env)
        drain_mode_ignored = bool(drain_active and allow_drain_mode)

        circuit_block_reason = None
        circuit_reason = None
        half_open_trial_available, half_open_trials_remaining, half_open_trial_in_flight = self._half_open_status(breaker)
        if breaker.state == 'open':
            circuit_block_reason = 'circuit_open'
            circuit_reason = breaker.reason or circuit_block_reason
        elif breaker.state == 'half_open' and not half_open_trial_available:
            circuit_block_reason = 'circuit_half_open_blocked'
            circuit_reason = breaker.reason or circuit_block_reason
        else:
            circuit_reason = breaker.reason

        market_ok = True
        market_reason = None
        if self.market_context_fail_closed and market_context_stale:
            market_ok = False
            market_reason = 'market_context_stale'
        elif self.market_context_fail_closed and market_open is False:
            market_ok = False
            market_reason = 'market_closed'

        blocked_reason = None
        if kill_active:
            blocked_reason = kill_reason or 'kill_switch'
        elif drain_active and not allow_drain_mode:
            blocked_reason = drain_reason or 'drain_mode'
        elif circuit_block_reason is not None:
            blocked_reason = circuit_block_reason
        elif quota_hard_block:
            blocked_reason = quota_reason or 'quota_hard_block'
        elif not market_ok:
            blocked_reason = market_reason

        return FailsafeSnapshot(
            global_fail_closed=self.global_fail_closed,
            kill_switch_active=kill_active,
            kill_switch_reason=kill_reason,
            drain_mode_active=drain_active,
            drain_mode_reason=drain_reason,
            drain_mode_ignored=drain_mode_ignored,
            circuit_state=breaker.state,
            circuit_reason=circuit_reason,
            quota_hard_block=quota_hard_block,
            quota_reason=quota_reason,
            market_context_ok=market_ok,
            market_context_reason=market_reason,
            half_open_trial_available=half_open_trial_available,
            half_open_trials_remaining=half_open_trials_remaining,
            half_open_trial_in_flight=half_open_trial_in_flight,
            ready_to_trade=blocked_reason is None,
            blocked_reason=blocked_reason,
        )
