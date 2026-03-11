from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional at runtime
    yaml = None


ACK_SENTINEL = "I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT"


@dataclass(frozen=True)
class ValidationSpec:
    name: str
    cmd: list[str]
    required: bool = True
    note: str | None = None
    potentially_submits: bool = False


@dataclass
class ValidationResult:
    name: str
    returncode: int
    duration_sec: float
    started_at_utc: str
    finished_at_utc: str
    cmd: list[str]
    required: bool
    note: str | None
    potentially_submits: bool
    stdout: str
    stderr: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ValidationPlan:
    stage: str
    config_path: str | None
    asset: str | None
    interval_sec: int | None
    specs: list[ValidationSpec]
    manual_checks: list[str]
    dangerous_stage: bool = False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    src_path = repo_root / "src"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([str(src_path), existing]) if existing else str(src_path)
    return env


def _module_cmd(*parts: str) -> list[str]:
    return ["-m", *parts]


def _rt_cmd(*parts: str, repo_root: str = ".", config: str | None = None, as_json: bool = True) -> list[str]:
    out = ["-m", "natbin.runtime_app", "--repo-root", repo_root]
    if config:
        out.extend(["--config", config])
    out.extend(parts)
    if as_json:
        out.append("--json")
    return out


def _parse_config_scope(config_path: Path) -> tuple[str | None, int | None]:
    if not config_path.exists() or yaml is None:
        return None, None
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None, None
    assets = raw.get("assets") or []
    if not isinstance(assets, list) or not assets:
        return None, None
    first = assets[0] or {}
    asset = first.get("asset")
    interval = first.get("interval_sec")
    try:
        return (str(asset) if asset is not None else None, int(interval) if interval is not None else None)
    except Exception:
        return (str(asset) if asset is not None else None, None)


BASELINE_MANUAL = [
    "Confirmar que o ambiente local está em PowerShell 7 + Python 3.12.",
    "Confirmar que o dashboard abre e lê os artefatos em runs/ sem erro.",
    "Confirmar que security/release/incident drill saem limpos antes de tocar no broker live.",
]

PRACTICE_MANUAL = [
    "Confirmar que broker.balance_mode=\"PRACTICE\" e execution.account_mode=\"PRACTICE\".",
    "Confirmar que a stake está mínima e multi_asset continua desabilitado.",
    "Se observe --once não gerar trade, repetir apenas em janela/candle válidos; ausência de sinal não é bug por si só.",
]

REAL_PREFLIGHT_MANUAL = [
    "Confirmar que o drain mode ficou ligado durante todo o estágio real_preflight.",
    "Confirmar que a conta REAL está acessível e saudável, mas sem submit novo.",
    "Só sair desse estágio se release/security/health/incidents estiverem limpos e reconciliados.",
]

REAL_SUBMIT_MANUAL = [
    "Executar este estágio apenas com stake mínima e uma única scope/asset.",
    "Antes do submit real, revisar manualmente orders/reconcile/incidents do estágio anterior.",
    "Após o primeiro ciclo real, religar drain mode e revisar o report JSON antes de ampliar o escopo.",
]


def build_validation_plan(
    *,
    stage: str,
    repo_root: Path,
    config_path: str | None,
    asset: str | None = None,
    interval_sec: int | None = None,
    include_baseline_tests: bool = True,
    force_send_alerts: bool = False,
    allow_live_submit: bool = False,
) -> ValidationPlan:
    stage_key = str(stage or "").strip().lower()
    root_str = str(repo_root)
    cfg = str(config_path) if config_path else None

    if (asset is None or interval_sec is None) and cfg:
        parsed_asset, parsed_interval = _parse_config_scope((repo_root / cfg).resolve())
        asset = asset or parsed_asset
        interval_sec = interval_sec or parsed_interval

    if stage_key == "baseline":
        specs: list[ValidationSpec] = []
        if include_baseline_tests:
            specs.extend(
                [
                    ValidationSpec("selfcheck_repo", ["scripts/tools/selfcheck_repo.py"], note="Repo sanity / imports / file layout"),
                    ValidationSpec("pytest", _module_cmd("pytest", "-q"), note="Unit tests + regressions"),
                    ValidationSpec("smoke_runtime_app", ["scripts/ci/smoke_runtime_app.py"], note="Control plane smoke"),
                    ValidationSpec("smoke_execution_layer", ["scripts/ci/smoke_execution_layer.py"], note="Execution layer smoke"),
                ]
            )
        specs.extend(
            [
                ValidationSpec("runtime_security", _rt_cmd("security", repo_root=root_str), note="Secrets/redaction posture"),
                ValidationSpec("runtime_health", _rt_cmd("health", repo_root=root_str), note="Runtime health snapshot"),
                ValidationSpec("runtime_release", _rt_cmd("release", repo_root=root_str), note="Production checklist"),
                ValidationSpec("incidents_status", _rt_cmd("incidents", "status", repo_root=root_str), note="Incident posture / recent feed"),
                ValidationSpec("incidents_drill", _rt_cmd("incidents", "drill", "--scenario", "broker_down", repo_root=root_str), note="Incident runbook drill"),
                ValidationSpec("alerts_status", _rt_cmd("alerts", "status", repo_root=root_str), required=False, note="Telegram/outbox visibility"),
            ]
        )
        return ValidationPlan(stage=stage_key, config_path=cfg, asset=asset, interval_sec=interval_sec, specs=specs, manual_checks=list(BASELINE_MANUAL), dangerous_stage=False)

    if stage_key not in {"practice", "real_preflight", "real_submit"}:
        raise ValueError(f"unsupported stage: {stage!r}")
    if not cfg:
        raise ValueError(f"stage {stage_key!r} requires --config")
    if not asset or not interval_sec:
        raise ValueError(f"stage {stage_key!r} requires asset/interval either in config or via --asset/--interval-sec")

    common_specs = [
        ValidationSpec("runtime_security", _rt_cmd("security", repo_root=root_str, config=cfg), note="Secrets/redaction posture"),
        ValidationSpec("runtime_health", _rt_cmd("health", repo_root=root_str, config=cfg), note="Runtime health snapshot"),
        ValidationSpec("runtime_release", _rt_cmd("release", repo_root=root_str, config=cfg), note="Production checklist"),
        ValidationSpec(
            "asset_prepare",
            _rt_cmd(
                "asset",
                "prepare",
                "--asset",
                str(asset),
                "--interval-sec",
                str(int(interval_sec)),
                repo_root=root_str,
                config=cfg,
            ),
            note="Collect + dataset + market context",
        ),
        ValidationSpec(
            "runtime_precheck",
            _rt_cmd(
                "precheck",
                "--enforce-market-context",
                repo_root=root_str,
                config=cfg,
            ),
            note="Failsafe + quota + market context gate",
        ),
    ]

    if stage_key == "practice":
        specs = [
            *common_specs,
            ValidationSpec(
                "alerts_test",
                _rt_cmd(
                    "alerts",
                    "test",
                    *( ["--force-send"] if force_send_alerts else [] ),
                    repo_root=root_str,
                    config=cfg,
                ),
                required=False,
                note="Optional Telegram smoke",
            ),
            ValidationSpec(
                "observe_once_practice_live",
                _rt_cmd("observe", "--once", repo_root=root_str, config=cfg, as_json=False),
                note="Controlled practice cycle using the live adapter on PRACTICE",
                potentially_submits=True,
            ),
            ValidationSpec("orders_after_practice", _rt_cmd("orders", "--limit", "10", repo_root=root_str, config=cfg), note="Inspect intents/orders"),
            ValidationSpec("reconcile_after_practice", _rt_cmd("reconcile", repo_root=root_str, config=cfg), note="Force reconciliation"),
            ValidationSpec("incidents_after_practice", _rt_cmd("incidents", "status", "--limit", "20", repo_root=root_str, config=cfg), note="Verify incident posture after practice cycle"),
        ]
        return ValidationPlan(stage=stage_key, config_path=cfg, asset=str(asset), interval_sec=int(interval_sec), specs=specs, manual_checks=list(PRACTICE_MANUAL), dangerous_stage=False)

    if stage_key == "real_preflight":
        specs = [
            ValidationSpec(
                "drain_on",
                _rt_cmd("ops", "drain", "on", "--reason", "controlled_live_validation_real_preflight", repo_root=root_str, config=cfg, as_json=False),
                note="Block new submits while validating the REAL environment",
            ),
            *common_specs,
            ValidationSpec(
                "observe_once_real_drain",
                _rt_cmd("observe", "--once", repo_root=root_str, config=cfg, as_json=False),
                note="Run one REAL-account cycle with drain mode enabled",
                potentially_submits=False,
            ),
            ValidationSpec("orders_after_real_preflight", _rt_cmd("orders", "--limit", "10", repo_root=root_str, config=cfg), note="Inspect intents/orders after preflight"),
            ValidationSpec("reconcile_after_real_preflight", _rt_cmd("reconcile", repo_root=root_str, config=cfg), note="Force reconciliation"),
            ValidationSpec("incidents_after_real_preflight", _rt_cmd("incidents", "status", "--limit", "20", repo_root=root_str, config=cfg), note="Verify incident posture after REAL preflight"),
            ValidationSpec(
                "drain_status",
                _rt_cmd("ops", "drain", "status", repo_root=root_str, config=cfg, as_json=False),
                note="Drain mode should still be on here",
            ),
        ]
        return ValidationPlan(stage=stage_key, config_path=cfg, asset=str(asset), interval_sec=int(interval_sec), specs=specs, manual_checks=list(REAL_PREFLIGHT_MANUAL), dangerous_stage=False)

    if not allow_live_submit:
        raise ValueError("real_submit requires allow_live_submit=True")

    specs = [
        ValidationSpec(
            "drain_off",
            _rt_cmd("ops", "drain", "off", "--reason", "controlled_live_validation_real_submit", repo_root=root_str, config=cfg, as_json=False),
            note="Enable a single controlled submit window",
        ),
        ValidationSpec(
            "killswitch_status",
            _rt_cmd("ops", "killswitch", "status", repo_root=root_str, config=cfg, as_json=False),
            note="Kill-switch must be off before any real submit",
        ),
        *common_specs,
        ValidationSpec(
            "observe_once_real_submit",
            _rt_cmd("observe", "--once", repo_root=root_str, config=cfg, as_json=False),
            note="Single controlled REAL cycle; may place a real order if a candidate qualifies",
            potentially_submits=True,
        ),
        ValidationSpec("orders_after_real_submit", _rt_cmd("orders", "--limit", "10", repo_root=root_str, config=cfg), note="Inspect intents/orders after real cycle"),
        ValidationSpec("reconcile_after_real_submit", _rt_cmd("reconcile", repo_root=root_str, config=cfg), note="Force reconciliation"),
        ValidationSpec("incidents_report", _rt_cmd("incidents", "report", "--limit", "20", repo_root=root_str, config=cfg), note="Persist an incident report after the live attempt"),
        ValidationSpec(
            "drain_reenable",
            _rt_cmd("ops", "drain", "on", "--reason", "controlled_live_validation_post_submit", repo_root=root_str, config=cfg, as_json=False),
            note="Close the submit window immediately after the cycle",
        ),
    ]
    return ValidationPlan(stage=stage_key, config_path=cfg, asset=str(asset), interval_sec=int(interval_sec), specs=specs, manual_checks=list(REAL_SUBMIT_MANUAL), dangerous_stage=True)


def run_validation_step(python_exe: str, spec: ValidationSpec, repo_root: Path, env: dict[str, str]) -> ValidationResult:
    started = utc_now_iso()
    started_perf = time.perf_counter()
    full_cmd = [python_exe, *spec.cmd]
    print(f"\n=== RUN {spec.name} ===")
    print(" ".join(full_cmd))
    proc = subprocess.run(full_cmd, cwd=repo_root, env=env, capture_output=True, text=True, check=False)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    duration = time.perf_counter() - started_perf
    finished = utc_now_iso()
    payload: dict[str, Any] | None = None
    stdout = proc.stdout or ""
    stripped = stdout.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            maybe = json.loads(stripped)
            if isinstance(maybe, dict):
                payload = maybe
        except Exception:
            payload = None
    return ValidationResult(
        name=spec.name,
        returncode=int(proc.returncode),
        duration_sec=round(duration, 3),
        started_at_utc=started,
        finished_at_utc=finished,
        cmd=full_cmd,
        required=bool(spec.required),
        note=spec.note,
        potentially_submits=bool(spec.potentially_submits),
        stdout=stdout,
        stderr=proc.stderr or "",
        payload=payload,
    )


def summarize_results(results: Iterable[ValidationResult]) -> dict[str, int]:
    total = 0
    passed = 0
    failed_required = 0
    failed_optional = 0
    for result in results:
        total += 1
        if result.returncode == 0:
            passed += 1
        elif result.required:
            failed_required += 1
        else:
            failed_optional += 1
    return {
        "total": total,
        "passed": passed,
        "failed_required": failed_required,
        "failed_optional": failed_optional,
    }


def write_validation_report(
    *,
    repo_root: Path,
    plan: ValidationPlan,
    python_exe: str,
    results: list[ValidationResult],
    allow_live_submit: bool,
    ack_live: str | None,
) -> Path:
    out_dir = repo_root / "runs" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"controlled_live_validation_{plan.stage}_{timestamp}.json"
    summary = summarize_results(results)
    payload = {
        "generated_at_utc": utc_now_iso(),
        "stage": plan.stage,
        "config_path": plan.config_path,
        "asset": plan.asset,
        "interval_sec": plan.interval_sec,
        "python_exe": python_exe,
        "allow_live_submit": bool(allow_live_submit),
        "ack_live_ok": bool(ack_live == ACK_SENTINEL),
        "dangerous_stage": bool(plan.dangerous_stage),
        "manual_checks": list(plan.manual_checks),
        "summary": summary,
        "all_required_passed": summary["failed_required"] == 0,
        "results": [asdict(r) for r in results],
    }
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the controlled live validation checklist for Thalor.")
    parser.add_argument("--repo-root", default=".", help="Repository root path.")
    parser.add_argument("--python", dest="python_exe", default=sys.executable, help="Python executable to use.")
    parser.add_argument("--stage", choices=["baseline", "practice", "real_preflight", "real_submit"], required=True, help="Validation stage to run.")
    parser.add_argument("--config", default=None, help="Config YAML to use for practice/real stages.")
    parser.add_argument("--asset", default=None, help="Optional asset override if not resolvable from config.")
    parser.add_argument("--interval-sec", type=int, default=None, help="Optional interval override if not resolvable from config.")
    parser.add_argument("--skip-baseline-tests", action="store_true", help="Skip selfcheck/pytest/smokes during the baseline stage.")
    parser.add_argument("--force-send-alerts", action="store_true", help="For alerts test, actually send instead of queue-only where supported.")
    parser.add_argument("--allow-live-submit", action="store_true", help="Required for the real_submit stage.")
    parser.add_argument("--ack-live", default=None, help=f"Must equal {ACK_SENTINEL!r} for the real_submit stage.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if not (repo_root / "pyproject.toml").exists():
        print(f"[ERROR] invalid repo root: {repo_root}")
        return 2
    if args.stage == "real_submit":
        if not args.allow_live_submit:
            print("[ERROR] real_submit requires --allow-live-submit")
            return 2
        if args.ack_live != ACK_SENTINEL:
            print(f"[ERROR] real_submit requires --ack-live {ACK_SENTINEL}")
            return 2

    try:
        plan = build_validation_plan(
            stage=args.stage,
            repo_root=repo_root,
            config_path=args.config,
            asset=args.asset,
            interval_sec=args.interval_sec,
            include_baseline_tests=not bool(args.skip_baseline_tests),
            force_send_alerts=bool(args.force_send_alerts),
            allow_live_submit=bool(args.allow_live_submit),
        )
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 2

    env = ensure_env(repo_root)
    print("Thalor controlled live validation")
    print(f"repo_root={repo_root}")
    print(f"python={args.python_exe}")
    print(f"stage={plan.stage}")
    print(f"config={plan.config_path}")
    print(f"asset={plan.asset}")
    print(f"interval_sec={plan.interval_sec}")
    print(f"dangerous_stage={plan.dangerous_stage}")
    if plan.manual_checks:
        print("manual_checks=")
        for item in plan.manual_checks:
            print(f"  - {item}")

    results: list[ValidationResult] = []
    for spec in plan.specs:
        result = run_validation_step(args.python_exe, spec, repo_root, env)
        results.append(result)
        status = "OK" if result.returncode == 0 else ("WARN" if not result.required else "FAIL")
        print(f"[{status}] {result.name} ({result.duration_sec:.3f}s)")
        if result.returncode != 0 and result.required:
            print("Stopping early because a required validation step failed.")
            break

    report_path = write_validation_report(
        repo_root=repo_root,
        plan=plan,
        python_exe=args.python_exe,
        results=results,
        allow_live_submit=bool(args.allow_live_submit),
        ack_live=args.ack_live,
    )
    summary = summarize_results(results)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"report={report_path}")
    return 1 if summary["failed_required"] else 0


__all__ = [
    "ACK_SENTINEL",
    "ValidationPlan",
    "ValidationResult",
    "ValidationSpec",
    "build_validation_plan",
    "ensure_env",
    "main",
    "run_validation_step",
    "summarize_results",
    "write_validation_report",
]
