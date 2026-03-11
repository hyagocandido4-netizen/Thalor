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
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, computed_field, model_validator
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


class DecisionSettings(BaseModel):
    # Requested gating mode for scoring. The scorer may report a more specific
    # gate_used (e.g. cp_meta_iso), but config should stay stable.
    gate_mode: str = "cp"
    meta_model: str = "hgb"
    thresh_on: str = "ev"
    threshold: float = 0.02

    # Optional model registry / tuning pointer for live runtime.
    # When set, this is recorded into decision snapshots for traceability.
    tune_dir: str = ""
    # Optional regime bounds used by make_regime_mask (keys: vol_lo/hi, bb_lo/hi, atr_lo/hi).
    bounds: dict[str, float] = Field(default_factory=dict)

    rolling_minutes: int = 360
    min_gap_minutes: int = 30
    pacing_enable: bool = True

    fail_closed: bool = True


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


class ObservabilitySettings(BaseModel):
    status_enable: bool = True
    metrics_enable: bool = False
    metrics_bind: str = "127.0.0.1:9108"

    # Package P: structured JSONL logs (append-only) for ingestion.
    structured_logs_enable: bool = True
    structured_logs_path: Path = Path("runs/logs/runtime_structured.jsonl")

    loop_log_enable: bool = True
    loop_log_dir: Path = Path("runs/logs")
    loop_log_retention_days: int = 14

    incidents_enable: bool = True
    decision_snapshots_enable: bool = True


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

    # Portfolio selection limits
    portfolio_topk_total: int = 6
    portfolio_hard_max_positions: int = 6
    portfolio_hard_max_trades_per_day: int | None = None
    portfolio_hard_max_pending_unknown_total: int | None = 1

    # Exposure caps (cross-asset / cross-interval)
    portfolio_hard_max_positions_per_asset: int | None = 1
    portfolio_hard_max_positions_per_cluster: int | None = 1

    # Correlation-aware suppression uses cluster_key as the correlation group.
    correlation_filter_enable: bool = True
    max_trades_per_cluster_per_cycle: int = 1

    # Safe partitioning of per-asset data paths (recommended for parallel runs)
    partition_data_paths: bool = True
    data_db_template: str = "data/market_{scope_tag}.sqlite3"
    dataset_path_template: str = "data/datasets/{scope_tag}/dataset.csv"




class IntelligenceSettings(BaseModel):
    enabled: bool = True
    artifact_dir: Path = Path("runs/intelligence")

    # P18 — slot-aware tuning
    slot_aware_enable: bool = True
    slot_aware_min_trades: int = 6
    slot_aware_prior_weight: float = 8.0
    slot_aware_multiplier_min: float = 0.85
    slot_aware_multiplier_max: float = 1.15

    # P19 — learned gating / stacking
    learned_gating_enable: bool = True
    learned_gating_min_rows: int = 50
    learned_gating_weight: float = 0.60

    # P20 — drift / regime monitor
    drift_monitor_enable: bool = True
    drift_recent_limit: int = 200
    drift_warn_psi: float = 0.15
    drift_block_psi: float = 0.30
    drift_fail_closed: bool = False
    retrain_warn_streak: int = 3
    retrain_block_streak: int = 1

    # P21 — coverage regulator 2.0
    coverage_regulator_enable: bool = True
    coverage_target_trades_per_day: float | None = None
    coverage_tolerance: float = 0.50
    coverage_bias_weight: float = 0.04

    # P22 — anti-overfitting guard
    anti_overfit_enable: bool = True
    anti_overfit_fail_closed: bool = False
    anti_overfit_min_robustness: float = 0.50

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
        if int(self.learned_gating_min_rows) < 10:
            raise ValueError("intelligence.learned_gating_min_rows must be >= 10")
        if not (0.0 <= float(self.learned_gating_weight) <= 1.0):
            raise ValueError("intelligence.learned_gating_weight must be within [0,1]")
        if int(self.drift_recent_limit) < 20:
            raise ValueError("intelligence.drift_recent_limit must be >= 20")
        if float(self.drift_warn_psi) <= 0:
            raise ValueError("intelligence.drift_warn_psi must be > 0")
        if float(self.drift_block_psi) < float(self.drift_warn_psi):
            raise ValueError("intelligence.drift_block_psi must be >= intelligence.drift_warn_psi")
        if int(self.retrain_warn_streak) < 1:
            raise ValueError("intelligence.retrain_warn_streak must be >= 1")
        if int(self.retrain_block_streak) < 1:
            raise ValueError("intelligence.retrain_block_streak must be >= 1")
        if self.coverage_target_trades_per_day is not None and float(self.coverage_target_trades_per_day) <= 0:
            raise ValueError("intelligence.coverage_target_trades_per_day must be > 0 when set")
        if float(self.coverage_tolerance) < 0:
            raise ValueError("intelligence.coverage_tolerance must be >= 0")
        if float(self.coverage_bias_weight) < 0:
            raise ValueError("intelligence.coverage_bias_weight must be >= 0")
        if not (0.0 <= float(self.anti_overfit_min_robustness) <= 1.0):
            raise ValueError("intelligence.anti_overfit_min_robustness must be within [0,1]")
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
    cpreg_alpha_start: float | None = None
    cpreg_alpha_end: float | None = None
    cp_alpha: float | None = None
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


class ExecutionLimitsSettings(BaseModel):
    max_pending_unknown: int = 1
    max_open_positions: int = 1


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
    mode: Literal["disabled", "paper", "live"] = "disabled"
    provider: Literal["fake", "iqoption"] = "fake"
    account_mode: Literal["PRACTICE", "REAL"] = "PRACTICE"
    fail_closed: bool = True
    client_order_prefix: str = "thalor"

    stake: ExecutionStakeSettings = Field(default_factory=ExecutionStakeSettings)
    submit: ExecutionSubmitSettings = Field(default_factory=ExecutionSubmitSettings)
    reconcile: ExecutionReconcileSettings = Field(default_factory=ExecutionReconcileSettings)
    limits: ExecutionLimitsSettings = Field(default_factory=ExecutionLimitsSettings)
    fake: FakeBrokerSettings = Field(default_factory=FakeBrokerSettings)

    @model_validator(mode="after")
    def _validate(self) -> "ExecutionSettings":
        if self.enabled and self.mode == "disabled":
            self.mode = "paper"
        if not self.enabled:
            self.mode = "disabled"
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
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
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
    observability: ObservabilitySettings
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
