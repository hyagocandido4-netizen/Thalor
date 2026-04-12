from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..runtime.provider_issue_recorder import read_provider_issue_events
from ..utils.provider_issue_taxonomy import aggregate_provider_issue_texts
from .diagnostic_utils import artifact_freshness, dedupe_actions, load_selected_scopes, now_utc
from .provider_probe import build_provider_probe_payload

_ARTIFACT_NAMES = (
    'provider_probe',
    'portfolio_canary_warmup',
    'evidence_window_scan',
    'portfolio_canary_signal_scan',
    'portfolio_canary_signal_proof',
)
_DEFAULT_ARTIFACT_MAX_AGE_SEC = 900


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


def _read_repo_artifact(repo: Path, name: str) -> dict[str, Any] | None:
    return _read_json(repo / 'runs' / 'control' / '_repo' / f'{name}.json')


def _normalize_issue_text(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {'returncode=0', 'timed_out=false', 'watch', 'hold', 'ready', 'actionable', 'wait_signal_and_rescan', 'rescan_next_candle'}:
        return None
    if lowered.startswith('strategy=') and lowered.endswith('skip_fresh'):
        return None
    if lowered.startswith('{') and lowered.endswith('}'):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if isinstance(payload, dict) and {'asset', 'interval_sec', 'market_open', 'open_source'}.issubset(set(payload.keys())):
            return None
    return text


def _append_issue_text(texts: list[str], raw: Any) -> None:
    text = _normalize_issue_text(raw)
    if text:
        texts.append(text)


def _texts_from_checklike(items: Iterable[Any]) -> list[str]:
    texts: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get('status') or item.get('severity') or '').strip().lower()
        include_message = status in {'warn', 'warning', 'error', 'failed'}
        if include_message:
            for key in ('message', 'reason'):
                _append_issue_text(texts, item.get(key))
        for key in ('stderr_tail',):
            _append_issue_text(texts, item.get(key))
        stdout_tail = item.get('stdout_tail')
        if status in {'warn', 'warning', 'error', 'failed'}:
            _append_issue_text(texts, stdout_tail)
    return texts


def _provider_texts(provider: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    texts.extend(_texts_from_checklike(list(provider.get('checks') or [])))
    shared = provider.get('shared_provider_session')
    if isinstance(shared, Mapping):
        _append_issue_text(texts, shared.get('reason'))
        metadata_prime = shared.get('metadata_prime')
        if isinstance(metadata_prime, Mapping):
            _append_issue_text(texts, metadata_prime.get('reason'))
    for scope in list(provider.get('scope_results') or []):
        if not isinstance(scope, Mapping):
            continue
        texts.extend(_texts_from_checklike(list(scope.get('checks') or [])))
        for bucket_name in ('remote_candles', 'remote_market_context', 'local_market_context', 'local_candle_db'):
            bucket = scope.get(bucket_name)
            if not isinstance(bucket, Mapping):
                continue
            status = str(bucket.get('status') or '').strip().lower()
            if status in {'warn', 'warning', 'error', 'failed'} or bool(bucket.get('advisory_only')):
                _append_issue_text(texts, bucket.get('reason'))
                _append_issue_text(texts, bucket.get('message'))
        for raw in list(scope.get('provider_errors') or []):
            _append_issue_text(texts, raw)
        for raw in list(scope.get('provider_warnings') or []):
            _append_issue_text(texts, raw)
    return texts


def _warmup_texts(warmup: Mapping[str, Any]) -> list[str]:
    texts = _texts_from_checklike(list(warmup.get('scope_results') or []))
    for scope in list(warmup.get('scope_results') or []):
        if not isinstance(scope, Mapping):
            continue
        cats = scope.get('issue_categories')
        if isinstance(cats, Mapping):
            for row in list(cats.get('categories') or []):
                if not isinstance(row, Mapping):
                    continue
                for example in list(row.get('examples') or []):
                    _append_issue_text(texts, example)
    return texts


def _scan_texts(scan: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    texts.extend(_texts_from_checklike(list(scan.get('scope_results') or [])))
    for item in list(scan.get('actionable_blockers') or []):
        if isinstance(item, str) and item.strip():
            texts.append(item.strip())
    for scope in list(scan.get('scope_results') or []):
        if not isinstance(scope, Mapping):
            continue
        for key in ('provider_errors', 'provider_warnings', 'reason_trace'):
            for raw in list(scope.get(key) or []):
                _append_issue_text(texts, raw)
    return texts


def _signal_texts(signal_scan: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    texts.extend(_texts_from_checklike(list(signal_scan.get('scope_results') or [])))
    for scope in list(signal_scan.get('scope_results') or []):
        if not isinstance(scope, Mapping):
            continue
        for key in ('gate_fail_detail', 'gate_mode', 'candidate_reason'):
            _append_issue_text(texts, scope.get(key))
        for raw in list(scope.get('candidate_blockers') or []):
            _append_issue_text(texts, raw)
        cats = scope.get('issue_categories')
        if isinstance(cats, Mapping):
            for row in list(cats.get('categories') or []):
                if not isinstance(row, Mapping):
                    continue
                for example in list(row.get('examples') or []):
                    _append_issue_text(texts, example)
    best = signal_scan.get('best_actionable_scope') or signal_scan.get('best_watch_scope') or signal_scan.get('best_observed_scope')
    if isinstance(best, Mapping):
        candidate = best.get('candidate') if isinstance(best.get('candidate'), Mapping) else {}
        _append_issue_text(texts, candidate.get('reason') if isinstance(candidate, Mapping) else best.get('reason'))
    return texts


def _artifact_snapshot(
    repo: Path,
    *,
    now: datetime | None = None,
    max_age_sec: int = _DEFAULT_ARTIFACT_MAX_AGE_SEC,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payloads: dict[str, Any] = {}
    freshness: dict[str, Any] = {}
    current = now or now_utc()
    for name in _ARTIFACT_NAMES:
        path = repo / 'runs' / 'control' / '_repo' / f'{name}.json'
        payload = _read_json(path)
        freshness[name] = {
            'path': str(path),
            **artifact_freshness(payload, max_age_sec=max_age_sec, now=current),
        }
        if isinstance(payload, dict):
            payloads[name] = payload
    if 'portfolio_canary_signal_scan' not in payloads and isinstance(payloads.get('portfolio_canary_signal_proof'), dict):
        payloads['portfolio_canary_signal_scan'] = dict(payloads['portfolio_canary_signal_proof'])
        freshness['portfolio_canary_signal_scan'] = dict(freshness.get('portfolio_canary_signal_proof') or {})
    return payloads, freshness


def _category_status(*, category: str, count: int, provider_ready_scopes: int) -> str:
    if count <= 0:
        return 'ok'
    if category in {'transport_proxy', 'auth_credentials'}:
        return 'error'
    if category in {'session_parse', 'websocket_lifecycle', 'upstream_digital_metadata', 'timeout'}:
        return 'warn' if provider_ready_scopes > 0 else 'error'
    if category in {'local_artifact', 'intelligence_cp_meta'}:
        return 'warn'
    if category == 'strategy_no_trade':
        return 'ok'
    if category == 'unknown':
        return 'ok'
    return 'warn'


def build_provider_stability_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = True,
    active_provider_probe: bool = True,
    refresh_probe: bool = False,
    sample_candles: int = 3,
    market_context_max_age_sec: int | None = None,
    recorded_event_limit: int = 200,
    artifact_max_age_sec: int = _DEFAULT_ARTIFACT_MAX_AGE_SEC,
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
            'kind': 'provider_stability_report',
            'at_utc': _now_iso(),
            'ok': False,
            'severity': 'error',
            'repo_root': str(repo),
            'config_path': str(cfg_path),
            'message': 'no_scopes_selected',
        }

    current_now = now_utc()
    artifacts, artifact_meta = _artifact_snapshot(repo, now=current_now, max_age_sec=int(artifact_max_age_sec or _DEFAULT_ARTIFACT_MAX_AGE_SEC))
    provider = artifacts.get('provider_probe')
    provider_meta = dict(artifact_meta.get('provider_probe') or {})
    should_refresh_probe = refresh_probe or not isinstance(provider, dict) or bool(provider_meta.get('stale'))
    if should_refresh_probe:
        provider = build_provider_probe_payload(
            repo_root=repo,
            config_path=cfg_path,
            asset=asset,
            interval_sec=interval_sec,
            all_scopes=all_scopes,
            active=True if bool(provider_meta.get('stale')) else bool(active_provider_probe),
            sample_candles=int(sample_candles or 0),
            probe_market_context=True,
            market_context_max_age_sec=market_context_max_age_sec,
            write_artifact=bool(write_artifact),
        )
        artifacts['provider_probe'] = provider
        artifact_meta['provider_probe'] = {
            'path': str(repo / 'runs' / 'control' / '_repo' / 'provider_probe.json'),
            **artifact_freshness(provider, max_age_sec=int(artifact_max_age_sec or _DEFAULT_ARTIFACT_MAX_AGE_SEC), now=now_utc()),
            'refreshed': True,
            'was_stale': bool(provider_meta.get('stale')),
        }
    warmup = artifacts.get('portfolio_canary_warmup') or {}
    scan = artifacts.get('evidence_window_scan') or {}
    signal_scan = artifacts.get('portfolio_canary_signal_scan') or {}
    stale_artifact_names = sorted(name for name, meta in artifact_meta.items() if bool((meta or {}).get('stale')))

    issue_texts: list[str] = []
    issue_texts.extend(_provider_texts(provider if isinstance(provider, Mapping) else {}))
    issue_texts.extend(_warmup_texts(warmup if isinstance(warmup, Mapping) else {}))
    issue_texts.extend(_scan_texts(scan if isinstance(scan, Mapping) else {}))
    issue_texts.extend(_signal_texts(signal_scan if isinstance(signal_scan, Mapping) else {}))
    recorded_events = read_provider_issue_events(repo, limit=max(20, int(recorded_event_limit or 200)))
    for event in recorded_events:
        if not isinstance(event, Mapping):
            continue
        for key in ('reason', 'normalized_reason', 'message'):
            _append_issue_text(issue_texts, event.get(key))

    categories = aggregate_provider_issue_texts(issue_texts)
    provider_summary = provider.get('summary') if isinstance(provider, Mapping) else {}
    provider_ready_scopes = int((provider_summary or {}).get('provider_ready_scopes') or 0)
    scope_count = int((provider_summary or {}).get('scope_count') or len(scopes))
    scan_summary = scan.get('summary') if isinstance(scan, Mapping) else {}
    signal_summary = signal_scan.get('summary') if isinstance(signal_scan, Mapping) else {}
    warmup_summary = warmup.get('summary') if isinstance(warmup, Mapping) else {}

    category_rows: list[dict[str, Any]] = []
    hard_blockers: list[str] = []
    transient_noise: list[str] = []
    for item in list(categories.get('categories') or []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row['status'] = _category_status(category=str(row.get('category') or 'unknown'), count=int(row.get('count') or 0), provider_ready_scopes=provider_ready_scopes)
        category_rows.append(row)
        if str(row.get('status')) == 'error':
            hard_blockers.append(str(row.get('category') or 'unknown'))
        elif int(row.get('count') or 0) > 0 and str(row.get('category') or '') in {'session_parse', 'websocket_lifecycle', 'upstream_digital_metadata', 'timeout'}:
            transient_noise.append(str(row.get('category') or 'unknown'))

    if hard_blockers or provider_ready_scopes <= 0:
        stability_state = 'unstable'
        severity = 'error'
    elif transient_noise:
        stability_state = 'degraded'
        severity = 'warn'
    else:
        stability_state = 'stable'
        severity = 'ok'

    actionable_scopes = int((signal_summary or {}).get('actionable_scopes') or 0)
    healthy_waiting_signal = bool((signal_summary or {}).get('healthy_waiting_signal'))
    strategy_blocked = bool(healthy_waiting_signal) or any(str(row.get('category')) == 'strategy_no_trade' and int(row.get('count') or 0) > 0 for row in category_rows)

    actions: list[str] = []
    if 'transport_proxy' in hard_blockers:
        actions.append('Corrija o caminho de transporte/proxy antes de qualquer expansão operacional; isso é blocker real do provider path.')
    if 'auth_credentials' in hard_blockers:
        actions.append('Valide credenciais/sessão do broker antes de continuar; auth/credential failure não é ruído transitório.')
    if transient_noise:
        actions.append('Mantenha execução single-position/top-1 e trate o provider como degradado até o ruído de sessão cair para zero.')
    if provider_ready_scopes > 0 and stability_state == 'degraded':
        actions.append('O provider está funcional, mas ruidoso; use o canary apenas para observação/ranking e evite aumentar concorrência agora.')
    if any(str(row.get('category')) == 'intelligence_cp_meta' and int(row.get('count') or 0) > 0 for row in category_rows):
        actions.append('Há scopes suprimidos por cp_meta ausente; trate isso como backfill/auditoria de inteligência, não como falha do provider.')
    if strategy_blocked and stability_state == 'stable':
        actions.append('O sistema parece saudável; ausência de trade acionável decorre do gating/modelo e não de defeito de conexão.')
    if actionable_scopes > 0 and stability_state == 'stable':
        actions.append('Há scope acionável com provider estável; capture evidência do melhor scope antes de discutir expansão operacional.')
    if stale_artifact_names:
        actions.append('Artifacts de controle estão stale; não trate relatórios antigos como verdade atual sem refresh explícito.')
    if not actions:
        actions.append('Rode um novo bundle de estabilidade para confirmar tendência e preserve o regime canary conservador.')
    actions = dedupe_actions(actions)

    payload = {
        'kind': 'provider_stability_report',
        'at_utc': _now_iso(),
        'ok': severity != 'error',
        'severity': severity,
        'stability_state': stability_state,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'all_scopes': bool(all_scopes),
        'summary': {
            'scope_count': scope_count,
            'provider_ready_scopes': provider_ready_scopes,
            'warmup_effective_ready_scopes': int((warmup_summary or {}).get('effective_ready_scopes') or 0),
            'signal_actionable_scopes': actionable_scopes,
            'signal_healthy_waiting': healthy_waiting_signal,
            'recorded_issue_events': len(recorded_events),
            'transient_noise_categories': transient_noise,
            'hard_blockers': hard_blockers,
            'parallel_execution_allowed': False,
        },
        'categories': category_rows,
        'artifacts': {
            'provider_probe_present': isinstance(provider, Mapping),
            'provider_probe_fresh': bool((artifact_meta.get('provider_probe') or {}).get('fresh')),
            'warmup_present': isinstance(warmup, Mapping) and bool(warmup),
            'warmup_fresh': bool((artifact_meta.get('portfolio_canary_warmup') or {}).get('fresh')),
            'evidence_window_scan_present': isinstance(scan, Mapping) and bool(scan),
            'evidence_window_scan_fresh': bool((artifact_meta.get('evidence_window_scan') or {}).get('fresh')),
            'signal_scan_present': isinstance(signal_scan, Mapping) and bool(signal_scan),
            'signal_scan_fresh': bool((artifact_meta.get('portfolio_canary_signal_scan') or {}).get('fresh')),
            'stale_artifacts': stale_artifact_names,
            'freshness': artifact_meta,
        },
        'provider_probe': {
            'severity': provider.get('severity') if isinstance(provider, Mapping) else None,
            'shared_provider_session': provider.get('shared_provider_session') if isinstance(provider, Mapping) else None,
            'transport_hint': provider.get('transport_hint') if isinstance(provider, Mapping) else None,
        },
        'canary': {
            'best_scope': scan.get('best_scope') if isinstance(scan, Mapping) else None,
            'recommended_scope': scan.get('recommended_scope') if isinstance(scan, Mapping) else None,
            'window_scan_severity': scan.get('severity') if isinstance(scan, Mapping) else None,
            'signal_scan_severity': signal_scan.get('severity') if isinstance(signal_scan, Mapping) else None,
            'healthy_waiting_signal': healthy_waiting_signal,
        },
        'recorded_events_tail': recorded_events[-20:],
        'actions': actions,
    }
    if write_artifact:
        path = repo / 'runs' / 'control' / '_repo' / 'provider_stability.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Provider Session Shield: classifica a estabilidade real do broker/provider e separa ruído transitório de blocker estrutural.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--asset', default=None)
    ap.add_argument('--interval-sec', type=int, default=None)
    ap.add_argument('--all-scopes', action='store_true')
    ap.set_defaults(active_provider_probe=True)
    ap.add_argument('--active-provider-probe', dest='active_provider_probe', action='store_true')
    ap.add_argument('--passive-provider-probe', dest='active_provider_probe', action='store_false')
    ap.add_argument('--refresh-probe', action='store_true')
    ap.add_argument('--sample-candles', type=int, default=3)
    ap.add_argument('--market-context-max-age-sec', type=int, default=None)
    ap.add_argument('--recorded-event-limit', type=int, default=200)
    ap.add_argument('--artifact-max-age-sec', type=int, default=_DEFAULT_ARTIFACT_MAX_AGE_SEC)
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_provider_stability_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        all_scopes=bool(ns.all_scopes),
        active_provider_probe=bool(ns.active_provider_probe),
        refresh_probe=bool(ns.refresh_probe),
        sample_candles=int(ns.sample_candles or 0),
        market_context_max_age_sec=ns.market_context_max_age_sec,
        recorded_event_limit=int(ns.recorded_event_limit or 200),
        artifact_max_age_sec=int(ns.artifact_max_age_sec or _DEFAULT_ARTIFACT_MAX_AGE_SEC),
        write_artifact=True,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
