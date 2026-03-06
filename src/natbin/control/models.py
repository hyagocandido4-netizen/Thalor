from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class RuntimeAppConfig:
    asset: str
    interval_sec: int
    timezone: str
    dataset_path: str
    config_path: str


@dataclass(frozen=True)
class RuntimeScopeInfo:
    asset: str
    interval_sec: int
    timezone: str
    scope_tag: str


@dataclass(frozen=True)
class RuntimeAppCapabilities:
    control_app: bool
    runtime_cycle: bool
    runtime_daemon: bool
    runtime_quota: bool
    runtime_scope: bool
    runtime_repos: bool
    runtime_observability: bool
    runtime_execution: bool
    runtime_reconciliation: bool


@dataclass(frozen=True)
class RuntimeContext:
    repo_root: str
    config: RuntimeAppConfig
    scope: RuntimeScopeInfo
    resolved_config: dict[str, Any]
    source_trace: list[str]
    scoped_paths: Dict[str, str]
    control_paths: Dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimePlan:
    mode: str
    repo_root: str
    scope: dict[str, Any]
    config_path: str
    steps: list[dict[str, Any]]
    control_paths: Dict[str, str]
    scoped_paths: Dict[str, str]
    notes: Dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeAppInfo:
    config: RuntimeAppConfig
    scope: RuntimeScopeInfo
    capabilities: RuntimeAppCapabilities
    scoped_paths: Dict[str, str]
    control_paths: Dict[str, str]
    health: Dict[str, Any]
    notes: Dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObserveRequest:
    once: bool = False
    max_cycles: Optional[int] = None
    topk: int = 3
    lookback_candles: int = 2000
    stop_on_failure: bool = True
    quota_aware_sleep: bool = False
    precheck_market_context: bool = False
    sleep_align_offset_sec: int = 3

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObserveResult:
    ok: bool
    exit_code: int
    phase: str
    action: str
    reason: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
