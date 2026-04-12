from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .diagnostic_utils import artifact_freshness, dedupe_actions, load_selected_scopes, now_utc
from .provider_stability import build_provider_stability_payload


_DEFAULT_STABILITY_MAX_AGE_SEC = 900


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def provider_session_governor_artifact_path(repo_root: str | Path = '.') -> Path:
    return Path(repo_root).resolve() / 'runs' / 'control' / '_repo' / 'provider_session_governor.json'


def read_provider_session_governor_payload(repo_root: str | Path = '.') -> dict[str, Any] | None:
    return _read_json(provider_session_governor_artifact_path(repo_root))


def _derive_governor(*, scope_count: int, provider_ready_scopes: int, stability_state: str, transient_noise: list[str], hard_blockers: list[str]) -> dict[str, Any]:
    state = str(stability_state or 'unknown').strip().lower()
    scope_count = max(1, int(scope_count or 1))
    provider_ready_scopes = max(0, int(provider_ready_scopes or 0))
    candidate_budget = max(1, min(scope_count, scope_count))
    if state == 'stable':
        return {
            'mode': 'normal',
            'sleep_between_scopes_ms': 250,
            'sleep_between_candidate_scopes_ms': 250,
            'refresh_market_context_timeout_sec': 60,
            'asset_prepare_timeout_sec': 240,
            'max_asset_prepare_fallback_scopes': scope_count,
            'max_candidate_scopes_per_run': candidate_budget,
            'prefer_cached_provider_artifacts': False,
            'skip_fresh_market_context_scopes': True,
            'scope_order': 'best_first_round_robin',
            'allow_parallel_execution': False,
            'allow_standalone_provider_probe': True,
        }
    if state == 'degraded':
        noisy = bool({'websocket_lifecycle', 'session_parse', 'upstream_digital_metadata', 'timeout'}.intersection(set(transient_noise or [])))
        sleep_ms = 1500 if noisy else 900
        refresh_timeout = 45 if noisy else 60
        prepare_timeout = 180 if noisy else 210
        candidate_budget = max(1, min(scope_count, 3 if noisy and scope_count > 3 else scope_count))
        prepare_budget = max(1, min(scope_count, 3 if noisy and scope_count > 3 else max(1, provider_ready_scopes or 1)))
        return {
            'mode': 'serial_guarded',
            'sleep_between_scopes_ms': sleep_ms,
            'sleep_between_candidate_scopes_ms': max(1000, sleep_ms),
            'refresh_market_context_timeout_sec': refresh_timeout,
            'asset_prepare_timeout_sec': prepare_timeout,
            'max_asset_prepare_fallback_scopes': prepare_budget,
            'max_candidate_scopes_per_run': candidate_budget,
            'prefer_cached_provider_artifacts': True,
            'skip_fresh_market_context_scopes': True,
            'scope_order': 'best_first_round_robin',
            'allow_parallel_execution': False,
            'allow_standalone_provider_probe': True,
        }
    if not hard_blockers and provider_ready_scopes <= 0:
        return {
            'mode': 'bootstrap_guarded',
            'sleep_between_scopes_ms': 1000,
            'sleep_between_candidate_scopes_ms': 1000,
            'refresh_market_context_timeout_sec': 45,
            'asset_prepare_timeout_sec': 180,
            'max_asset_prepare_fallback_scopes': max(1, min(scope_count, 2)),
            'max_candidate_scopes_per_run': candidate_budget,
            'prefer_cached_provider_artifacts': False,
            'skip_fresh_market_context_scopes': True,
            'scope_order': 'best_first_round_robin',
            'allow_parallel_execution': False,
            'allow_standalone_provider_probe': True,
        }
    return {
        'mode': 'hold_only',
        'sleep_between_scopes_ms': 2000,
        'sleep_between_candidate_scopes_ms': 2000,
        'refresh_market_context_timeout_sec': 30,
        'asset_prepare_timeout_sec': 120,
        'max_asset_prepare_fallback_scopes': 0,
        'max_candidate_scopes_per_run': 0,
        'prefer_cached_provider_artifacts': True,
        'skip_fresh_market_context_scopes': True,
        'scope_order': 'best_first',
        'allow_parallel_execution': False,
        'allow_standalone_provider_probe': False,
        'hard_blockers': list(hard_blockers or []),
    }


def build_provider_session_governor_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = True,
    active_provider_probe: bool = True,
    refresh_stability: bool = False,
    sample_candles: int = 3,
    market_context_max_age_sec: int | None = None,
    recorded_event_limit: int = 200,
    stability_max_age_sec: int = _DEFAULT_STABILITY_MAX_AGE_SEC,
    write_artifact: bool = True,
) -> dict[str, Any]:
    repo, cfg_path, _cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    if not scopes:
        return {
            'kind': 'provider_session_governor',
            'at_utc': _now_iso(),
            'ok': False,
            'severity': 'error',
            'repo_root': str(repo),
            'config_path': str(cfg_path),
            'message': 'no_scopes_selected',
        }

    stability_path = repo / 'runs' / 'control' / '_repo' / 'provider_stability.json'
    stability = None if refresh_stability else _read_json(stability_path)
    stability_meta = {
        'path': str(stability_path),
        **artifact_freshness(stability, max_age_sec=int(stability_max_age_sec or _DEFAULT_STABILITY_MAX_AGE_SEC), now=now_utc()),
    }
    if (not isinstance(stability, dict)) or bool(stability_meta.get('stale')):
        stability = build_provider_stability_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            active_provider_probe=True if bool(stability_meta.get('stale')) else bool(active_provider_probe),
            refresh_probe=bool(refresh_stability) or bool(stability_meta.get('stale')),
            sample_candles=int(sample_candles or 0),
            market_context_max_age_sec=market_context_max_age_sec,
            recorded_event_limit=int(recorded_event_limit or 0),
            artifact_max_age_sec=int(stability_max_age_sec or _DEFAULT_STABILITY_MAX_AGE_SEC),
            write_artifact=bool(write_artifact),
        )
        stability_meta = {
            'path': str(stability_path),
            **artifact_freshness(stability, max_age_sec=int(stability_max_age_sec or _DEFAULT_STABILITY_MAX_AGE_SEC), now=now_utc()),
            'refreshed': True,
        }

    stability_summary = dict(stability.get('summary') or {}) if isinstance(stability, Mapping) else {}
    transient_noise = [str(item) for item in list(stability_summary.get('transient_noise_categories') or []) if str(item)]
    hard_blockers = [str(item) for item in list(stability_summary.get('hard_blockers') or []) if str(item)]
    scope_count = int(stability_summary.get('scope_count') or len(scopes) or 0)
    provider_ready_scopes = int(stability_summary.get('provider_ready_scopes') or 0)
    governor = _derive_governor(
        scope_count=scope_count,
        provider_ready_scopes=provider_ready_scopes,
        stability_state=str(stability.get('stability_state') or 'unknown'),
        transient_noise=transient_noise,
        hard_blockers=hard_blockers,
    )

    state = str(stability.get('stability_state') or 'unknown').strip().lower()
    severity = 'ok' if state == 'stable' else 'warn' if state in {'degraded'} or governor.get('mode') == 'bootstrap_guarded' else 'error'
    actions: list[str] = []
    if severity == 'warn':
        actions.append('Provider degradado: mantenha observação multi-asset, execução top-1 e no máximo 1 posição aberta.')
        actions.append('Prefira artifacts frescos e fan-out serializado; evite bursts de scan até o ruído do provider cair.')
    elif severity == 'error':
        actions.append('Provider instável: não expanda o regime operacional; trate a sessão do provider antes de qualquer aumento de escopo.')
    else:
        actions.append('Provider estável: mantenha o canary conservador e capture evidência do melhor scope antes de discutir expansão operacional.')
    if 'upstream_digital_metadata' in transient_noise:
        actions.append('Ruído digital/underlying detectado; trate isso como fragilidade do upstream IQ, não como falha do proxy Decodo.')
    if 'websocket_lifecycle' in transient_noise:
        actions.append('Ruído de websocket detectado; mantenha pacing serial entre scopes e evite scans repetidos no mesmo candle.')
    if hard_blockers:
        actions.append('Há blockers estruturais no provider shield; preserve parallel_execution_allowed=false até o shield voltar para stable.')
    if bool(stability_meta.get('stale')):
        actions.append('O artifact de provider_stability estava stale e foi reavaliado antes de emitir o governor.')
    actions = dedupe_actions(actions)

    payload = {
        'kind': 'provider_session_governor',
        'at_utc': _now_iso(),
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'all_scopes': bool(all_scopes),
        'summary': {
            'scope_count': scope_count,
            'provider_ready_scopes': provider_ready_scopes,
            'stability_state': stability.get('stability_state'),
            'transient_noise_categories': transient_noise,
            'hard_blockers': hard_blockers,
            'governor_mode': governor.get('mode'),
            'parallel_execution_allowed': bool(governor.get('allow_parallel_execution', False)),
            'sleep_between_scopes_ms': int(governor.get('sleep_between_scopes_ms') or 0),
            'sleep_between_candidate_scopes_ms': int(governor.get('sleep_between_candidate_scopes_ms') or 0),
            'refresh_market_context_timeout_sec': int(governor.get('refresh_market_context_timeout_sec') or 0),
            'asset_prepare_timeout_sec': int(governor.get('asset_prepare_timeout_sec') or 0),
            'max_asset_prepare_fallback_scopes': int(governor.get('max_asset_prepare_fallback_scopes') or 0),
            'max_candidate_scopes_per_run': int(governor.get('max_candidate_scopes_per_run') or 0),
        },
        'governor': governor,
        'stability': {
            'stability_state': stability.get('stability_state'),
            'severity': stability.get('severity'),
            'provider_summary': stability_summary,
            'categories': list(stability.get('categories') or []),
        },
        'artifacts': {
            'provider_stability': stability_meta,
        },
        'actions': actions,
    }
    if write_artifact:
        path = provider_session_governor_artifact_path(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Provider Session Governor: transforma estabilidade do provider em governança durável para fan-out, pacing e bundles do canary.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--asset', default=None)
    ap.add_argument('--interval-sec', type=int, default=None)
    ap.add_argument('--all-scopes', action='store_true')
    ap.set_defaults(active_provider_probe=True)
    ap.add_argument('--active-provider-probe', dest='active_provider_probe', action='store_true')
    ap.add_argument('--passive-provider-probe', dest='active_provider_probe', action='store_false')
    ap.add_argument('--refresh-stability', action='store_true')
    ap.add_argument('--sample-candles', type=int, default=3)
    ap.add_argument('--market-context-max-age-sec', type=int, default=None)
    ap.add_argument('--recorded-event-limit', type=int, default=200)
    ap.add_argument('--stability-max-age-sec', type=int, default=_DEFAULT_STABILITY_MAX_AGE_SEC)
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_provider_session_governor_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        all_scopes=bool(ns.all_scopes),
        active_provider_probe=bool(ns.active_provider_probe),
        refresh_stability=bool(ns.refresh_stability),
        sample_candles=int(ns.sample_candles or 0),
        market_context_max_age_sec=ns.market_context_max_age_sec,
        recorded_event_limit=int(ns.recorded_event_limit or 0),
        stability_max_age_sec=int(ns.stability_max_age_sec or _DEFAULT_STABILITY_MAX_AGE_SEC),
        write_artifact=True,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
