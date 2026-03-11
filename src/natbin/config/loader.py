from __future__ import annotations

"""Config loader / resolver.

In Package M v1 this loader focuses on:

* strongly-typed config parsing + validation
* legacy ``config.yaml`` compatibility
* producing an immutable :class:`natbin.config.models.ResolvedConfig`

The runtime still uses environment overrides heavily. We keep those flows by
supporting both the new THALOR__* settings and the old IQ_* scope keys.
"""

from pathlib import Path
from typing import Any, Mapping

from pydantic_settings import EnvSettingsSource, YamlConfigSettingsSource
from pydantic_settings.sources import InitSettingsSource
from pydantic_settings.sources.providers.env import parse_env_vars

from .models import ResolvedConfig, ThalorConfig
from .paths import resolve_config_path, resolve_env_path, resolve_repo_root
from .sources import build_source_trace, compat_env_source, legacy_yaml_to_model_dict, modern_dotenv_env_map


class MappingEnvSettingsSource(EnvSettingsSource):
    """Parse a supplied env mapping using the same rules as real os.environ."""

    def __init__(self, settings_cls, env_mapping: Mapping[str, str | None]):
        self._env_mapping = dict(env_mapping)
        super().__init__(settings_cls)

    def _load_env_vars(self) -> Mapping[str, str | None]:
        return parse_env_vars(self._env_mapping, self.case_sensitive, self.env_ignore_empty, self.env_parse_none_str)



def load_thalor_config(
    *,
    config_path: str | Path | None = None,
    repo_root: str | Path | None = None,
    env_path: str | Path | None = None,
    profile: str | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> ThalorConfig:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    path = resolve_config_path(repo_root=root, config_path=config_path)
    env_file = resolve_env_path(repo_root=root, env_path=env_path, config_path=path)

    class _ThalorYamlSource(YamlConfigSettingsSource):
        def __call__(self) -> dict[str, Any]:  # type: ignore[override]
            raw = super().__call__()
            if not raw:
                return {}
            if isinstance(raw, dict) and ("assets" in raw or "runtime" in raw or "broker" in raw):
                return raw
            if isinstance(raw, dict):
                return legacy_yaml_to_model_dict(raw)
            return {}

    class _Settings(ThalorConfig):
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        ):
            yaml_src = _ThalorYamlSource(settings_cls, yaml_file=path, yaml_file_encoding="utf-8")
            compat_src = InitSettingsSource(settings_cls, init_kwargs=compat_env_source(env_path=env_file))
            dotenv_env_src = MappingEnvSettingsSource(settings_cls, env_mapping=modern_dotenv_env_map(env_path=env_file))
            # Precedence is left-to-right in pydantic-settings. Keep runtime
            # overrides ahead of compatibility sources so THALOR__ wins over
            # IQ_*/ASSET legacy keys, and both win over YAML defaults.
            return (
                init_settings,
                env_settings,
                dotenv_env_src,
                compat_src,
                yaml_src,
                file_secret_settings,
            )

    cfg = _Settings(config_path=path)
    secret_trace: list[str] = []
    try:
        from ..security.secrets import apply_external_secret_overrides

        cfg, secret_trace = apply_external_secret_overrides(cfg, repo_root=root)
    except Exception:
        secret_trace = []
    if profile is not None:
        cfg.runtime.profile = str(profile)
    if cli_overrides:
        allowed = set(cfg.runtime_overrides.model_fields.keys())
        updates = {k: v for k, v in dict(cli_overrides).items() if k in allowed}
        if updates:
            cfg.runtime_overrides = cfg.runtime_overrides.model_copy(update=updates)
    try:
        object.__setattr__(cfg, '_secret_source_trace', list(secret_trace))
    except Exception:
        pass
    return cfg



def load_resolved_config(
    *,
    config_path: str | Path | None = None,
    repo_root: str | Path | None = None,
    env_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    profile: str | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> ResolvedConfig:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    path = resolve_config_path(repo_root=root, config_path=config_path)
    env_file = resolve_env_path(repo_root=root, env_path=env_path, config_path=path)
    cfg = load_thalor_config(config_path=path, repo_root=root, env_path=env_file, profile=profile, cli_overrides=cli_overrides)

    chosen = None
    if asset is not None:
        for a in cfg.assets:
            if str(a.asset) == str(asset) and (interval_sec is None or int(a.interval_sec) == int(interval_sec)):
                chosen = a
                break
        if chosen is None:
            # If explicit override doesn't exist, synthesize from override.
            base = cfg.assets[0]
            chosen = base.model_copy(update={
                "asset": str(asset),
                "interval_sec": int(interval_sec) if interval_sec is not None else int(base.interval_sec),
            })
    else:
        chosen = cfg.assets[0]
        if interval_sec is not None:
            for a in cfg.assets:
                if int(a.interval_sec) == int(interval_sec):
                    chosen = a
                    break

    trace = build_source_trace(config_path=Path(cfg.config_path), env_path=env_file)
    extra_trace = list(getattr(cfg, '_secret_source_trace', []) or [])
    for item in extra_trace:
        if item not in trace:
            trace.append(item)
    return ResolvedConfig(
        version=str(cfg.version),
        profile=str(cfg.runtime.profile),
        asset=str(chosen.asset),
        interval_sec=int(chosen.interval_sec),
        timezone=str(chosen.timezone),
        broker=cfg.broker,
        data=cfg.data,
        decision=cfg.decision,
        quota=cfg.quota,
        autos=cfg.autos,
        observability=cfg.observability,
        failsafe=cfg.failsafe,
        runtime=cfg.runtime,
        multi_asset=cfg.multi_asset,
        intelligence=cfg.intelligence,
        execution=cfg.execution,
        security=cfg.security,
        notifications=cfg.notifications,
        runtime_overrides=cfg.runtime_overrides,
        source_trace=trace,
    )
