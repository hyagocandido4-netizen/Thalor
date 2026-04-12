from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from ..intelligence.paths import latest_eval_path, pack_path
from ..runtime.scope import decision_latest_path
from ..utils.provider_issue_taxonomy import aggregate_provider_issue_texts
from .diagnostic_utils import age_sec_from_iso, dedupe_actions, load_selected_scopes
from .intelligence_surface import _load_allocation_entry, _load_candidate_entry

_TRADE_ACTIONS = {"CALL", "PUT", "BUY", "SELL", "UP", "DOWN"}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _scope_payload(repo: Path, scope: Any) -> tuple[Path, dict[str, Any] | None]:
    path = decision_latest_path(asset=str(scope.asset), interval_sec=int(scope.interval_sec), out_dir=repo / 'runs')
    return path, _read_json_dict(path)



def _age_from_mapping(payload: Mapping[str, Any] | None, *keys: str) -> float | None:
    if not isinstance(payload, Mapping):
        return None
    ages: list[float] = []
    for key in keys:
        age = age_sec_from_iso(payload.get(key))
        if age is not None:
            ages.append(float(age))
    if not ages:
        return None
    return round(float(min(ages)), 3)


def _min_age(*values: float | None) -> float | None:
    real = [float(v) for v in values if v is not None]
    if not real:
        return None
    return round(float(min(real)), 3)


def _artifact_sources(*, candidate_entry: Mapping[str, Any] | None, allocation_entry: Mapping[str, Any] | None, eval_payload: Mapping[str, Any] | None, pack_payload: Mapping[str, Any] | None) -> list[str]:
    out: list[str] = []
    if isinstance(candidate_entry, Mapping):
        source = candidate_entry.get('source') if isinstance(candidate_entry.get('source'), Mapping) else {}
        hint = str(source.get('source') or 'scoped')
        out.append(f'candidate:{hint}')
    if isinstance(allocation_entry, Mapping):
        source = allocation_entry.get('source') if isinstance(allocation_entry.get('source'), Mapping) else {}
        hint = str(source.get('source') or 'scoped')
        out.append(f'allocation:{hint}')
    if isinstance(eval_payload, Mapping):
        out.append('latest_eval')
    if isinstance(pack_payload, Mapping):
        out.append('pack')
    return out


def _fallback_context(repo: Path, cfg: Any, cfg_path: Path, scope: Any) -> dict[str, Any]:
    runtime_profile = str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default')
    int_cfg = getattr(cfg, 'intelligence', None)
    artifact_dir = str(getattr(int_cfg, 'artifact_dir', 'runs/intelligence') or 'runs/intelligence')
    candidate_entry, candidate_source = _load_candidate_entry(
        repo=repo,
        scope_tag=str(scope.scope_tag),
        asset=str(scope.asset),
        interval_sec=int(scope.interval_sec),
        config_path=cfg_path,
        profile=runtime_profile,
    )
    allocation_entry, allocation_source = _load_allocation_entry(
        repo=repo,
        scope_tag=str(scope.scope_tag),
        asset=str(scope.asset),
        interval_sec=int(scope.interval_sec),
        config_path=cfg_path,
        profile=runtime_profile,
    )
    eval_path = latest_eval_path(repo_root=repo, scope_tag=str(scope.scope_tag), artifact_dir=artifact_dir)
    pack_path_obj = pack_path(repo_root=repo, scope_tag=str(scope.scope_tag), artifact_dir=artifact_dir)
    eval_payload = _read_json_dict(eval_path)
    pack_payload = _read_json_dict(pack_path_obj)
    candidate_item = dict(candidate_entry.get('item') or {}) if isinstance(candidate_entry, Mapping) else {}
    allocation_item = dict(allocation_entry.get('item') or {}) if isinstance(allocation_entry, Mapping) else {}
    age_sec = _min_age(
        _age_from_mapping(candidate_entry, 'finished_at_utc', 'at_utc'),
        _age_from_mapping(allocation_entry, 'at_utc', 'finished_at_utc'),
        _artifact_age_sec(eval_path, eval_payload),
        _artifact_age_sec(pack_path_obj, pack_payload),
    )
    artifact_present = bool(candidate_item or allocation_item or eval_payload)
    return {
        'artifact_present': artifact_present,
        'age_sec': age_sec,
        'candidate_entry': candidate_entry if isinstance(candidate_entry, Mapping) else None,
        'candidate_source': candidate_source if isinstance(candidate_source, Mapping) else {},
        'candidate_item': candidate_item,
        'allocation_entry': allocation_entry if isinstance(allocation_entry, Mapping) else None,
        'allocation_source': allocation_source if isinstance(allocation_source, Mapping) else {},
        'allocation_item': allocation_item,
        'eval_path': eval_path,
        'eval_payload': eval_payload,
        'pack_path': pack_path_obj,
        'pack_payload': pack_payload,
        'artifact_sources': _artifact_sources(
            candidate_entry=candidate_entry if isinstance(candidate_entry, Mapping) else None,
            allocation_entry=allocation_entry if isinstance(allocation_entry, Mapping) else None,
            eval_payload=eval_payload,
            pack_payload=pack_payload,
        ),
    }

def _artifact_age_sec(path: Path, payload: Mapping[str, Any] | None) -> float | None:
    ages: list[float] = []
    if isinstance(payload, Mapping):
        for key in ('observed_at_utc', 'at_utc', 'updated_at_utc'):
            age = age_sec_from_iso(payload.get(key))
            if age is not None:
                ages.append(float(age))
        raw = payload.get('raw') if isinstance(payload.get('raw'), Mapping) else None
        if isinstance(raw, Mapping):
            for key in ('observed_at_utc', 'at_utc', 'updated_at_utc'):
                age = age_sec_from_iso(raw.get(key))
                if age is not None:
                    ages.append(float(age))
    if path.exists():
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            ages.append((datetime.now(tz=UTC) - mtime).total_seconds())
        except Exception:
            pass
    if not ages:
        return None
    return round(float(min(ages)), 3)


def _split_blockers(raw: Any) -> list[str]:
    text = str(raw or '').strip()
    if not text:
        return []
    out: list[str] = []
    for item in text.split(';'):
        value = str(item or '').strip()
        if value:
            out.append(value)
    return out


def _blocker_flags(blockers: list[str]) -> dict[str, bool]:
    items = {str(item or '').strip().lower() for item in blockers if str(item or '').strip()}
    return {
        'cp_reject': 'cp_reject' in items,
        'below_ev_threshold': 'below_ev_threshold' in items,
        'not_in_topk_today': 'not_in_topk_today' in items,
        'gate_fail_closed': 'gate_fail_closed' in items,
    }


def _cp_meta_missing(*, gate_fail_detail: str, gate_mode: str, blockers: list[str]) -> bool:
    haystack = ' '.join([gate_fail_detail, gate_mode, ';'.join(blockers)]).lower()
    return 'cp_fail_closed_missing_cp_meta' in haystack or 'missing_cp_meta' in haystack


def _dominant_reason(*, action: str | None, reason: str | None, cp_meta_missing: bool, regime_block: bool, blocker_flags: Mapping[str, bool], stale: bool, missing: bool) -> str:
    if missing:
        return 'missing_artifact'
    if stale:
        return 'stale_artifact'
    if action in _TRADE_ACTIONS:
        return 'actionable'
    if cp_meta_missing:
        return 'cp_meta_missing'
    reason_text = str(reason or '').strip().lower()
    if regime_block or 'regime_block' in reason_text:
        return 'regime_block'
    if bool(blocker_flags.get('below_ev_threshold')) or 'below_ev_threshold' in reason_text:
        return 'below_ev_threshold'
    if bool(blocker_flags.get('not_in_topk_today')) or 'not_in_topk_today' in reason_text:
        return 'not_in_topk_today'
    if bool(blocker_flags.get('cp_reject')) or 'cp_reject' in reason_text:
        return 'cp_reject'
    if bool(blocker_flags.get('gate_fail_closed')) or 'gate_fail_closed' in reason_text:
        return 'gate_fail_closed'
    if str(action or '').upper() == 'HOLD':
        return 'hold'
    return 'unknown'


def _score_entry(*, action: str | None, cp_meta_missing: bool, regime_block: bool, blocker_flags: Mapping[str, bool], conf: float | None, score: float | None, ev: float | None, stale: bool, missing: bool) -> float:
    value = 50.0
    if missing:
        value -= 40.0
    elif stale:
        value -= 25.0
    if action in _TRADE_ACTIONS:
        value += 35.0
    elif str(action or '').upper() == 'HOLD':
        value -= 5.0
    if cp_meta_missing:
        value -= 24.0
    if regime_block:
        value -= 12.0
    if bool(blocker_flags.get('below_ev_threshold')):
        value -= 8.0
    if bool(blocker_flags.get('not_in_topk_today')):
        value -= 8.0
    if bool(blocker_flags.get('cp_reject')):
        value -= 6.0
    if bool(blocker_flags.get('gate_fail_closed')):
        value -= 10.0
    if conf is not None:
        value += max(-4.0, min(8.0, (float(conf) - 0.5) * 40.0))
    if score is not None:
        value += max(-6.0, min(12.0, float(score) * 12.0))
    if ev is not None:
        value += max(-8.0, min(8.0, float(ev) * 4.0))
    return round(max(0.0, min(100.0, value)), 1)


def build_signal_artifact_audit_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = True,
    decision_max_age_sec: int = 3600,
    write_artifact: bool = True,
) -> dict[str, Any]:
    repo, cfg_path, cfg, scopes = load_selected_scopes(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
    )
    if not scopes:
        return {
            'kind': 'signal_artifact_audit',
            'at_utc': _now_iso(),
            'ok': False,
            'severity': 'error',
            'repo_root': str(repo),
            'config_path': str(cfg_path),
            'all_scopes': bool(all_scopes),
            'message': 'no_scopes_selected',
            'summary': {'scope_count': 0},
            'scope_results': [],
            'actions': ['Nenhum scope selecionado para auditoria de artifacts de sinal.'],
        }

    scope_results: list[dict[str, Any]] = []
    actionable_scopes = 0
    watch_scopes = 0
    hold_scopes = 0
    missing_artifact_scopes = 0
    stale_artifact_scopes = 0
    cp_meta_missing_scopes = 0
    regime_block_scopes = 0
    cp_reject_scopes = 0
    threshold_block_scopes = 0
    topk_suppressed_scopes = 0
    gate_fail_closed_scopes = 0

    for scope in scopes:
        path, payload = _scope_payload(repo, scope)
        fallback = _fallback_context(repo, cfg, cfg_path, scope)
        missing = payload is None and not bool(fallback.get('artifact_present'))
        age_sec = _min_age(_artifact_age_sec(path, payload), fallback.get('age_sec'))
        stale = bool((not missing) and (age_sec is None or float(age_sec) > float(max(1, int(decision_max_age_sec)))))

        action = None
        reason = None
        blocker_text = ''
        gate_fail_detail = ''
        gate_mode = ''
        conf = None
        score = None
        ev = None
        observed_at_utc = None
        if isinstance(payload, Mapping):
            action = str(payload.get('action') or '').upper() or None
            reason = str(payload.get('reason') or '') or None
            blocker_text = str(payload.get('blockers') or '')
            gate_fail_detail = str(payload.get('gate_fail_detail') or '')
            gate_mode = str(payload.get('gate_mode') or '')
            conf = _safe_float(payload.get('conf'))
            score = _safe_float(payload.get('score'))
            ev = _safe_float(payload.get('ev'))
            observed_at_utc = payload.get('observed_at_utc') or payload.get('at_utc')
            raw = payload.get('raw') if isinstance(payload.get('raw'), Mapping) else None
            if raw:
                action = action or (str(raw.get('action') or '').upper() or None)
                reason = reason or (str(raw.get('reason') or '') or None)
                blocker_text = blocker_text or str(raw.get('blockers') or '')
                gate_fail_detail = gate_fail_detail or str(raw.get('gate_fail_detail') or '')
                gate_mode = gate_mode or str(raw.get('gate_mode') or '')
                conf = conf if conf is not None else _safe_float(raw.get('conf'))
                score = score if score is not None else _safe_float(raw.get('score'))
                ev = ev if ev is not None else _safe_float(raw.get('ev'))
                observed_at_utc = observed_at_utc or raw.get('observed_at_utc') or raw.get('at_utc')

        candidate_item = dict(fallback.get('candidate_item') or {})
        allocation_item = dict(fallback.get('allocation_item') or {})
        eval_payload = dict(fallback.get('eval_payload') or {})
        if action is None:
            action = str(candidate_item.get('action') or allocation_item.get('action') or '').upper() or None
        if reason is None:
            reason = (
                str(candidate_item.get('reason') or '')
                or str(allocation_item.get('reason') or '')
                or str(eval_payload.get('block_reason') or '')
                or str(eval_payload.get('status') or '')
                or None
            )
        blocker_text = blocker_text or str(candidate_item.get('blockers') or allocation_item.get('blockers') or '')
        gate_fail_detail = gate_fail_detail or str(eval_payload.get('block_reason') or '')
        gate_mode = gate_mode or str(eval_payload.get('gate_mode') or '')
        conf = conf if conf is not None else _safe_float(candidate_item.get('conf') if candidate_item else allocation_item.get('conf'))
        score = score if score is not None else _safe_float(candidate_item.get('score') if candidate_item else allocation_item.get('score'))
        ev = ev if ev is not None else _safe_float(candidate_item.get('ev') if candidate_item else allocation_item.get('ev'))
        observed_at_utc = observed_at_utc or (fallback.get('candidate_entry') or {}).get('finished_at_utc') or (fallback.get('allocation_entry') or {}).get('at_utc') or eval_payload.get('evaluated_at_utc') or eval_payload.get('at_utc')

        blockers = _split_blockers(blocker_text)
        blocker_flags = _blocker_flags(blockers)
        cp_meta_missing = _cp_meta_missing(
            gate_fail_detail=';'.join(filter(None, [gate_fail_detail, str(reason or '')])),
            gate_mode=gate_mode,
            blockers=blockers,
        )
        regime_block = str(reason or '').strip().lower() == 'regime_block'
        dominant_reason = _dominant_reason(
            action=action,
            reason=reason,
            cp_meta_missing=cp_meta_missing,
            regime_block=regime_block,
            blocker_flags=blocker_flags,
            stale=stale,
            missing=missing,
        )

        if missing:
            missing_artifact_scopes += 1
            window_state = 'hold'
            recommended_action = 'capture_candidate_artifact'
        elif stale:
            stale_artifact_scopes += 1
            window_state = 'hold'
            recommended_action = 'refresh_decision_artifact'
        elif action in _TRADE_ACTIONS:
            actionable_scopes += 1
            window_state = 'actionable'
            recommended_action = 'capture_practice_evidence'
        elif cp_meta_missing:
            hold_scopes += 1
            cp_meta_missing_scopes += 1
            window_state = 'hold'
            recommended_action = 'backfill_cp_meta'
        elif regime_block:
            watch_scopes += 1
            regime_block_scopes += 1
            window_state = 'watch'
            recommended_action = 'wait_regime_rescan'
        else:
            hold_scopes += 1
            window_state = 'hold'
            recommended_action = 'wait_signal_and_rescan'

        if blocker_flags['cp_reject']:
            cp_reject_scopes += 1
        if blocker_flags['below_ev_threshold']:
            threshold_block_scopes += 1
        if blocker_flags['not_in_topk_today']:
            topk_suppressed_scopes += 1
        if blocker_flags['gate_fail_closed'] or cp_meta_missing:
            gate_fail_closed_scopes += 1

        issue_categories = aggregate_provider_issue_texts([reason, blocker_text, gate_fail_detail, gate_mode, observed_at_utc])
        artifact_score = _score_entry(
            action=action,
            cp_meta_missing=cp_meta_missing,
            regime_block=regime_block,
            blocker_flags=blocker_flags,
            conf=conf,
            score=score,
            ev=ev,
            stale=stale,
            missing=missing,
        )

        scope_results.append({
            'scope': {
                'asset': str(scope.asset),
                'interval_sec': int(scope.interval_sec),
                'scope_tag': str(scope.scope_tag),
            },
            'decision_path': str(path),
            'exists': not missing,
            'missing': missing,
            'artifact_sources': list(fallback.get('artifact_sources') or []),
            'pack_available': bool(isinstance(fallback.get('pack_payload'), Mapping)),
            'eval_available': bool(isinstance(fallback.get('eval_payload'), Mapping)),
            'age_sec': age_sec,
            'max_age_sec': int(max(1, int(decision_max_age_sec))),
            'stale': stale,
            'window_state': window_state,
            'recommended_action': recommended_action,
            'artifact_score': artifact_score,
            'candidate_action': action,
            'candidate_reason': reason,
            'candidate_blockers': blockers,
            'cp_meta_missing': cp_meta_missing,
            'gate_fail_detail': gate_fail_detail or None,
            'gate_mode': gate_mode or None,
            'regime_block': regime_block,
            'dominant_reason': dominant_reason,
            'blocker_flags': blocker_flags,
            'conf': conf,
            'score': score,
            'ev': ev,
            'observed_at_utc': observed_at_utc,
            'issue_categories': issue_categories,
            'last_json': payload,
        })

    scope_results.sort(key=lambda item: (item.get('artifact_score') or 0.0), reverse=True)
    best_actionable = next((item for item in scope_results if item.get('window_state') == 'actionable'), None)
    best_watch = next((item for item in scope_results if item.get('window_state') == 'watch'), None)
    best_hold = next((item for item in scope_results if item.get('window_state') == 'hold'), None)
    best_observed = best_actionable or best_watch or best_hold
    dominant_nontrade_reason = str((best_observed or {}).get('dominant_reason') or '') or None

    if actionable_scopes:
        severity = 'ok'
        recommended_action = 'capture_practice_evidence'
        headline = 'Há artifacts com ação operacional candidata; capture evidência no melhor scope antes de qualquer mudança de envelope.'
    elif missing_artifact_scopes or stale_artifact_scopes:
        severity = 'warn'
        recommended_action = 'refresh_artifacts_and_rescan'
        headline = 'Há scopes sem artifact recente de decisão; atualize observe/candidate artifacts antes de confiar totalmente na leitura multi-scope.'
    elif cp_meta_missing_scopes == len(scope_results):
        severity = 'warn'
        recommended_action = 'backfill_cp_meta'
        headline = 'Todos os scopes auditados estão em fail-closed por ausência de cp_meta; trate isso como bloqueio de inteligência/meta.'
    elif best_watch and str(best_watch.get('dominant_reason') or '') == 'regime_block' and cp_meta_missing_scopes > 0:
        severity = 'ok'
        recommended_action = 'wait_regime_rescan_backfill_cp_meta'
        headline = 'O melhor scope está em regime_block, enquanto scopes secundários ainda sofrem com cp_meta ausente.'
    elif regime_block_scopes == len(scope_results):
        severity = 'ok'
        recommended_action = 'wait_regime_rescan'
        headline = 'Todos os scopes auditados estão em no-trade por regime_block; aguarde novo candle/janela.'
    elif threshold_block_scopes or topk_suppressed_scopes or cp_reject_scopes:
        severity = 'ok'
        recommended_action = 'wait_signal_and_rescan'
        headline = 'Os scopes estão saudáveis, mas foram suprimidos por threshold/top-k/cp_reject no candle atual.'
    else:
        severity = 'ok'
        recommended_action = 'rescan_next_candle'
        headline = 'Sem ação imediata; artifacts de sinal indicam estado saudável de watch/hold.'

    actions = [headline]
    if cp_meta_missing_scopes:
        actions.append('Há scopes com cp_meta ausente; trate isso como backfill/auditoria de inteligência, não como falha do provider.')
    if regime_block_scopes:
        actions.append('Regime block é no-trade operacional; reavalie no próximo candle antes de alterar o envelope do canary.')
    if stale_artifact_scopes or missing_artifact_scopes:
        actions.append('Atualize os artifacts de decisão dos scopes faltantes/stale antes de concluir que o canary inteiro está em no-trade saudável.')
    if best_actionable:
        sc = best_actionable['scope']
        actions.append(f'Best actionable scope: {sc["scope_tag"]}. Priorize evidência de execução nesse scope.')
    elif best_watch:
        sc = best_watch['scope']
        actions.append(f'Best watch scope: {sc["scope_tag"]}. Monitore esse scope primeiro para o próximo candle.')
    elif best_hold:
        sc = best_hold['scope']
        actions.append(f'Best hold scope: {sc["scope_tag"]}. Use-o como diagnóstico, não como candidato imediato.')

    payload = {
        'kind': 'signal_artifact_audit',
        'at_utc': _now_iso(),
        'ok': True,
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'all_scopes': bool(all_scopes),
        'summary': {
            'full_scope_count': len(scopes),
            'actionable_scopes': actionable_scopes,
            'watch_scopes': watch_scopes,
            'hold_scopes': hold_scopes,
            'missing_artifact_scopes': missing_artifact_scopes,
            'stale_artifact_scopes': stale_artifact_scopes,
            'cp_meta_missing_scopes': cp_meta_missing_scopes,
            'regime_block_scopes': regime_block_scopes,
            'cp_reject_scopes': cp_reject_scopes,
            'threshold_block_scopes': threshold_block_scopes,
            'topk_suppressed_scopes': topk_suppressed_scopes,
            'gate_fail_closed_scopes': gate_fail_closed_scopes,
            'best_actionable_scope_tag': (best_actionable or {}).get('scope', {}).get('scope_tag') if best_actionable else None,
            'best_watch_scope_tag': (best_watch or {}).get('scope', {}).get('scope_tag') if best_watch else None,
            'best_hold_scope_tag': (best_hold or {}).get('scope', {}).get('scope_tag') if best_hold else None,
            'dominant_nontrade_reason': dominant_nontrade_reason,
            'recommended_action': recommended_action,
        },
        'best_actionable_scope': best_actionable,
        'best_watch_scope': best_watch,
        'best_hold_scope': best_hold,
        'scope_results': scope_results,
        'actions': dedupe_actions(actions),
    }
    if write_artifact:
        out = repo / 'runs' / 'control' / '_repo' / 'signal_artifact_audit.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload


__all__ = ['build_signal_artifact_audit_payload']
