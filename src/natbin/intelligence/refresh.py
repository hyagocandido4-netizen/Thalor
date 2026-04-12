from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config.loader import load_thalor_config
from ..config.paths import resolve_config_path, resolve_repo_root
from ..portfolio.candidate_utils import candidate_from_decision_payload
from ..portfolio.materialize import materialize_portfolio_latest_payloads
from ..portfolio.models import CandidateDecision, PortfolioScope
from ..portfolio.paths import resolve_scope_runtime_paths, scope_tag as compute_scope_tag
from ..runtime.scope import decision_latest_path
from .fit import fit_intelligence_pack
from .paths import latest_eval_path, pack_path, retrain_plan_path, retrain_status_path
from .runtime import enrich_candidate


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def _write_latest_eval_placeholder(
    *,
    repo_root: Path,
    artifact_dir: str | Path,
    scope: PortfolioScope,
    pack_payload: dict[str, Any] | None,
    eval_out: Path,
    decision_path: Path,
    status: str,
    reason: str | None,
) -> dict[str, Any]:
    metadata = dict((pack_payload or {}).get('metadata') or {}) if isinstance(pack_payload, dict) else {}
    payload = {
        'kind': 'intelligence_eval',
        'schema_version': 'phase1-intelligence-eval-v3',
        'evaluated_at_utc': _now_iso(),
        'scope_tag': scope.scope_tag,
        'asset': scope.asset,
        'interval_sec': int(scope.interval_sec),
        'pack_available': bool(pack_payload is not None),
        'status': str(status or 'decision_missing'),
        'allow_trade': False,
        'reason': reason,
        'decision_path': str(decision_path),
        'pack_path': str(pack_path(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)),
        'pack_training_rows': int(metadata.get('training_rows') or 0),
        'pack_training_strategy': metadata.get('training_strategy'),
    }
    _write_json(eval_out, payload)
    return payload


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return dict(obj) if isinstance(obj, dict) else None


def _select_scopes(cfg: Any, *, asset: str | None, interval_sec: int | None) -> list[Any]:
    out: list[Any] = []
    for item in list(getattr(cfg, 'assets', []) or []):
        if asset is not None and str(item.asset) != str(asset):
            continue
        if interval_sec is not None and int(item.interval_sec) != int(interval_sec):
            continue
        out.append(item)
    return out


def _to_scope(item: Any) -> PortfolioScope:
    return PortfolioScope(
        asset=str(item.asset),
        interval_sec=int(item.interval_sec),
        timezone=str(getattr(item, 'timezone', 'UTC')),
        scope_tag=compute_scope_tag(str(item.asset), int(item.interval_sec)),
        weight=float(getattr(item, 'weight', 1.0) or 1.0),
        cluster_key=str(getattr(item, 'cluster_key', 'default') or 'default'),
        topk_k=int(getattr(item, 'topk_k', 3) or 3),
        hard_max_trades_per_day=getattr(item, 'hard_max_trades_per_day', None),
        max_open_positions=getattr(item, 'max_open_positions', None),
        max_pending_unknown=getattr(item, 'max_pending_unknown', None),
    )


def refresh_config_intelligence(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    asset: str | None = None,
    interval_sec: int | None = None,
    rebuild_pack: bool = True,
    materialize_portfolio: bool = True,
    write_legacy_portfolio: bool = False,
) -> dict[str, Any]:
    """Rebuild/evaluate intelligence artifacts for the current config/profile.

    The recovery flow has two responsibilities:
    1. keep `pack.json` / `latest_eval.json` in sync with the current scope(s)
    2. materialize scoped portfolio latest payloads for the current profile so
       ops surfaces stop reading stale or mismatched legacy artifacts.
    """

    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    cfg = load_thalor_config(config_path=cfg_path, repo_root=root)
    runtime_profile = str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default')
    int_cfg = getattr(cfg, 'intelligence', None)
    artifact_dir = getattr(int_cfg, 'artifact_dir', 'runs/intelligence') if int_cfg is not None else 'runs/intelligence'

    chosen = _select_scopes(cfg, asset=asset, interval_sec=interval_sec)
    if not chosen:
        return {
            'ok': False,
            'message': 'scope_not_found',
            'repo_root': str(root),
            'config_path': str(cfg_path),
            'runtime_profile': runtime_profile,
            'items': [],
        }

    items: list[dict[str, Any]] = []
    materialize_pairs: list[tuple[PortfolioScope, CandidateDecision]] = []

    for item in chosen:
        scope = _to_scope(item)
        scope_runtime_paths = resolve_scope_runtime_paths(root, scope_tag=scope.scope_tag, partition_enable=bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)))
        decision_path = decision_latest_path(asset=scope.asset, interval_sec=int(scope.interval_sec), out_dir=root / 'runs')
        decision_payload = _read_json(decision_path)
        pack_out = pack_path(repo_root=root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)
        pack_payload: dict[str, Any] | None = None
        if rebuild_pack:
            try:
                pack_payload, pack_out = fit_intelligence_pack(
                    repo_root=root,
                    config_path=cfg_path,
                    asset=scope.asset,
                    interval_sec=int(scope.interval_sec),
                )
            except Exception as exc:
                items.append(
                    {
                        'scope_tag': scope.scope_tag,
                        'asset': scope.asset,
                        'interval_sec': int(scope.interval_sec),
                        'ok': False,
                        'phase': 'fit_intelligence_pack',
                        'error': f'{type(exc).__name__}:{exc}',
                    }
                )
                continue
        else:
            pack_payload = _read_json(pack_out)

        candidate = candidate_from_decision_payload(scope, decision_payload, decision_path=decision_path)
        eval_out = latest_eval_path(repo_root=root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)
        if decision_payload is not None and bool(getattr(int_cfg, 'enabled', False)):
            try:
                candidate = enrich_candidate(
                    repo_root=root,
                    scope=scope,
                    candidate=candidate,
                    runtime_paths=scope_runtime_paths,
                    cfg=cfg,
                )
            except Exception as exc:
                items.append(
                    {
                        'scope_tag': scope.scope_tag,
                        'asset': scope.asset,
                        'interval_sec': int(scope.interval_sec),
                        'ok': False,
                        'phase': 'enrich_candidate',
                        'error': f'{type(exc).__name__}:{exc}',
                        'pack_path': str(pack_out),
                        'decision_path': str(decision_path),
                    }
                )
                continue
            eval_payload = _read_json(eval_out)
        else:
            placeholder_status = 'decision_missing' if decision_payload is None else 'intelligence_disabled'
            placeholder_reason = 'decision_artifact_missing' if decision_payload is None else 'intelligence_disabled'
            eval_payload = _write_latest_eval_placeholder(
                repo_root=root,
                artifact_dir=artifact_dir,
                scope=scope,
                pack_payload=pack_payload,
                eval_out=eval_out,
                decision_path=decision_path,
                status=placeholder_status,
                reason=placeholder_reason,
            )
        if decision_payload is not None:
            materialize_pairs.append((scope, candidate))

        meta = dict((pack_payload or {}).get('metadata') or {}) if isinstance(pack_payload, dict) else {}
        anti = dict((pack_payload or {}).get('anti_overfit') or {}) if isinstance(pack_payload, dict) else {}
        anti_tuning = dict((pack_payload or {}).get('anti_overfit_tuning') or {}) if isinstance(pack_payload, dict) else {}
        eval_anti = dict((eval_payload or {}).get('anti_overfit') or {}) if isinstance(eval_payload, dict) else {}
        items.append(
            {
                'scope_tag': scope.scope_tag,
                'asset': scope.asset,
                'interval_sec': int(scope.interval_sec),
                'ok': True,
                'rebuild_pack': bool(rebuild_pack),
                'decision_present': bool(decision_payload is not None),
                'pack_path': str(pack_out),
                'pack_training_rows': int(meta.get('training_rows') or 0),
                'pack_training_strategy': meta.get('training_strategy'),
                'pack_learned_gate_available': bool((pack_payload or {}).get('learned_gate')),
                'pack_anti_overfit_available': bool(anti.get('available')),
                'pack_anti_overfit_source': (meta.get('anti_overfit_source') or {}).get('kind'),
                'pack_anti_overfit_selected_variant': anti_tuning.get('selected_variant'),
                'pack_anti_overfit_tuned': bool(anti_tuning.get('improved')),
                'latest_eval_path': str(eval_out),
                'latest_eval_present': bool(eval_payload is not None),
                'latest_eval_allow_trade': (eval_payload or {}).get('allow_trade') if isinstance(eval_payload, dict) else None,
                'latest_eval_anti_overfit_available': bool(eval_anti.get('available')),
                'latest_eval_retrain_state': ((eval_payload or {}).get('retrain_orchestration') or {}).get('state') if isinstance(eval_payload, dict) else None,
                'latest_eval_retrain_priority': ((eval_payload or {}).get('retrain_orchestration') or {}).get('priority') if isinstance(eval_payload, dict) else None,
                'decision_path': str(decision_path),
                'retrain_plan_path': str(retrain_plan_path(repo_root=root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)),
                'retrain_status_path': str(retrain_status_path(repo_root=root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)),
            }
        )

    materialized = None
    if materialize_portfolio and materialize_pairs:
        try:
            scopes = [scope for scope, _cand in materialize_pairs]
            candidates = [cand for _scope, cand in materialize_pairs]
            materialized = materialize_portfolio_latest_payloads(
                repo_root=root,
                config_path=cfg_path,
                scopes=scopes,
                candidates=candidates,
                message='intelligence_refresh_materialized',
                write_legacy=write_legacy_portfolio,
            )
        except Exception as exc:
            materialized = {
                'ok': False,
                'message': 'materialize_failed',
                'error': f'{type(exc).__name__}:{exc}',
            }

    ok = bool(items) and all(bool(item.get('ok')) for item in items) and (materialized is None or bool(materialized.get('ok', False)))
    return {
        'ok': ok,
        'message': 'intelligence_refresh_ok' if ok else 'intelligence_refresh_warn',
        'repo_root': str(root),
        'config_path': str(cfg_path),
        'runtime_profile': runtime_profile,
        'rebuild_pack': bool(rebuild_pack),
        'materialize_portfolio': bool(materialize_portfolio),
        'items': items,
        'materialized_portfolio': materialized,
    }


__all__ = ['refresh_config_intelligence']
