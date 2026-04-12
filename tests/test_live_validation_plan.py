from __future__ import annotations

from pathlib import Path

from natbin.ops.live_validation import ACK_SENTINEL, build_validation_plan


def _write_cfg(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                'version: "2.0"',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
            ]
        ),
        encoding="utf-8",
    )


def test_build_baseline_plan(tmp_path: Path) -> None:
    plan = build_validation_plan(stage="baseline", repo_root=tmp_path, config_path=None)
    names = [spec.name for spec in plan.specs]
    assert plan.stage == "baseline"
    assert "selfcheck_repo" in names
    assert "runtime_release" in names
    assert "runtime_doctor" in names
    release = next(spec for spec in plan.specs if spec.name == 'runtime_release')
    assert release.required is False
    assert plan.dangerous_stage is False


def test_build_practice_plan_parses_scope_from_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg)
    plan = build_validation_plan(stage="practice", repo_root=tmp_path, config_path="config.yaml")
    names = [spec.name for spec in plan.specs]
    assert plan.asset == "EURUSD-OTC"
    assert plan.interval_sec == 300
    assert "runtime_practice_readiness" in names
    assert "observe_once_practice_live" in names
    assert plan.dangerous_stage is False


def test_real_submit_requires_opt_in(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg)
    try:
        build_validation_plan(stage="real_submit", repo_root=tmp_path, config_path="config.yaml")
    except ValueError as exc:
        assert "allow_live_submit" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_ack_sentinel_constant() -> None:
    assert ACK_SENTINEL == "I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT"
