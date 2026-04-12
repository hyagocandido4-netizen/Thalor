from __future__ import annotations

"""Pydantic models for Thalor configuration.

These models are intentionally strict (extra fields forbidden) so the runtime
can fail-fast on misconfiguration.

In Package M v1, we still accept the legacy root ``config.yaml`` by mapping its
keys into this model shape in :mod:`natbin.config.sources`.

Package N adds an explicit execution/reconciliation section so runtime quota and
broker-facing behaviour can be configured without relying on ad-hoc env vars.
"""

from datetime import UTC, datetime
from pathlib import Path

from .execution_mode import MODE_DISABLED, MODE_PRACTICE, normalize_execution_mode
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrokerSettings(BaseModel):
    provider: Literal["iqoption"] = "iqoption"
    email: str | None = None
    password: SecretStr | None = None
    balance_mode: Literal["PRACTICE", "REAL"] = "PRACTICE"

    connect_retries: int = 8
    connect_sleep_s: float = 1.0
    connect_sleep_max_s: float = 8.0
    timeout_connect_s: int = 25

    get_candles_retries: int = 3
    get_candles_sleep_s: float = 1.0
    get_candles_sleep_max_s: float = 4.0

    # Package M6: formalize IQ API throttling/backoff from config instead of
    # relying only on ad-hoc env vars.
    api_throttle_min_interval_s: float = 0.0
    api_throttle_jitter_s: float = 0.0


class AssetSettings(BaseModel):
    asset: str
    interval_sec: int = 300
    enabled: bool = True
    timezone: str = "America/Sao_Paulo"
    payout_default: float = 0.80
    topk_k: int = 3

    # Portfolio/runtime hints (Package O)
    weight: float = 1.0
    cluster_key: str = "default"
    hard_max_trades_per_day: int | None = None
    max_open_positions: int | None = None
    max_pending_unknown: int | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AssetSettings":
        if not str(self.asset).strip():
            raise ValueError("asset must be non-empty")
        if int(self.interval_sec) <= 0:
            raise ValueError("interval_sec must be > 0")
        if not str(self.timezone).strip():
            raise ValueError("timezone must be non-empty")
        return self

    @computed_field
    @property
    def scope_key(self) -> str:
        safe = (
            str(self.asset)
            .replace("/", "_")
            .replace(":", "_")
            .replace(" ", "_")
        )
        return f"{safe}_{int(self.interval_sec)}s"


class DataSettings(BaseModel):
    db_path: Path = Path("data/market_otc.sqlite3")
    dataset_path: Path = Path("data/dataset_phase2.csv")
    lookback_candles: int = 2000
    max_batch: int = 1000


class RegimeBoundsSettings(BaseModel):
    vol_lo: float
    vol_hi: float
    bb_lo: float
    bb_hi: float
    atr_lo: float
    atr_hi: float

    @model_validator(mode="after")
    def _validate(self) -> "RegimeBoundsSettings":
        pairs = [
            (float(self.vol_lo), float(self.vol_hi), "vol"),
            (float(self.bb_lo), float(self.bb_hi), "bb"),
            (float(self.atr_lo), float(self.atr_hi), "atr"),
        ]
        for lo, hi, prefix in pairs:
            if lo > hi:
                raise ValueError(f"decision.bounds.{prefix}_lo must be <= decision.bounds.{prefix}_hi")
        return self

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.model_dump(mode="python").items()}


class CpRegSettings(BaseModel):
    enabled: bool = False
    alpha_start: float = 0.06
    alpha_end: float = 0.09
    warmup_frac: float = 0.50
    ramp_end_frac: float = 0.90
    slot2_mult: float = 0.85
    clamp_min: float = 0.001
    clamp_max: float = 0.50

    @model_validator(mode="after")
    def _validate(self) -> "CpRegSettings":
        if float(self.alpha_start) <= 0.0:
            raise ValueError("decision.cpreg.alpha_start must be > 0")
        if float(self.alpha_end) <= 0.0:
            raise ValueError("decision.cpreg.alpha_end must be > 0")
        if not 0.0 <= float(self.warmup_frac) <= 1.0:
            raise ValueError("decision.cpreg.warmup_frac must be between 0 and 1")
        if not 0.0 <= float(self.ramp_end_frac) <= 1.0:
            raise ValueError("decision.cpreg.ramp_end_frac must be between 0 and 1")
        if float(self.ramp_end_frac) < float(self.warmup_frac):
            raise ValueError("decision.cpreg.ramp_end_frac must be >= decision.cpreg.warmup_frac")
        if float(self.slot2_mult) <= 0.0:
            raise ValueError("decision.cpreg.slot2_mult must be > 0")
        if float(self.clamp_min) <= 0.0:
            raise ValueError("decision.cpreg.clamp_min must be > 0")
        if float(self.clamp_max) < float(self.clamp_min):
            raise ValueError("decision.cpreg.clamp_max must be >= decision.cpreg.clamp_min")
        return self


class DecisionSettings(BaseModel):
    # Requested gating mode for scoring. The scorer may report a more specific
    # gate_used (e.g. cp_meta_iso), but config should stay stable.
    gate_mode: str = "cp"
    meta_model: str = "hgb"
    thresh_on: str = "ev"
    threshold: float = 0.02

    # Static conformal alpha used when CPREG is disabled.
    cp_alpha: float | None = None
    cpreg: CpRegSettings = Field(default_factory=CpRegSettings)

    # Optional model registry / tuning pointer for live runtime.
    # When set, this is recorded into decision snapshots for traceability.
    tune_dir: str = ""
    # Optional regime bounds used by make_regime_mask (keys: vol_lo/hi, bb_lo/hi, atr_lo/hi).
    bounds: RegimeBoundsSettings | None = None

    rolling_minutes: int = 360
    min_gap_minutes: int = 30
    pacing_enable: bool = True

    fail_closed: bool = True
    # Practice/canary bootstrap escape hatch for CP-gated profiles.
    #
    # When gate_mode=cp and the observer cache has a meta-model but still lacks
    # a fitted conformal gate, older behavior was to fail-closed forever with
    # ``cp_fail_closed_missing_cp_meta``. That is appropriate for strict live
    # envelopes, but it blocks canary/practice recovery even when the provider,
    # market context and meta score are otherwise healthy.
    #
    # Supported values:
    #   - off/none/fail_closed: preserve strict CP fail-closed semantics
    #   - auto/meta/meta_iso: reuse the available meta score as a temporary
    #     bootstrap fallback until CP metadata becomes available
    cp_bootstrap_fallback: str = "off"

    @model_validator(mode="after")
    def _validate(self) -> "DecisionSettings":
        if self.cp_alpha is not None and not 0.0 <= float(self.cp_alpha) <= 1.0:
            raise ValueError("decision.cp_alpha must be between 0 and 1")
        fallback = str(self.cp_bootstrap_fallback or 'off').strip().lower()
        allowed = {'off', 'none', 'fail_closed', 'auto', 'meta', 'meta_iso'}
        if fallback not in allowed:
            raise ValueError('decision.cp_bootstrap_fallback must be one of off, none, fail_closed, auto, meta, meta_iso')
        self.cp_bootstrap_fallback = fallback
        return self


class QuotaSettings(BaseModel):
    target_trades_per_day: float = 3.0
    hard_max_trades_per_day: int = 3
    pacing_morning_cap: int = 1
    pacing_afternoon_cap: int = 2
    pacing_morning_until_hhmm: str = "08:00"
    pacing_afternoon_until_hhmm: str = "16:00"


class AutosSettings(BaseModel):
    enabled: bool = True
    summary_fail_closed: bool = True
    legacy_summary_fallback: bool = False
    min_days_used: int = 3
    min_trades_eval: int = 10

    volume_enabled: bool = True
    isoblend_enabled: bool = True
    hourthr_enabled: bool = True


class RequestMetricsSettings(BaseModel):
    enabled: bool = False
    timezone: str | None = None
    structured_log_path: Path | None = None
    summary_log_level: int | str = 'INFO'
    emit_summary_on_rollover: bool = True
    emit_summary_on_close: bool = True
    emit_request_events: bool = True
    emit_summary_every_requests: int = 25

    @field_validator('timezone', mode='before')
    @classmethod
    def _normalize_timezone(cls, value: object) -> str | None:
        if value in (None, ''):
            return None
        text = str(value).strip()
        return text or None


class NetworkTransportSettings(BaseModel):
    enabled: bool = False
    endpoint: str | None = None
    endpoints: list[Any] = Field(default_factory=list)
    endpoint_file: Path | None = None
    endpoints_file: Path | None = None
    no_proxy: list[str] = Field(default_factory=list)
    max_retries: int = 3
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0
    jitter_ratio: float = 0.2
    failure_threshold: int = 3
    quarantine_base_s: float = 30.0
    quarantine_max_s: float = 300.0
    healthcheck_interval_s: float = 60.0
    healthcheck_timeout_s: float = 3.0
    healthcheck_mode: Literal['tcp', 'http'] = 'tcp'
    healthcheck_url: str | None = None
    fail_open_when_exhausted: bool = True
    structured_log_path: Path | None = None

    @field_validator('no_proxy', mode='before')
    @classmethod
    def _normalize_no_proxy(cls, value: object) -> list[str]:
        if value in (None, ''):
            return []
        if isinstance(value, str):
            parts = [item.strip() for item in value.split(',')]
            return [item for item in parts if item]
        if isinstance(value, (list, tuple, set)):
            out: list[str] = []
            for item in value:
                text = str(item or '').strip()
                if text:
                    out.append(text)
            return out
        text = str(value).strip()
        return [text] if text else []

    @field_validator('healthcheck_mode', mode='before')
    @classmethod
    def _normalize_healthcheck_mode(cls, value: object) -> str:
        text = str(value or 'tcp').strip().lower()
        if text not in {'tcp', 'http'}:
            raise ValueError('network.transport.healthcheck_mode must be tcp or http')
        return text

    @model_validator(mode='after')
    def _validate(self) -> 'NetworkTransportSettings':
        if int(self.max_retries) < 1:
            raise ValueError('network.transport.max_retries must be >= 1')
        if float(self.backoff_base_s) <= 0:
            raise ValueError('network.transport.backoff_base_s must be > 0')
        if float(self.backoff_max_s) < float(self.backoff_base_s):
            raise ValueError('network.transport.backoff_max_s must be >= network.transport.backoff_base_s')
        if float(self.jitter_ratio) < 0 or float(self.jitter_ratio) > 1:
            raise ValueError('network.transport.jitter_ratio must be between 0 and 1')
        if int(self.failure_threshold) < 1:
            raise ValueError('network.transport.failure_threshold must be >= 1')
        if float(self.quarantine_base_s) <= 0:
            raise ValueError('network.transport.quarantine_base_s must be > 0')
        if float(self.quarantine_max_s) < float(self.quarantine_base_s):
            raise ValueError('network.transport.quarantine_max_s must be >= network.transport.quarantine_base_s')
        if float(self.healthcheck_interval_s) < 0:
            raise ValueError('network.transport.healthcheck_interval_s must be >= 0')
        if float(self.healthcheck_timeout_s) <= 0:
            raise ValueError('network.transport.healthcheck_timeout_s must be > 0')
        return self


class NetworkSettings(BaseModel):
    transport: NetworkTransportSettings = Field(default_factory=NetworkTransportSettings)


class ObservabilitySettings(BaseModel):
    status_enable: bool = True
    metrics_enable: bool = False
    metrics_bind: str = "127.0.0.1:9108"
    request_metrics: RequestMetricsSettings = Field(default_factory=RequestMetricsSettings)

    # Package P: structured JSONL logs (append-only) for ingestion.
    structured_logs_enable: bool = True
    structured_logs_path: Path = Path("runs/logs/runtime_structured.jsonl")

    loop_log_enable: bool = True
    loop_log_dir: Path = Path("runs/logs")
    loop_log_retention_days: int = 14

    incidents_enable: bool = True
    decision_snapshots_enable: bool = True


class DashboardReportSettings(BaseModel):
    output_dir: Path = Path("runs/reports/dashboard")
    export_json: bool = True


class DashboardSettings(BaseModel):
    enabled: bool = True
    title: str = "Thalor"
    theme: Literal["cyber_dragon", "dark"] = "cyber_dragon"
    default_refresh_sec: float = 3.0
    default_equity_start: float = 1000.0
    max_alerts: int = 50
    max_equity_points: int = 500
    report: DashboardReportSettings = Field(default_factory=DashboardReportSettings)

    @model_validator(mode="after")
    def _validate(self) -> "DashboardSettings":
        if not str(self.title).strip():
            raise ValueError("dashboard.title must be non-empty")
        if float(self.default_refresh_sec) < 0.0:
            raise ValueError("dashboard.default_refresh_sec must be >= 0")
        if float(self.default_equity_start) < 0.0:
            raise ValueError("dashboard.default_equity_start must be >= 0")
        if int(self.max_alerts) <= 0:
            raise ValueError("dashboard.max_alerts must be > 0")
        if int(self.max_equity_points) <= 0:
            raise ValueError("dashboard.max_equity_points must be > 0")
        return self


class MonteCarloReportSettings(BaseModel):
    output_dir: Path = Path("runs/reports/monte_carlo")
    export_json: bool = True
    export_html: bool = True
    export_pdf: bool = True


class MonteCarloScenarioSettings(BaseModel):
    label: str = "Scenario"
    trade_count_scale: float = 1.0
    return_scale: float = 1.0
    stake_scale: float = 1.0

    @model_validator(mode="after")
    def _validate(self) -> "MonteCarloScenarioSettings":
        if not str(self.label).strip():
            raise ValueError("monte_carlo scenario label must be non-empty")
        for name in ('trade_count_scale', 'return_scale', 'stake_scale'):
            if float(getattr(self, name)) <= 0.0:
                raise ValueError(f"monte_carlo scenario {name} must be > 0")
        return self


class MonteCarloSettings(BaseModel):
    enabled: bool = True
    initial_capital_brl: float = 1000.0
    horizon_days: int = 60
    trials: int = 1000
    rng_seed: int = 42
    min_realized_trades: int = 20
    max_stake_fraction_cap: float = 0.10
    conservative: MonteCarloScenarioSettings = Field(
        default_factory=lambda: MonteCarloScenarioSettings(
            label="Conservador",
            trade_count_scale=0.85,
            return_scale=0.90,
            stake_scale=0.90,
        )
    )
    medium: MonteCarloScenarioSettings = Field(
        default_factory=lambda: MonteCarloScenarioSettings(
            label="Médio",
            trade_count_scale=1.00,
            return_scale=1.00,
            stake_scale=1.00,
        )
    )
    aggressive: MonteCarloScenarioSettings = Field(
        default_factory=lambda: MonteCarloScenarioSettings(
            label="Agressivo",
            trade_count_scale=1.15,
            return_scale=1.10,
            stake_scale=1.10,
        )
    )
    report: MonteCarloReportSettings = Field(default_factory=MonteCarloReportSettings)

    @model_validator(mode="after")
    def _validate(self) -> "MonteCarloSettings":
        if float(self.initial_capital_brl) <= 0.0:
            raise ValueError("monte_carlo.initial_capital_brl must be > 0")
        if int(self.horizon_days) < 5:
            raise ValueError("monte_carlo.horizon_days must be >= 5")
        if int(self.trials) < 100:
            raise ValueError("monte_carlo.trials must be >= 100")
        if int(self.min_realized_trades) < 5:
            raise ValueError("monte_carlo.min_realized_trades must be >= 5")
        if not (0.0 < float(self.max_stake_fraction_cap) <= 1.0):
            raise ValueError("monte_carlo.max_stake_fraction_cap must be within (0,1]")
        return self


class ProductionBackupSettings(BaseModel):
    enabled: bool = True
    output_dir: Path = Path("runs/backups")
    archive_prefix: str = "thalor_backup"
    format: Literal["tar.gz", "zip"] = "tar.gz"
    interval_minutes: int = 60
    retention_days: int = 14
    max_archives: int = 48
    include_globs: list[str] = Field(
        default_factory=lambda: [
            "runs/runtime_execution.sqlite3",
            "runs/runtime_control.sqlite3",
            "runs/control/**/*.json",
            "runs/logs/**/*.jsonl",
            "runs/logs/**/*.log",
            "runs/intelligence/**/*",
            "runs/reports/**/*",
            "data/**/*.sqlite3",
            "data/datasets/**/*",
        ]
    )
    exclude_globs: list[str] = Field(
        default_factory=lambda: [
            "**/__pycache__/**",
            "**/*.tmp",
            "**/*.swp",
            "**/*.sqlite3-shm",
            "**/*.sqlite3-wal",
        ]
    )
    latest_manifest_path: Path = Path("runs/backups/latest.json")

    @model_validator(mode="after")
    def _validate(self) -> "ProductionBackupSettings":
        if not str(self.archive_prefix).strip():
            raise ValueError("production.backup.archive_prefix must be non-empty")
        if int(self.interval_minutes) <= 0:
            raise ValueError("production.backup.interval_minutes must be > 0")
        if int(self.retention_days) <= 0:
            raise ValueError("production.backup.retention_days must be > 0")
        if int(self.max_archives) <= 0:
            raise ValueError("production.backup.max_archives must be > 0")
        if not list(self.include_globs):
            raise ValueError("production.backup.include_globs must be non-empty")
        return self


class ProductionHealthcheckSettings(BaseModel):
    enabled: bool = True
    require_loop_status: bool = False
    max_loop_status_age_sec: int = 1800
    check_kill_switch: bool = True
    check_drain_mode: bool = False
    require_execution_repo: bool = False
    scope_sample_limit: int = 6

    @model_validator(mode="after")
    def _validate(self) -> "ProductionHealthcheckSettings":
        if int(self.max_loop_status_age_sec) <= 0:
            raise ValueError("production.healthcheck.max_loop_status_age_sec must be > 0")
        if int(self.scope_sample_limit) <= 0:
            raise ValueError("production.healthcheck.scope_sample_limit must be > 0")
        return self


class ProductionSettings(BaseModel):
    enabled: bool = False
    profile: Literal["local", "vps", "docker"] = "local"
    backup: ProductionBackupSettings = Field(default_factory=ProductionBackupSettings)
    healthcheck: ProductionHealthcheckSettings = Field(default_factory=ProductionHealthcheckSettings)




class FailsafeSettings(BaseModel):
    global_fail_closed: bool = True
    market_context_fail_closed: bool = True
    summary_fail_closed: bool = True

    kill_switch_file: Path = Path("runs/KILL_SWITCH")
    kill_switch_env_var: str = "THALOR_KILL_SWITCH"

    # Package P: "drain" mode blocks new submits but allows reconciliation.
    drain_mode_file: Path = Path("runs/DRAIN_MODE")
    drain_mode_env_var: str = "THALOR_DRAIN_MODE"

    circuit_breaker_enable: bool = True
    breaker_failures_to_open: int = 3
    breaker_cooldown_minutes: int = 15
    breaker_half_open_trials: int = 1


class RuntimeSettings(BaseModel):
    profile: str = "default"
    runtime_retention_days: int = 30
    state_reconcile_days: int = 7
    legacy_runtime_cleanup_enable: bool = True
    quota_aware_sleep: bool = True

    # Package M3: runtime soak / scheduler hardening.
    # When null, the runtime derives a conservative freshness window from the
    # scope interval (currently max(interval*3, 600)).
    stale_artifact_after_sec: int | None = None
    startup_invalidate_stale_artifacts: bool = True
    startup_lifecycle_artifacts: bool = True
    lock_refresh_enable: bool = True


class MultiAssetSettings(BaseModel):
    enabled: bool = False
    max_parallel_assets: int = 4

    # Optional staggering (seconds) between scopes when running parallel phases.
    # Useful to avoid bursty broker/API requests and reduce simultaneous file I/O.
    stagger_sec: float = Field(0.0, ge=0.0)

    # Optional stagger specific to execution submits. When null/zero, the runtime
    # falls back to ``stagger_sec``.
    execution_stagger_sec: float = Field(0.0, ge=0.0)

    # Portfolio selection limits
    portfolio_topk_total: int = 6
    portfolio_hard_max_positions: int = 6
    portfolio_hard_max_trades_per_day: int | None = None
    portfolio_hard_max_pending_unknown_total: int | None = 1

    # Shared quota defaults for assets that do not define an explicit override.
    asset_quota_default_trades_per_day: int | None = None
    asset_quota_default_max_open_positions: int | None = None
    asset_quota_default_max_pending_unknown: int | None = None

    # Exposure caps (cross-asset / cross-interval)
    portfolio_hard_max_positions_per_asset: int | None = 1
    portfolio_hard_max_positions_per_cluster: int | None = 1

    # Correlation-aware suppression uses explicit ``cluster_key`` when present
    # and otherwise infers a deterministic quote-bucket group from the asset.
    correlation_filter_enable: bool = True
    max_trades_per_cluster_per_cycle: int = 1

    # Safe partitioning of per-asset data paths (recommended for parallel runs)
    partition_data_paths: bool = True
    data_db_template: str = "data/market_{scope_tag}.sqlite3"
    dataset_path_template: str = "data/datasets/{scope_tag}/dataset.csv"

    # Runtime headroom hardening for long multi-asset observe loops.
    # ``adaptive_prepare_enable`` avoids re-running the full prepare pipeline
    # when the local candle DB + market context are already fresh.
    adaptive_prepare_enable: bool = True

    # When the local DB already exists, only a shorter collect window is needed
    # to converge the newest candles. The full decision lookback remains
    # unchanged for the candidate phase.
    prepare_incremental_lookback_candles: int | None = Field(256, ge=32)

    # Rotate governed candidate scans when the provider governor enforces a
    # budget smaller than the full scope count.
    candidate_budget_rotation_enable: bool = True




class IntelligenceScopePolicy(BaseModel):
    name: str = "scope_policy"
    scope_tag: str | None = None
    asset: str | None = None
    interval_sec: int | None = None

    learned_weight: float | None = None
    promote_above: float | None = None
    suppress_below: float | None = None
    abstain_band: float | None = None
    min_reliability: float | None = None
    neutralize_low_reliability: bool | None = None
    stack_max_bonus: float | None = None
    stack_max_penalty: float | None = None
    learned_fail_closed: bool | None = None
    drift_fail_closed: bool | None = None
    portfolio_weight: float | None = None
    allocator_block_regime: bool | None = None
    allocator_warn_penalty: float | None = None
    allocator_block_penalty: float | None = None
    allocator_under_target_bonus: float | None = None
    allocator_over_target_penalty: float | None = None
    allocator_retrain_penalty: float | None = None
    allocator_reliability_penalty: float | None = None

    @model_validator(mode="after")
    def _validate(self) -> "IntelligenceScopePolicy":
        if self.interval_sec is not None and int(self.interval_sec) <= 0:
            raise ValueError("intelligence.scope_policies[].interval_sec must be > 0")
        for name in ('learned_weight', 'promote_above', 'suppress_below', 'min_reliability'):
            value = getattr(self, name)
            if value is not None and not (0.0 <= float(value) <= 1.0):
                raise ValueError(f"intelligence.scope_policies[].{name} must be within [0,1]")
        if self.abstain_band is not None and not (0.0 <= float(self.abstain_band) <= 0.50):
            raise ValueError("intelligence.scope_policies[].abstain_band must be within [0,0.50]")
        if self.portfolio_weight is not None and not (0.0 <= float(self.portfolio_weight) <= 2.0):
            raise ValueError("intelligence.scope_policies[].portfolio_weight must be within [0,2]")
        for name in (
            'stack_max_bonus',
            'stack_max_penalty',
            'allocator_warn_penalty',
            'allocator_block_penalty',
            'allocator_under_target_bonus',
            'allocator_over_target_penalty',
            'allocator_retrain_penalty',
            'allocator_reliability_penalty',
        ):
            value = getattr(self, name)
            if value is not None and float(value) < 0:
                raise ValueError(f"intelligence.scope_policies[].{name} must be >= 0")
        if (
            self.promote_above is not None and self.suppress_below is not None
            and float(self.promote_above) < float(self.suppress_below)
        ):
            raise ValueError("intelligence.scope_policies[].promote_above must be >= suppress_below")
        return self


class IntelligenceSettings(BaseModel):
    enabled: bool = True
    artifact_dir: Path = Path("runs/intelligence")

    # P18 — slot-aware tuning
    slot_aware_enable: bool = True
    slot_aware_min_trades: int = 6
    slot_aware_prior_weight: float = 8.0
    slot_aware_multiplier_min: float = 0.85
    slot_aware_multiplier_max: float = 1.15
    slot_aware_score_delta_cap: float = 0.05
    slot_aware_threshold_delta_cap: float = 0.03

    # P19 — learned gating / stacking
    learned_gating_enable: bool = True
    learned_gating_min_rows: int = 50
    learned_gating_weight: float = 0.60
    learned_stacking_enable: bool = True
    learned_promote_above: float = 0.62
    learned_suppress_below: float = 0.42
    learned_abstain_band: float = 0.03
    learned_fail_closed: bool = False
    learned_calibration_enable: bool = True
    learned_min_reliability: float = 0.50
    learned_neutralize_low_reliability: bool = True
    stack_max_bonus: float = 0.05
    stack_max_penalty: float = 0.05
    portfolio_weight: float = 1.0
    allocator_block_regime: bool = True
    allocator_warn_penalty: float = 0.04
    allocator_block_penalty: float = 0.12
    allocator_under_target_bonus: float = 0.03
    allocator_over_target_penalty: float = 0.04
    allocator_retrain_penalty: float = 0.05
    allocator_reliability_penalty: float = 0.03
    scope_policies: list[IntelligenceScopePolicy] = Field(default_factory=list)

    # P20 — drift / regime monitor
    drift_monitor_enable: bool = True
    drift_recent_limit: int = 200
    drift_warn_psi: float = 0.15
    drift_block_psi: float = 0.30
    drift_fail_closed: bool = False
    regime_warn_shift: float = 0.10
    regime_block_shift: float = 0.20
    retrain_warn_streak: int = 3
    retrain_block_streak: int = 1
    retrain_cooldown_hours: int = 12
    retrain_plan_cooldown_hours: int = 24
    retrain_rejection_backoff_hours: int = 6
    retrain_watch_reliability_below: float = 0.55
    retrain_queue_on_regime_block: bool = True
    retrain_queue_on_anti_overfit_reject: bool = True

    # P21 — coverage regulator 2.0
    coverage_regulator_enable: bool = True
    coverage_target_trades_per_day: float | None = None
    coverage_tolerance: float = 0.50
    coverage_bias_weight: float = 0.04
    coverage_curve_power: float = 1.20
    coverage_max_bonus: float = 0.05
    coverage_max_penalty: float = 0.05

    # P22 — anti-overfitting guard
    anti_overfit_enable: bool = True
    anti_overfit_fail_closed: bool = False
    anti_overfit_min_robustness: float = 0.50
    anti_overfit_min_windows: int = 3
    anti_overfit_gap_penalty_weight: float = 0.10
    anti_overfit_tuning_enable: bool = True
    anti_overfit_tuning_min_robustness_floor: float = 0.45
    anti_overfit_tuning_window_flex: int = 1
    anti_overfit_tuning_gap_penalty_flex: float = 0.03
    anti_overfit_tuning_recent_rows_min: int = 48
    anti_overfit_tuning_objective_min_delta: float = 0.015

    @model_validator(mode="after")
    def _validate(self) -> "IntelligenceSettings":
        if int(self.slot_aware_min_trades) < 0:
            raise ValueError("intelligence.slot_aware_min_trades must be >= 0")
        if float(self.slot_aware_prior_weight) < 0:
            raise ValueError("intelligence.slot_aware_prior_weight must be >= 0")
        if float(self.slot_aware_multiplier_min) <= 0:
            raise ValueError("intelligence.slot_aware_multiplier_min must be > 0")
        if float(self.slot_aware_multiplier_max) < float(self.slot_aware_multiplier_min):
            raise ValueError("intelligence.slot_aware_multiplier_max must be >= intelligence.slot_aware_multiplier_min")
        for name in ['slot_aware_score_delta_cap', 'slot_aware_threshold_delta_cap']:
            if float(getattr(self, name)) < 0:
                raise ValueError(f"intelligence.{name} must be >= 0")
        if int(self.learned_gating_min_rows) < 10:
            raise ValueError("intelligence.learned_gating_min_rows must be >= 10")
        if not (0.0 <= float(self.learned_gating_weight) <= 1.0):
            raise ValueError("intelligence.learned_gating_weight must be within [0,1]")
        if not (0.0 <= float(self.learned_suppress_below) <= 1.0):
            raise ValueError("intelligence.learned_suppress_below must be within [0,1]")
        if not (0.0 <= float(self.learned_promote_above) <= 1.0):
            raise ValueError("intelligence.learned_promote_above must be within [0,1]")
        if float(self.learned_promote_above) < float(self.learned_suppress_below):
            raise ValueError("intelligence.learned_promote_above must be >= intelligence.learned_suppress_below")
        if not (0.0 <= float(self.learned_abstain_band) <= 0.50):
            raise ValueError("intelligence.learned_abstain_band must be within [0,0.50]")
        if not (0.0 <= float(self.learned_min_reliability) <= 1.0):
            raise ValueError("intelligence.learned_min_reliability must be within [0,1]")
        if float(self.stack_max_bonus) < 0 or float(self.stack_max_penalty) < 0:
            raise ValueError("intelligence stack max bonus/penalty must be >= 0")
        if not (0.0 <= float(self.portfolio_weight) <= 2.0):
            raise ValueError("intelligence.portfolio_weight must be within [0,2]")
        for name in (
            'allocator_warn_penalty',
            'allocator_block_penalty',
            'allocator_under_target_bonus',
            'allocator_over_target_penalty',
            'allocator_retrain_penalty',
            'allocator_reliability_penalty',
        ):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"intelligence.{name} must be >= 0")
        if int(self.drift_recent_limit) < 20:
            raise ValueError("intelligence.drift_recent_limit must be >= 20")
        if float(self.drift_warn_psi) <= 0:
            raise ValueError("intelligence.drift_warn_psi must be > 0")
        if float(self.drift_block_psi) < float(self.drift_warn_psi):
            raise ValueError("intelligence.drift_block_psi must be >= intelligence.drift_warn_psi")
        if float(self.regime_warn_shift) <= 0:
            raise ValueError("intelligence.regime_warn_shift must be > 0")
        if float(self.regime_block_shift) < float(self.regime_warn_shift):
            raise ValueError("intelligence.regime_block_shift must be >= intelligence.regime_warn_shift")
        if int(self.retrain_warn_streak) < 1:
            raise ValueError("intelligence.retrain_warn_streak must be >= 1")
        if int(self.retrain_block_streak) < 1:
            raise ValueError("intelligence.retrain_block_streak must be >= 1")
        if int(self.retrain_cooldown_hours) < 0:
            raise ValueError("intelligence.retrain_cooldown_hours must be >= 0")
        if int(self.retrain_plan_cooldown_hours) < 0:
            raise ValueError("intelligence.retrain_plan_cooldown_hours must be >= 0")
        if int(self.retrain_rejection_backoff_hours) < 0:
            raise ValueError("intelligence.retrain_rejection_backoff_hours must be >= 0")
        if not (0.0 <= float(self.retrain_watch_reliability_below) <= 1.0):
            raise ValueError("intelligence.retrain_watch_reliability_below must be within [0,1]")
        if self.coverage_target_trades_per_day is not None and float(self.coverage_target_trades_per_day) <= 0:
            raise ValueError("intelligence.coverage_target_trades_per_day must be > 0 when set")
        if float(self.coverage_tolerance) < 0:
            raise ValueError("intelligence.coverage_tolerance must be >= 0")
        if float(self.coverage_bias_weight) < 0:
            raise ValueError("intelligence.coverage_bias_weight must be >= 0")
        if float(self.coverage_curve_power) <= 0:
            raise ValueError("intelligence.coverage_curve_power must be > 0")
        if float(self.coverage_max_bonus) < 0 or float(self.coverage_max_penalty) < 0:
            raise ValueError("intelligence coverage max bonus/penalty must be >= 0")
        if not (0.0 <= float(self.anti_overfit_min_robustness) <= 1.0):
            raise ValueError("intelligence.anti_overfit_min_robustness must be within [0,1]")
        if int(self.anti_overfit_min_windows) < 1:
            raise ValueError("intelligence.anti_overfit_min_windows must be >= 1")
        if float(self.anti_overfit_gap_penalty_weight) < 0:
            raise ValueError("intelligence.anti_overfit_gap_penalty_weight must be >= 0")
        if not (0.0 <= float(self.anti_overfit_tuning_min_robustness_floor) <= 1.0):
            raise ValueError("intelligence.anti_overfit_tuning_min_robustness_floor must be within [0,1]")
        if float(self.anti_overfit_tuning_min_robustness_floor) > float(self.anti_overfit_min_robustness):
            raise ValueError("intelligence.anti_overfit_tuning_min_robustness_floor must be <= anti_overfit_min_robustness")
        if int(self.anti_overfit_tuning_window_flex) < 0:
            raise ValueError("intelligence.anti_overfit_tuning_window_flex must be >= 0")
        if float(self.anti_overfit_tuning_gap_penalty_flex) < 0:
            raise ValueError("intelligence.anti_overfit_tuning_gap_penalty_flex must be >= 0")
        if int(self.anti_overfit_tuning_recent_rows_min) < 20:
            raise ValueError("intelligence.anti_overfit_tuning_recent_rows_min must be >= 20")
        if float(self.anti_overfit_tuning_objective_min_delta) < 0:
            raise ValueError("intelligence.anti_overfit_tuning_objective_min_delta must be >= 0")
        return self

class SecurityGuardSettings(BaseModel):
    enabled: bool = True
    live_only: bool = True
    min_submit_spacing_sec: int = 10
    max_submit_per_minute: int = 4
    time_filter_enable: bool = False
    allowed_start_local: str = "00:00"
    allowed_end_local: str = "23:59"
    blocked_weekdays_local: list[int] = Field(default_factory=list)
    state_path: Path = Path("runs/security/broker_guard_state.json")

    @model_validator(mode="after")
    def _validate(self) -> "SecurityGuardSettings":
        if int(self.min_submit_spacing_sec) < 0:
            raise ValueError("security.guard.min_submit_spacing_sec must be >= 0")
        if int(self.max_submit_per_minute) < 1:
            raise ValueError("security.guard.max_submit_per_minute must be >= 1")
        for raw in [self.allowed_start_local, self.allowed_end_local]:
            parts = str(raw or '').split(':')
            if len(parts) != 2:
                raise ValueError("security.guard allowed_start_local/end_local must be HH:MM")
            hh, mm = int(parts[0]), int(parts[1])
            if hh < 0 or hh > 23 or mm < 0 or mm > 59:
                raise ValueError("security.guard allowed_start_local/end_local must be HH:MM")
        cleaned: list[int] = []
        for item in list(self.blocked_weekdays_local or []):
            value = int(item)
            if value < 0 or value > 6:
                raise ValueError("security.guard.blocked_weekdays_local must be in [0,6]")
            if value not in cleaned:
                cleaned.append(value)
        self.blocked_weekdays_local = cleaned
        return self


class ProtectionWindowSettings(BaseModel):
    name: str = "session"
    start_local: str = "00:00"
    end_local: str = "23:59"

    @model_validator(mode="after")
    def _validate(self) -> "ProtectionWindowSettings":
        if not str(self.name).strip():
            raise ValueError("security.protection.sessions.windows[].name must be non-empty")
        for raw in [self.start_local, self.end_local]:
            parts = str(raw or '').split(':')
            if len(parts) != 2:
                raise ValueError("security.protection.sessions.windows start/end must be HH:MM")
            hh, mm = int(parts[0]), int(parts[1])
            if hh < 0 or hh > 23 or mm < 0 or mm > 59:
                raise ValueError("security.protection.sessions.windows start/end must be HH:MM")
        return self


class ProtectionSessionsSettings(BaseModel):
    enabled: bool = True
    inherit_guard_window: bool = True
    blocked_weekdays_local: list[int] = Field(default_factory=list)
    windows: list[ProtectionWindowSettings] = Field(default_factory=lambda: [ProtectionWindowSettings()])

    @model_validator(mode="after")
    def _validate(self) -> "ProtectionSessionsSettings":
        cleaned: list[int] = []
        for item in list(self.blocked_weekdays_local or []):
            value = int(item)
            if value < 0 or value > 6:
                raise ValueError("security.protection.sessions.blocked_weekdays_local must be in [0,6]")
            if value not in cleaned:
                cleaned.append(value)
        self.blocked_weekdays_local = cleaned
        if not list(self.windows or []):
            self.windows = [ProtectionWindowSettings()]
        return self


class ProtectionCadenceSettings(BaseModel):
    enabled: bool = True
    apply_delay_before_submit: bool = True
    min_delay_sec: float = 0.25
    max_delay_sec: float = 1.50
    early_morning_extra_sec: float = 0.20
    midday_extra_sec: float = 0.10
    evening_extra_sec: float = 0.15
    overnight_extra_sec: float = 0.25
    volatility_extra_sec: float = 0.25
    recent_submit_weight_sec: float = 0.10
    jitter_max_sec: float = 0.25

    @model_validator(mode="after")
    def _validate(self) -> "ProtectionCadenceSettings":
        for field_name in [
            'min_delay_sec',
            'max_delay_sec',
            'early_morning_extra_sec',
            'midday_extra_sec',
            'evening_extra_sec',
            'overnight_extra_sec',
            'volatility_extra_sec',
            'recent_submit_weight_sec',
            'jitter_max_sec',
        ]:
            value = float(getattr(self, field_name) or 0.0)
            if value < 0:
                raise ValueError(f"security.protection.cadence.{field_name} must be >= 0")
        if float(self.max_delay_sec) < float(self.min_delay_sec):
            raise ValueError("security.protection.cadence.max_delay_sec must be >= min_delay_sec")
        return self


class ProtectionPacingSettings(BaseModel):
    enabled: bool = True
    min_spacing_global_sec: int = 12
    min_spacing_asset_sec: int = 20
    max_submit_15m_global: int = 2
    max_submit_15m_asset: int = 1
    max_submit_60m_global: int = 4
    max_submit_60m_asset: int = 2
    max_submit_day_global: int = 6
    max_submit_day_asset: int = 3

    @model_validator(mode="after")
    def _validate(self) -> "ProtectionPacingSettings":
        for field_name in [
            'min_spacing_global_sec',
            'min_spacing_asset_sec',
            'max_submit_15m_global',
            'max_submit_15m_asset',
            'max_submit_60m_global',
            'max_submit_60m_asset',
            'max_submit_day_global',
            'max_submit_day_asset',
        ]:
            value = int(getattr(self, field_name) or 0)
            minimum = 0 if field_name.startswith('min_spacing') else 1
            if value < minimum:
                raise ValueError(f"security.protection.pacing.{field_name} must be >= {minimum}")
        return self


class ProtectionCorrelationSettings(BaseModel):
    enabled: bool = True
    block_same_cluster_active: bool = True
    max_active_per_cluster: int = 1
    max_pending_per_cluster: int = 1

    @model_validator(mode="after")
    def _validate(self) -> "ProtectionCorrelationSettings":
        if int(self.max_active_per_cluster) < 1:
            raise ValueError("security.protection.correlation.max_active_per_cluster must be >= 1")
        if int(self.max_pending_per_cluster) < 1:
            raise ValueError("security.protection.correlation.max_pending_per_cluster must be >= 1")
        return self


class ProtectionSettings(BaseModel):
    enabled: bool = False
    live_submit_only: bool = True
    state_path: Path = Path("runs/security/account_protection_state.json")
    decision_log_path: Path = Path("runs/logs/account_protection.jsonl")

    sessions: ProtectionSessionsSettings = Field(default_factory=ProtectionSessionsSettings)
    cadence: ProtectionCadenceSettings = Field(default_factory=ProtectionCadenceSettings)
    pacing: ProtectionPacingSettings = Field(default_factory=ProtectionPacingSettings)
    correlation: ProtectionCorrelationSettings = Field(default_factory=ProtectionCorrelationSettings)


class SecuritySettings(BaseModel):
    enabled: bool = True
    deployment_profile: Literal["local", "ci", "live"] = "local"

    redact_control_artifacts: bool = True
    redact_structured_logs: bool = True
    redact_email: bool = True

    allow_embedded_credentials: bool = False
    live_require_credentials: bool = True
    live_require_external_credentials: bool = False

    secrets_file: Path | None = None
    secrets_file_env_var: str = "THALOR_SECRETS_FILE"
    broker_email_file_env_var: str = "THALOR_BROKER_EMAIL_FILE"
    broker_password_file_env_var: str = "THALOR_BROKER_PASSWORD_FILE"
    audit_on_context_build: bool = True

    guard: SecurityGuardSettings = Field(default_factory=SecurityGuardSettings)
    protection: ProtectionSettings = Field(default_factory=ProtectionSettings)

    @model_validator(mode="after")
    def _validate(self) -> "SecuritySettings":
        if not str(self.secrets_file_env_var).strip():
            raise ValueError("security.secrets_file_env_var must be non-empty")
        if not str(self.broker_email_file_env_var).strip():
            raise ValueError("security.broker_email_file_env_var must be non-empty")
        if not str(self.broker_password_file_env_var).strip():
            raise ValueError("security.broker_password_file_env_var must be non-empty")
        return self


class TelegramAlertingSettings(BaseModel):
    enabled: bool = False
    send_enabled: bool = False

    bot_token: SecretStr | None = None
    chat_id: str | None = None

    bot_token_env_var: str = "THALOR_TELEGRAM_BOT_TOKEN"
    chat_id_env_var: str = "THALOR_TELEGRAM_CHAT_ID"
    bot_token_file_env_var: str = "THALOR_TELEGRAM_BOT_TOKEN_FILE"
    chat_id_file_env_var: str = "THALOR_TELEGRAM_CHAT_ID_FILE"

    timeout_sec: int = 10
    parse_mode: Literal["HTML", "MarkdownV2", "none"] = "HTML"
    outbox_path: Path = Path("runs/alerts/telegram_outbox.jsonl")
    state_path: Path = Path("runs/alerts/telegram_state.json")

    emit_release_summary: bool = True
    emit_security_alerts: bool = True
    emit_precheck_blocked: bool = False
    emit_execution_submit: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "TelegramAlertingSettings":
        if int(self.timeout_sec) < 1:
            raise ValueError("notifications.telegram.timeout_sec must be >= 1")
        for field_name in [
            'bot_token_env_var',
            'chat_id_env_var',
            'bot_token_file_env_var',
            'chat_id_file_env_var',
        ]:
            if not str(getattr(self, field_name) or '').strip():
                raise ValueError(f"notifications.telegram.{field_name} must be non-empty")
        return self


class NotificationsSettings(BaseModel):
    enabled: bool = True
    outbox_dir: Path = Path("runs/alerts")
    history_limit: int = 200
    telegram: TelegramAlertingSettings = Field(default_factory=TelegramAlertingSettings)

    @model_validator(mode="after")
    def _validate(self) -> "NotificationsSettings":
        if int(self.history_limit) < 10:
            raise ValueError("notifications.history_limit must be >= 10")
        return self


class RuntimeOverrides(BaseModel):
    # Autos/runtime overrides (per-cycle). Keep optional.
    threshold: float | None = None
    cpreg_enable: bool | None = None
    cpreg_alpha_start: float | None = None
    cpreg_alpha_end: float | None = None
    cpreg_slot2_mult: float | None = None
    cp_alpha: float | None = None
    cp_bootstrap_fallback: str | None = None
    meta_iso_blend: float | None = None
    regime_mode: Literal["hard", "soft"] | None = None
    payout: float | None = None
    market_open: bool | None = None


class ExecutionStakeSettings(BaseModel):
    amount: float = 2.0
    currency: str = "BRL"

    @model_validator(mode="after")
    def _validate(self) -> "ExecutionStakeSettings":
        if float(self.amount) <= 0:
            raise ValueError("execution.stake.amount must be > 0")
        if not str(self.currency).strip():
            raise ValueError("execution.stake.currency must be non-empty")
        return self


class ExecutionSubmitSettings(BaseModel):
    grace_sec: int = 2
    max_latency_ms: int = 1500
    retry_on_reject: bool = False
    retry_on_timeout: bool = False


class ExecutionReconcileSettings(BaseModel):
    poll_interval_sec: int = 5
    history_lookback_sec: int = 3600
    orphan_lookback_sec: int = 7200
    not_found_grace_sec: int = 20
    settle_grace_sec: int = 30
    scan_without_pending: bool = False


class ExecutionLimitsSettings(BaseModel):
    max_pending_unknown: int = 1
    max_open_positions: int = 1


class ExecutionRealGuardSettings(BaseModel):
    enabled: bool = True
    require_env_allow_real: bool = True
    allow_multi_asset_live: bool = False
    serialize_submits: bool = True
    submit_lock_path: Path = Path("runs/runtime_execution.submit.lock")
    min_submit_spacing_sec: int = 20
    max_pending_unknown_total: int | None = 1
    max_open_positions_total: int | None = 1
    recent_failure_window_sec: int = 300
    max_recent_transport_failures: int = 2
    post_submit_verify_enable: bool = True
    post_submit_verify_timeout_sec: int = 8
    post_submit_verify_poll_sec: float = 0.5

    @model_validator(mode="after")
    def _validate(self) -> "ExecutionRealGuardSettings":
        if int(self.min_submit_spacing_sec) < 0:
            raise ValueError("execution.real_guard.min_submit_spacing_sec must be >= 0")
        if self.max_pending_unknown_total is not None and int(self.max_pending_unknown_total) < 0:
            raise ValueError("execution.real_guard.max_pending_unknown_total must be >= 0 or null")
        if self.max_open_positions_total is not None and int(self.max_open_positions_total) < 0:
            raise ValueError("execution.real_guard.max_open_positions_total must be >= 0 or null")
        if int(self.recent_failure_window_sec) < 0:
            raise ValueError("execution.real_guard.recent_failure_window_sec must be >= 0")
        if int(self.max_recent_transport_failures) < 0:
            raise ValueError("execution.real_guard.max_recent_transport_failures must be >= 0")
        if int(self.post_submit_verify_timeout_sec) < 1:
            raise ValueError("execution.real_guard.post_submit_verify_timeout_sec must be >= 1")
        if float(self.post_submit_verify_poll_sec) <= 0:
            raise ValueError("execution.real_guard.post_submit_verify_poll_sec must be > 0")
        return self


class FakeBrokerSettings(BaseModel):
    state_path: Path = Path("runs/fake_broker_state.json")
    submit_behavior: Literal["ack", "reject", "timeout", "exception"] = "ack"
    settlement: Literal["open", "win", "loss", "refund", "cancelled"] = "open"
    settle_after_sec: int = 0
    create_order_on_timeout: bool = True
    payout: float = 0.80
    heartbeat_ok: bool = True


class ExecutionSettings(BaseModel):
    enabled: bool = False
    mode: Literal["disabled", "paper", "live", "practice"] = "disabled"
    provider: Literal["fake", "iqoption"] = "fake"
    account_mode: Literal["PRACTICE", "REAL"] = "PRACTICE"
    fail_closed: bool = True
    client_order_prefix: str = "thalor"

    stake: ExecutionStakeSettings = Field(default_factory=ExecutionStakeSettings)
    submit: ExecutionSubmitSettings = Field(default_factory=ExecutionSubmitSettings)
    reconcile: ExecutionReconcileSettings = Field(default_factory=ExecutionReconcileSettings)
    limits: ExecutionLimitsSettings = Field(default_factory=ExecutionLimitsSettings)
    real_guard: ExecutionRealGuardSettings = Field(default_factory=ExecutionRealGuardSettings)
    fake: FakeBrokerSettings = Field(default_factory=FakeBrokerSettings)

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> str:
        return normalize_execution_mode(value, default=MODE_DISABLED)

    @model_validator(mode="after")
    def _validate(self) -> "ExecutionSettings":
        self.mode = normalize_execution_mode(self.mode, default=MODE_DISABLED)
        if self.enabled and self.mode == MODE_DISABLED:
            self.mode = "paper"
        if not self.enabled:
            self.mode = MODE_DISABLED
        # `practice` is a safe explicit alias for IQ submit on the PRACTICE
        # balance; never let it drift into REAL implicitly.
        if self.mode == MODE_PRACTICE:
            self.account_mode = "PRACTICE"
        if not str(self.client_order_prefix).strip():
            raise ValueError("execution.client_order_prefix must be non-empty")
        return self


class ThalorConfig(BaseSettings):
    """Root settings.

    Note: YAML ingestion is implemented via a custom source in
    :mod:`natbin.config.sources`. Env vars use the THALOR__ prefix.
    """

    model_config = SettingsConfigDict(
        env_prefix="THALOR__",
        env_nested_delimiter="__",
        case_sensitive=False,
        env_file=None,
        env_file_encoding="utf-8",
        extra="forbid",
        validate_default=True,
    )

    version: str = "2.0"
    config_path: Path = Path("config.yaml")

    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)
    quota: QuotaSettings = Field(default_factory=QuotaSettings)
    autos: AutosSettings = Field(default_factory=AutosSettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    monte_carlo: MonteCarloSettings = Field(default_factory=MonteCarloSettings)
    production: ProductionSettings = Field(default_factory=ProductionSettings)
    failsafe: FailsafeSettings = Field(default_factory=FailsafeSettings)
    multi_asset: MultiAssetSettings = Field(default_factory=MultiAssetSettings)
    intelligence: IntelligenceSettings = Field(default_factory=IntelligenceSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    notifications: NotificationsSettings = Field(default_factory=NotificationsSettings)

    assets: list[AssetSettings] = Field(default_factory=lambda: [AssetSettings(asset="EURUSD-OTC", interval_sec=300)])
    runtime_overrides: RuntimeOverrides = Field(default_factory=RuntimeOverrides)

    @model_validator(mode="after")
    def _validate_assets(self) -> "ThalorConfig":
        seen: set[tuple[str, int]] = set()
        for a in self.assets:
            key = (str(a.asset), int(a.interval_sec))
            if key in seen:
                raise ValueError(f"duplicate asset scope: {key}")
            seen.add(key)
        return self


class ResolvedConfig(BaseModel):
    """Effective config for a specific runtime scope (asset+interval).

    This is what should be used as an immutable input during a runtime cycle.
    """

    version: str
    profile: str
    asset: str
    interval_sec: int
    timezone: str

    broker: BrokerSettings
    data: DataSettings
    decision: DecisionSettings
    quota: QuotaSettings
    autos: AutosSettings
    network: NetworkSettings
    observability: ObservabilitySettings
    dashboard: DashboardSettings
    monte_carlo: MonteCarloSettings
    production: ProductionSettings
    failsafe: FailsafeSettings
    runtime: RuntimeSettings
    multi_asset: MultiAssetSettings
    intelligence: IntelligenceSettings
    execution: ExecutionSettings
    security: SecuritySettings
    notifications: NotificationsSettings

    runtime_overrides: RuntimeOverrides
    resolved_at_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_trace: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")
