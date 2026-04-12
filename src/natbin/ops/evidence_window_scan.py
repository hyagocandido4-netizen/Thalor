from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from ..ops.production_doctor import build_production_doctor_payload
from ..ops.provider_probe import build_provider_probe_payload
from .diagnostic_utils import dedupe_actions, load_selected_scopes

_TRADE_ACTIONS = {"CALL", "PUT", "BUY", "SELL", "UP", "DOWN"}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _scope_key(scope: Mapping[str, Any] | None) -> str:
    if not isinstance(scope, Mapping):
        return ""
    return str(scope.get("scope_tag") or "")


def _market_open_from_probe(scope_probe: Mapping[str, Any]) -> bool | None:
    local = dict(scope_probe.get("local_market_context") or {})
    if "market_open" in local:
        return bool(local.get("market_open")) if local.get("market_open") is not None else None
    remote = dict(scope_probe.get("remote_market_context") or {})
    if remote.get("ok") and remote.get("market_open") is not None:
        return bool(remote.get("market_open"))
    return None


def _bundle_cmd(repo: Path, cfg_path: Path) -> str:
    try:
        rel = str(cfg_path.resolve().relative_to(repo.resolve()))
    except Exception:
        rel = str(cfg_path)
    return f'.\\scripts\\tools\\capture_portfolio_canary_bundle.cmd --config {rel}'


def _candidate_cmd(repo: Path, cfg_path: Path, *, asset: str, interval_sec: int) -> str:
    return (
        f'python -m natbin.runtime_app --repo-root "{repo}" --config "{cfg_path}" '
        f'asset candidate --asset "{asset}" --interval-sec {int(interval_sec)} --json'
    )


def _prepare_cmd(repo: Path, cfg_path: Path, *, asset: str, interval_sec: int) -> str:
    return (
        f'python -m natbin.runtime_app --repo-root "{repo}" --config "{cfg_path}" '
        f'asset prepare --asset "{asset}" --interval-sec {int(interval_sec)} --json'
    )


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



def _read_repo_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _age_sec_from_iso(value: Any) -> float | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    age = (datetime.now(tz=UTC) - dt.astimezone(UTC)).total_seconds()
    return round(max(0.0, age), 3)

def _score_scope(
    *,
    repo: Path,
    cfg_path: Path,
    scope: Mapping[str, Any],
    doctor: Mapping[str, Any],
    probe: Mapping[str, Any],
    board: Mapping[str, Any],
    intel: Mapping[str, Any],
) -> dict[str, Any]:
    scope_tag = str(scope.get("scope_tag") or "")
    asset = str(scope.get("asset") or "")
    interval_sec = int(scope.get("interval_sec") or 0)

    provider_ready = bool(
        ((probe.get("shared_provider_session") or {}).get("ok"))
        and bool((probe.get("remote_candles") or {}).get("ok") or not (probe.get("remote_candles") or {}).get("attempted"))
        and bool((probe.get("remote_market_context") or {}).get("ok") or not (probe.get("remote_market_context") or {}).get("attempted"))
    ) or bool(probe.get("ok"))
    ready_for_cycle = bool(doctor.get("ready_for_cycle"))
    ready_for_practice = bool(doctor.get("ready_for_practice"))
    market_open = _market_open_from_probe(probe)
    local_market = dict(probe.get("local_market_context") or {})
    remote_market = dict(probe.get("remote_market_context") or {})
    remote_candles = dict(probe.get("remote_candles") or {})

    budget_left = _safe_int(board.get("budget_left"))
    pending_unknown = _safe_int(board.get("pending_unknown"))
    open_positions = _safe_int(board.get("open_positions"))
    latest_action = str(board.get("latest_action") or "").upper()
    feedback_blocked = bool(intel.get("feedback_blocked"))
    feedback_reason = str(intel.get("feedback_reason") or "")
    portfolio_score = _safe_float(intel.get("portfolio_score"))
    intelligence_score = _safe_float(intel.get("intelligence_score"))

    blockers = list(doctor.get("blockers") or [])
    warnings = list(doctor.get("warnings") or [])
    provider_checks = list(probe.get("checks") or [])
    provider_errors = [str(item.get("name") or "provider") for item in provider_checks if str(item.get("status")) == "error"]
    provider_warns = [str(item.get("name") or "provider") for item in provider_checks if str(item.get("status")) == "warn"]

    score = 50.0
    reasons: list[str] = []
    if provider_ready:
        score += 24.0
        reasons.append("provider_ready")
    else:
        score -= 34.0
        reasons.append("provider_not_ready")

    if ready_for_cycle:
        score += 18.0
        reasons.append("ready_for_cycle")
    else:
        score -= 20.0
        reasons.append("not_ready_for_cycle")

    if local_market.get("fresh"):
        score += 8.0
        reasons.append("market_context_fresh")
    else:
        score -= 8.0
        reasons.append("market_context_stale")

    if market_open is True:
        score += 12.0
        reasons.append("market_open")
    elif market_open is False:
        score -= 12.0
        reasons.append("market_closed")

    if remote_market.get("ok"):
        score += 6.0
    if remote_candles.get("ok"):
        score += 4.0

    if budget_left is not None:
        if budget_left > 0:
            score += 5.0
            reasons.append("quota_available")
        else:
            score -= 15.0
            reasons.append("quota_exhausted")
    if pending_unknown is not None and pending_unknown > 0:
        score -= 20.0
        reasons.append("pending_unknown")
    if open_positions is not None and open_positions > 0:
        score -= 20.0
        reasons.append("open_positions")

    if latest_action in _TRADE_ACTIONS:
        score += 8.0
        reasons.append(f"candidate_action:{latest_action}")
    elif latest_action == "HOLD":
        score -= 2.0
        reasons.append("candidate_hold")

    if feedback_blocked:
        normalized = feedback_reason.strip().lower().replace("portfolio_feedback_block:", "")
        if normalized == "regime_block":
            score -= 12.0
            reasons.append("regime_block")
        else:
            score -= 18.0
            reasons.append("portfolio_feedback_blocked")

    if portfolio_score is not None:
        score += max(-4.0, min(8.0, portfolio_score * 10.0))
    if intelligence_score is not None:
        score += max(-4.0, min(8.0, intelligence_score * 10.0))

    score -= min(36.0, 12.0 * len(blockers))
    score -= min(16.0, 4.0 * len(warnings))
    score -= min(20.0, 10.0 * len(provider_errors))
    score -= min(12.0, 3.0 * len(provider_warns))
    score = round(max(0.0, min(100.0, score)), 1)

    hard_block = bool(blockers) or bool(provider_errors) or not provider_ready or (pending_unknown or 0) > 0 or (open_positions or 0) > 0
    if hard_block:
        window_state = "hold"
        recommended_action = "resolve_blockers"
    elif feedback_blocked and feedback_reason.strip().lower().replace("portfolio_feedback_block:", "") == "regime_block":
        window_state = "watch"
        recommended_action = "hold_regime_block"
    elif market_open is False:
        window_state = "watch"
        recommended_action = "wait_market_open"
    elif latest_action in _TRADE_ACTIONS and ready_for_cycle and provider_ready and (budget_left or 0) > 0:
        window_state = "ready"
        recommended_action = "run_safe_candidate_capture"
    else:
        window_state = "watch"
        recommended_action = "wait_signal_and_rescan"

    actions = []
    if hard_block:
        actions.extend(str(item) for item in list(doctor.get("actions") or []))
        actions.extend(str(item) for item in list(probe.get("actions") or []))
    elif recommended_action == "wait_market_open":
        actions.append("Mercado fechado para o scope no snapshot atual; aguarde a próxima janela e rode o scan novamente.")
    elif recommended_action == "hold_regime_block":
        actions.append("O scope está saudável, mas o portfolio_feedback bloqueou o trade por regime atual; trate como no-trade operacional.")
    elif recommended_action == "run_safe_candidate_capture":
        actions.append("Rode o asset candidate do scope recomendado para capturar uma decisão segura (execution_disabled).")
    else:
        actions.append("Nenhum blocker duro detectado; aguarde um novo candle/sinal e rode o scan novamente.")

    return {
        "scope": {
            "asset": asset,
            "interval_sec": interval_sec,
            "scope_tag": scope_tag,
        },
        "score": score,
        "window_state": window_state,
        "recommended_action": recommended_action,
        "provider_ready": provider_ready,
        "ready_for_cycle": ready_for_cycle,
        "ready_for_practice": ready_for_practice,
        "market_open": market_open,
        "market_context_fresh": bool(local_market.get("fresh")),
        "latest_action": latest_action or None,
        "budget_left": budget_left,
        "pending_unknown": pending_unknown,
        "open_positions": open_positions,
        "feedback_blocked": feedback_blocked,
        "feedback_reason": feedback_reason or None,
        "portfolio_score": portfolio_score,
        "intelligence_score": intelligence_score,
        "doctor": {
            "severity": doctor.get("severity"),
            "blockers": blockers,
            "warnings": warnings,
            "ready_for_cycle": bool(doctor.get("ready_for_cycle")),
            "ready_for_practice": bool(doctor.get("ready_for_practice")),
        },
        "provider_probe": {
            "severity": probe.get("severity"),
            "shared_provider_session": probe.get("shared_provider_session"),
            "local_market_context": local_market,
            "remote_candles": remote_candles,
            "remote_market_context": remote_market,
            "provider_errors": provider_errors,
            "provider_warnings": provider_warns,
        },
        "portfolio": {
            "board": dict(board or {}),
            "intelligence": dict(intel or {}),
        },
        "reason_trace": reasons,
        "actions": dedupe_actions(actions),
        "commands": {
            "capture_bundle": _bundle_cmd(repo, cfg_path),
            "asset_prepare": _prepare_cmd(repo, cfg_path, asset=asset, interval_sec=interval_sec),
            "asset_candidate": _candidate_cmd(repo, cfg_path, asset=asset, interval_sec=interval_sec),
        },
    }


def build_evidence_window_scan_payload(
    *,
    repo_root: str | Path = ".",
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = True,
    active_provider_probe: bool = True,
    sample_candles: int = 3,
    market_context_max_age_sec: int | None = None,
    min_dataset_rows: int = 100,
    top_n: int = 3,
    write_artifact: bool = True,
) -> dict[str, Any]:
    from ..control.commands import portfolio_status_payload

    repo, cfg_path, cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    if not scopes:
        return {
            "kind": "evidence_window_scan",
            "at_utc": _now_iso(),
            "ok": False,
            "severity": "error",
            "repo_root": str(repo),
            "config_path": str(cfg_path),
            "message": "no_scopes_selected",
            "scope_results": [],
        }

    portfolio = portfolio_status_payload(repo_root=repo, config_path=cfg_path)
    board_map = {
        str(item.get("scope_tag") or ""): dict(item)
        for item in list(portfolio.get("asset_board") or [])
        if isinstance(item, Mapping)
    }
    intelligence_items = (((portfolio.get("intelligence") or {}).get("items")) or []) if isinstance(portfolio.get("intelligence"), Mapping) else []
    intel_map = {
        str(item.get("scope_tag") or ""): dict(item)
        for item in list(intelligence_items)
        if isinstance(item, Mapping)
    }

    provider_stability = _read_repo_json(repo / 'runs' / 'control' / '_repo' / 'provider_stability.json') or {}
    provider_governor = _read_repo_json(repo / 'runs' / 'control' / '_repo' / 'provider_session_governor.json') or {}
    provider_artifact = _read_repo_json(repo / 'runs' / 'control' / '_repo' / 'provider_probe.json') or {}
    provider_requested_active = bool(active_provider_probe)
    provider_effective_active = provider_requested_active
    provider_strategy = 'active' if provider_requested_active else 'passive'
    provider_artifact_age_sec = _age_sec_from_iso(provider_artifact.get('at_utc')) if isinstance(provider_artifact, Mapping) else None
    provider_artifact_fresh = provider_artifact_age_sec is not None and provider_artifact_age_sec <= max(300, int(market_context_max_age_sec or 900))
    provider_artifact_scope_count = 0
    if isinstance(provider_artifact, Mapping):
        provider_artifact_scope_count = max(
            int(((provider_artifact.get('summary') or {}).get('scope_count') or 0)),
            len(list(provider_artifact.get('scope_results') or [])),
        )
    governor_cfg = dict((provider_governor.get('governor') or {})) if isinstance(provider_governor, Mapping) else {}
    governor_summary = dict((provider_governor.get('summary') or {})) if isinstance(provider_governor, Mapping) else {}
    stability_state = str(governor_summary.get('stability_state') or provider_stability.get('stability_state') or '').strip().lower()
    if provider_requested_active and bool(governor_cfg.get('prefer_cached_provider_artifacts')) and stability_state in {'degraded', 'unstable'}:
        if isinstance(provider_artifact, Mapping) and provider_artifact_scope_count >= len(scopes) and provider_artifact_fresh:
            provider = dict(provider_artifact)
            provider_strategy = 'artifact_cached_due_governor'
            provider_effective_active = False
        else:
            provider = build_provider_probe_payload(
                repo_root=repo,
                config_path=cfg_path,
                asset=asset,
                interval_sec=interval_sec,
                all_scopes=all_scopes,
                active=False,
                sample_candles=int(sample_candles),
                probe_market_context=True,
                market_context_max_age_sec=market_context_max_age_sec,
                write_artifact=False,
            )
            provider_strategy = 'passive_due_governor'
            provider_effective_active = False
    else:
        provider = build_provider_probe_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            active=provider_requested_active,
            sample_candles=int(sample_candles),
            probe_market_context=True,
            market_context_max_age_sec=market_context_max_age_sec,
            write_artifact=False,
        )
    provider_map = {
        _scope_key(item.get("scope") if isinstance(item, Mapping) else {}): dict(item)
        for item in list(provider.get("scope_results") or [])
        if isinstance(item, Mapping)
    }

    scope_results: list[dict[str, Any]] = []
    provider_ready_count = 0
    watch_count = 0
    ready_count = 0
    for scope_obj in scopes:
        scope = {
            "asset": str(scope_obj.asset),
            "interval_sec": int(scope_obj.interval_sec),
            "scope_tag": str(scope_obj.scope_tag),
        }
        doctor = build_production_doctor_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=scope["asset"],
            interval_sec=int(scope["interval_sec"]),
            probe_broker=False,
            strict_runtime_artifacts=True,
            market_context_max_age_sec=market_context_max_age_sec,
            min_dataset_rows=int(min_dataset_rows),
            heal_breaker=False,
            breaker_stale_after_sec=None,
            heal_market_context=False,
            heal_control_freshness=False,
            write_artifact=False,
        )
        probe_scope = provider_map.get(scope["scope_tag"]) or {
            "scope": scope,
            "severity": "error",
            "ok": False,
            "checks": [{"name": "provider_probe_missing", "status": "error", "message": "provider probe ausente para o scope"}],
            "actions": ["Rode provider-probe --all-scopes para este profile."],
            "shared_provider_session": {"attempted": False, "ok": False, "reason": "missing"},
            "local_market_context": {},
            "remote_candles": {"attempted": False, "ok": False, "reason": "missing"},
            "remote_market_context": {"attempted": False, "ok": False, "reason": "missing"},
        }
        scored = _score_scope(
            repo=repo,
            cfg_path=cfg_path,
            scope=scope,
            doctor=doctor,
            probe=probe_scope,
            board=board_map.get(scope["scope_tag"], {}),
            intel=intel_map.get(scope["scope_tag"], {}),
        )
        provider_ready_count += int(bool(scored.get("provider_ready")))
        ready_count += int(str(scored.get("window_state")) == "ready")
        watch_count += int(str(scored.get("window_state")) == "watch")
        scope_results.append(scored)

    scope_results.sort(key=lambda item: (-float(item.get("score") or 0.0), str(((item.get("scope") or {}).get("scope_tag") or ""))))
    best = dict(scope_results[0]) if scope_results else None
    recommended_scope = None
    if best is not None:
        recommended_scope = {
            "scope": dict(best.get("scope") or {}),
            "score": best.get("score"),
            "window_state": best.get("window_state"),
            "recommended_action": best.get("recommended_action"),
            "commands": dict(best.get("commands") or {}),
        }

    multi = getattr(cfg, "multi_asset", None)
    execution = getattr(cfg, "execution", None)
    broker = getattr(cfg, "broker", None)
    stake = getattr(execution, "stake", None)
    limits = getattr(execution, "limits", None)
    canary_contract = {
        "multi_asset_enabled": bool(getattr(multi, "enabled", False)),
        "max_parallel_assets": int(getattr(multi, "max_parallel_assets", 1) or 1),
        "portfolio_topk_total": int(getattr(multi, "portfolio_topk_total", 1) or 1),
        "portfolio_hard_max_positions": int(getattr(multi, "portfolio_hard_max_positions", 1) or 1),
        "execution_account_mode": str(getattr(execution, "account_mode", "PRACTICE") or "PRACTICE").upper(),
        "broker_balance_mode": str(getattr(broker, "balance_mode", "PRACTICE") or "PRACTICE").upper(),
        "stake_amount": _safe_float(getattr(stake, "amount", None)),
        "max_pending_unknown": _safe_int(getattr(limits, "max_pending_unknown", None)),
        "max_open_positions": _safe_int(getattr(limits, "max_open_positions", None)),
    }
    canary_contract["ok"] = bool(
        canary_contract["multi_asset_enabled"]
        and int(canary_contract["max_parallel_assets"] or 0) == 1
        and int(canary_contract["portfolio_topk_total"] or 0) == 1
        and int(canary_contract["portfolio_hard_max_positions"] or 0) == 1
        and str(canary_contract["execution_account_mode"]) == "PRACTICE"
        and str(canary_contract["broker_balance_mode"]) == "PRACTICE"
        and (canary_contract["stake_amount"] is None or float(canary_contract["stake_amount"]) <= 5.0)
        and (canary_contract["max_pending_unknown"] is None or int(canary_contract["max_pending_unknown"]) <= 1)
        and (canary_contract["max_open_positions"] is None or int(canary_contract["max_open_positions"]) <= 1)
    )

    summary = {
        "scope_count": len(scope_results),
        "provider_ready_scopes": provider_ready_count,
        "ready_scopes": ready_count,
        "watch_scopes": watch_count,
        "hold_scopes": int(len(scope_results) - ready_count - watch_count),
        "best_scope_tag": ((best or {}).get("scope") or {}).get("scope_tag") if best else None,
        "best_score": (best or {}).get("score") if best else None,
        "multi_asset_enabled": bool(getattr(multi, "enabled", False)),
        "max_parallel_assets": int(getattr(multi, "max_parallel_assets", 1) or 1),
        "portfolio_topk_total": int(getattr(multi, "portfolio_topk_total", 1) or 1),
        "all_scopes": bool(all_scopes),
    }
    if ready_count > 0:
        severity = "ok"
    elif watch_count > 0:
        severity = "warn"
    elif provider_ready_count > 0 or bool(canary_contract["ok"]):
        # A canary scan is still informative when every scope is on HOLD.
        # Treat this as advisory instead of a command failure so operators can
        # distinguish "no good window now" from "scanner broke".
        severity = "warn"
    else:
        severity = "error"

    actions: list[str] = []
    if not bool(canary_contract["ok"]):
        actions.append("O profile atual não está no envelope de portfolio canary recomendado (multi_asset on + top1 + 1 posição + PRACTICE).")
    if best is not None and str(best.get("window_state")) == "ready":
        actions.append("Há pelo menos um scope elegível para captura segura de evidência; rode o asset candidate do scope recomendado.")
    elif best is not None and str(best.get("recommended_action")) == "hold_regime_block":
        actions.append("O melhor scope está saudável, mas bloqueado por regime atual; aguarde mudança de regime em vez de forçar entrada.")
    elif best is not None and str(best.get("recommended_action")) == "wait_market_open":
        actions.append("O melhor scope ainda está com mercado fechado; aguarde abertura/local cache convergir antes de capturar evidência.")
    elif best is not None:
        actions.extend(list(best.get("actions") or []))
    if provider_strategy == 'artifact_cached_due_governor':
        actions.append('Evidence scan usou provider_probe em cache por orientação do governor; isso reduz fan-out quando o provider está degradado.')
    elif provider_strategy == 'passive_due_governor':
        actions.append('Evidence scan caiu para provider probe passivo por orientação do governor; use o shield/bundle para refresh ativo, não este scan isolado.')
    if str(((provider_governor.get('summary') or {}).get('governor_mode') or '')) == 'serial_guarded':
        actions.append('Provider degradado: mantenha o scan serializado e preserve top-1/single-position enquanto o shield permanecer warn.')
    actions.append("Use capture_portfolio_canary_bundle.cmd para gerar um ZIP único com status, provider, gate, scan e asset candidate do melhor scope.")
    actions = dedupe_actions(actions)

    payload = {
        "kind": "evidence_window_scan",
        "at_utc": _now_iso(),
        "ok": severity != "error",
        "severity": severity,
        "repo_root": str(repo),
        "config_path": str(cfg_path),
        "all_scopes": bool(all_scopes),
        "summary": summary,
        "best_scope_tag": (recommended_scope or {}).get("scope", {}).get("scope_tag") if isinstance(recommended_scope, dict) else None,
        "window_state": recommended_scope.get("window_state") if isinstance(recommended_scope, dict) else None,
        "recommended_action": recommended_scope.get("recommended_action") if isinstance(recommended_scope, dict) else None,
        "canary_contract": canary_contract,
        "recommended_scope": recommended_scope,
        "best_scope": recommended_scope,
        "provider_ready_scopes": provider_ready_count,
        "scope_results": scope_results[: max(1, int(top_n or len(scope_results)))] if top_n else scope_results,
        "full_scope_count": len(scope_results),
        "provider_probe": {
            "severity": provider.get("severity"),
            "summary": provider.get("summary"),
            "shared_provider_session": provider.get("shared_provider_session"),
            "transport_hint": provider.get("transport_hint"),
            "strategy": provider_strategy,
            "active_requested": provider_requested_active,
            "active_effective": provider_effective_active,
            "artifact_age_sec": provider_artifact_age_sec,
        },
        "provider_stability": {
            "stability_state": provider_stability.get('stability_state'),
            "severity": provider_stability.get('severity'),
            "summary": provider_stability.get('summary'),
        },
        "provider_session_governor": {
            "severity": provider_governor.get('severity'),
            "summary": provider_governor.get('summary'),
            "governor": provider_governor.get('governor'),
        },
        "actions": actions,
        "actionable_blockers": sorted({str(name) for item in scope_results for name in list(((item.get("doctor") or {}).get("blockers") or [])) if str(name)}),
    }
    if write_artifact:
        if bool(all_scopes) or len(scopes) > 1:
            path = repo / "runs" / "control" / "_repo" / "evidence_window_scan.json"
        else:
            scope_tag = str(scope_results[0].get("scope", {}).get("scope_tag") if scope_results else "scan")
            path = repo / "runs" / "control" / scope_tag / "evidence_window_scan.json"
        _write_payload(path, payload)
    return payload


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Scan read-only dos scopes para escolher a melhor janela/scope de evidência do portfolio canary.")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--config", default=None)
    ap.add_argument("--asset", default=None)
    ap.add_argument("--interval-sec", type=int, default=None)
    ap.add_argument("--all-scopes", action="store_true")
    ap.add_argument("--active-provider-probe", action="store_true")
    ap.add_argument("--sample-candles", type=int, default=3)
    ap.add_argument("--market-context-max-age-sec", type=int, default=None)
    ap.add_argument("--min-dataset-rows", type=int, default=100)
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    ns = ap.parse_args(argv)
    payload = build_evidence_window_scan_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        all_scopes=bool(ns.all_scopes),
        active_provider_probe=bool(ns.active_provider_probe),
        sample_candles=int(ns.sample_candles or 0),
        market_context_max_age_sec=ns.market_context_max_age_sec,
        min_dataset_rows=int(ns.min_dataset_rows or 100),
        top_n=int(ns.top_n or 0),
        write_artifact=True,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get("ok")) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
