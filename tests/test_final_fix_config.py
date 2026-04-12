from __future__ import annotations

from pathlib import Path

from natbin.config.loader import load_thalor_config


def test_load_thalor_config_supports_extends(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "base.yaml").write_text(
        """
version: "2.0"
runtime:
  profile: default
nbroker:
  provider: iqoption
execution:
  enabled: false
  mode: disabled
assets:
  - asset: EURUSD-OTC
    interval_sec: 300
    timezone: America/Sao_Paulo
""".replace("nbroker", "broker"),
        encoding="utf-8",
    )
    profile = cfg_dir / "multi.yaml"
    profile.write_text(
        """
extends: base.yaml
runtime:
  profile: multi
execution:
  enabled: true
  mode: paper
multi_asset:
  enabled: true
  max_parallel_assets: 2
assets:
  - asset: GBPUSD-OTC
    interval_sec: 300
    timezone: America/Sao_Paulo
""",
        encoding="utf-8",
    )

    cfg = load_thalor_config(repo_root=tmp_path, config_path=profile)

    assert cfg.runtime.profile == "multi"
    assert cfg.execution.enabled is True
    assert cfg.execution.mode == "paper"
    assert cfg.multi_asset.enabled is True
    assert cfg.broker.provider == "iqoption"
    assert len(cfg.assets) == 1
    assert cfg.assets[0].asset == "GBPUSD-OTC"


def test_dotenv_only_applies_safe_keys_by_default(tmp_path: Path, monkeypatch) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "base.yaml").write_text(
        """
version: "2.0"
runtime:
  profile: default
broker:
  provider: iqoption
  balance_mode: PRACTICE
execution:
  enabled: false
  mode: disabled
assets:
  - asset: EURUSD-OTC
    interval_sec: 300
    timezone: America/Sao_Paulo
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "THALOR__EXECUTION__ENABLED=1",
                "THALOR__EXECUTION__MODE=paper",
                "THALOR__BROKER__BALANCE_MODE=REAL",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("THALOR__EXECUTION__ENABLED", raising=False)
    monkeypatch.delenv("THALOR__EXECUTION__MODE", raising=False)
    monkeypatch.delenv("THALOR__BROKER__BALANCE_MODE", raising=False)
    monkeypatch.delenv("THALOR_DOTENV_ALLOW_BEHAVIOR", raising=False)

    cfg = load_thalor_config(repo_root=tmp_path, config_path=cfg_dir / "base.yaml")

    assert cfg.execution.enabled is False
    assert cfg.execution.mode == "disabled"
    assert cfg.broker.balance_mode == "REAL"


def test_process_env_still_overrides_behavior(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "base.yaml").write_text(
        """
version: "2.0"
runtime:
  profile: default
broker:
  provider: iqoption
execution:
  enabled: false
  mode: disabled
assets:
  - asset: EURUSD-OTC
    interval_sec: 300
    timezone: America/Sao_Paulo
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("THALOR__EXECUTION__ENABLED", "1")
    monkeypatch.setenv("THALOR__EXECUTION__MODE", "paper")

    cfg = load_thalor_config(repo_root=tmp_path, config_path=cfg_dir / "base.yaml")

    assert cfg.execution.enabled is True
    assert cfg.execution.mode == "paper"


def test_dotenv_behavior_can_be_reenabled_explicitly(tmp_path: Path, monkeypatch) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "base.yaml").write_text(
        """
version: "2.0"
runtime:
  profile: default
execution:
  enabled: false
  mode: disabled
assets:
  - asset: EURUSD-OTC
    interval_sec: 300
    timezone: America/Sao_Paulo
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "THALOR__EXECUTION__ENABLED=1\nTHALOR__EXECUTION__MODE=paper\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("THALOR__EXECUTION__ENABLED", raising=False)
    monkeypatch.delenv("THALOR__EXECUTION__MODE", raising=False)
    monkeypatch.setenv("THALOR_DOTENV_ALLOW_BEHAVIOR", "1")

    cfg = load_thalor_config(repo_root=tmp_path, config_path=cfg_dir / "base.yaml")

    assert cfg.execution.enabled is True
    assert cfg.execution.mode == "paper"


def test_load_resolved_config_source_trace_includes_extends_chain(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "base.yaml").write_text(
        """
version: "2.0"
broker:
  provider: iqoption
assets:
  - asset: EURUSD-OTC
    interval_sec: 300
    timezone: UTC
""",
        encoding="utf-8",
    )
    child = cfg_dir / "practice.yaml"
    child.write_text(
        """
extends: base.yaml
execution:
  enabled: false
  mode: disabled
assets:
  - asset: GBPUSD-OTC
    interval_sec: 300
    timezone: UTC
""",
        encoding="utf-8",
    )

    from natbin.config.loader import load_resolved_config

    cfg = load_resolved_config(repo_root=tmp_path, config_path=child)

    assert f"yaml:{(cfg_dir / 'base.yaml').resolve().as_posix()}" in cfg.source_trace
    assert f"yaml:{child.resolve().as_posix()}" in cfg.source_trace
