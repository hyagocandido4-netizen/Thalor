from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.ops.diagnostic_utils import dedupe_actions, load_selected_scopes  # noqa: E402
from natbin.ops.provider_session_governor import build_provider_session_governor_payload  # noqa: E402
from natbin.utils.provider_issue_taxonomy import aggregate_provider_issue_texts  # noqa: E402
from natbin.portfolio.runtime_budget import select_governed_items  # noqa: E402
try:
    from _capture_json import select_last_dict  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover - package import path
    from scripts.tools._capture_json import select_last_dict  # type: ignore  # noqa: E402

_TRADE_ACTIONS = {'CALL', 'PUT', 'BUY', 'SELL', 'UP', 'DOWN'}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _find_python(repo_root: Path) -> str:
    for candidate in (repo_root / '.venv' / 'Scripts' / 'python.exe', repo_root / '.venv' / 'bin' / 'python'):
        if candidate.exists():
            return str(candidate)
    return shutil.which('python') or sys.executable


def _build_env(repo_root: Path, config_path: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    src = repo_root / 'src'
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(src) if not existing else f"{src}{os.pathsep}{existing}"
    env['THALOR_REPO_ROOT'] = str(repo_root)
    if config_path is not None:
        env['THALOR_CONFIG'] = str(config_path)
        env['THALOR_CONFIG_PATH'] = str(config_path)
    return env


def _extract_last_json_dict(text: str) -> dict[str, Any] | None:
    payload = select_last_dict(text)
    return payload if isinstance(payload, dict) else None


def _candidate_cmd(repo: Path, cfg_path: Path, *, asset: str, interval_sec: int) -> list[str]:
    return [
        _find_python(repo), '-m', 'natbin.runtime_app', '--repo-root', str(repo), '--config', str(cfg_path),
        'asset', 'candidate', '--asset', asset, '--interval-sec', str(int(interval_sec)), '--json'
    ]


def _select_governed_scopes(ordered_scopes: list[Any], *, repo: Path, budget: int, scope_order: str) -> tuple[list[Any], dict[str, Any]]:
    return select_governed_items(ordered_scopes, repo_root=repo, budget=budget, scope_order=scope_order)


def _run_cmd(cmd: list[str], *, cwd: Path, env: dict[str, str], timeout_sec: int) -> dict[str, Any]:
    started = time.monotonic()
    timed_out = False
    stdout = ''
    stderr = ''
    returncode: int | None = None
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=max(1, int(timeout_sec)))
        stdout = proc.stdout or ''
        stderr = proc.stderr or ''
        returncode = int(proc.returncode)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = (exc.stdout or b'').decode('utf-8', errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        stderr = (exc.stderr or b'').decode('utf-8', errors='replace') if isinstance(exc.stderr, bytes) else (exc.stderr or '')
    return {
        'returncode': returncode,
        'timed_out': timed_out,
        'duration_sec': round(time.monotonic() - started, 3),
        'stdout': stdout,
        'stderr': stderr,
        'last_json': _extract_last_json_dict(stdout),
    }


def _score_candidate(action: str | None, cp_meta_missing: bool, regime_block: bool, blockers: list[str], conf: float | None, score: float | None, ev: float | None) -> float:
    value = 50.0
    if action in _TRADE_ACTIONS:
        value += 35.0
    elif str(action or '').upper() == 'HOLD':
        value -= 5.0
    if cp_meta_missing:
        value -= 28.0
    if regime_block:
        value -= 18.0
    value -= min(24.0, 6.0 * len(blockers))
    if conf is not None:
        value += max(-4.0, min(8.0, (float(conf) - 0.5) * 40.0))
    if score is not None:
        value += max(-6.0, min(12.0, float(score) * 12.0))
    if ev is not None:
        value += max(-8.0, min(8.0, float(ev) * 4.0))
    return round(max(0.0, min(100.0, value)), 1)


def _blocker_flags(blockers: list[str]) -> dict[str, bool]:
    items = {str(item or '').strip().lower() for item in blockers if str(item or '').strip()}
    return {
        'cp_reject': 'cp_reject' in items,
        'below_ev_threshold': 'below_ev_threshold' in items,
        'not_in_topk_today': 'not_in_topk_today' in items,
        'gate_fail_closed': 'gate_fail_closed' in items,
    }


def _dominant_reason(*, action: str | None, cp_meta_missing: bool, regime_block: bool, blocker_flags: dict[str, bool], candidate_error: bool) -> str:
    if candidate_error:
        return 'candidate_error'
    if action in _TRADE_ACTIONS:
        return 'actionable'
    if cp_meta_missing:
        return 'cp_meta_missing'
    if regime_block:
        return 'regime_block'
    if bool(blocker_flags.get('below_ev_threshold')):
        return 'below_ev_threshold'
    if bool(blocker_flags.get('not_in_topk_today')):
        return 'not_in_topk_today'
    if bool(blocker_flags.get('cp_reject')):
        return 'cp_reject'
    if bool(blocker_flags.get('gate_fail_closed')):
        return 'gate_fail_closed'
    if str(action or '').upper() == 'HOLD':
        return 'hold'
    return 'unknown'


def _is_healthy_waiting(*, actionable_scopes: int, candidate_error_scopes: int, cp_meta_missing_scopes: int) -> bool:
    return actionable_scopes == 0 and candidate_error_scopes == 0 and cp_meta_missing_scopes == 0


def _read_best_first(repo: Path, scopes: list[Any]) -> list[Any]:
    path = repo / 'runs' / 'control' / '_repo' / 'evidence_window_scan.json'
    if not path.exists():
        return list(scopes)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return list(scopes)
    items = list(payload.get('scope_results') or []) if isinstance(payload, dict) else []
    score_map = {str((item.get('scope') or {}).get('scope_tag') or ''): float(item.get('score') or 0.0) for item in items if isinstance(item, dict)}
    return sorted(list(scopes), key=lambda scope: (-score_map.get(str(scope.scope_tag), 0.0), str(scope.scope_tag)))


def build_signal_proof_payload(
    *,
    repo_root: str | Path='.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = True,
    timeout_sec: int = 300,
    refresh_stability: bool = False,
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
            'kind': 'portfolio_canary_signal_proof',
            'at_utc': _now_iso(),
            'ok': False,
            'severity': 'error',
            'repo_root': str(repo),
            'config_path': str(cfg_path),
            'message': 'no_scopes_selected',
            'scope_results': [],
        }

    governor_payload = build_provider_session_governor_payload(
        repo_root=repo,
        config_path=cfg_path,
        asset=asset,
        interval_sec=interval_sec,
        all_scopes=all_scopes,
        active_provider_probe=True,
        refresh_stability=True,
        write_artifact=False,
    )
    governor = dict(governor_payload.get('governor') or {})
    sleep_ms = int(governor.get('sleep_between_candidate_scopes_ms') or 0)
    max_candidate_scopes = int(governor.get('max_candidate_scopes_per_run') or len(scopes))

    env = _build_env(repo, cfg_path)
    ordered_scopes = _read_best_first(repo, scopes)
    selected_scopes, budget_meta = _select_governed_scopes(
        ordered_scopes,
        repo=repo,
        budget=max_candidate_scopes,
        scope_order=str(governor.get('scope_order') or 'best_first'),
    )
    scope_results: list[dict[str, Any]] = []
    cp_meta_missing_scopes = 0
    regime_block_scopes = 0
    actionable_scopes = 0
    watch_scopes = 0
    hold_scopes = 0
    candidate_error_scopes = 0
    cp_reject_scopes = 0
    threshold_block_scopes = 0
    topk_suppressed_scopes = 0
    gate_fail_closed_scopes = 0

    for idx, scope in enumerate(selected_scopes):
        if idx > 0 and sleep_ms > 0:
            time.sleep(float(sleep_ms) / 1000.0)
        cmd = _candidate_cmd(repo, cfg_path, asset=str(scope.asset), interval_sec=int(scope.interval_sec))
        result = _run_cmd(cmd, cwd=repo, env=env, timeout_sec=timeout_sec)
        payload = result.get('last_json') if isinstance(result.get('last_json'), dict) else {}
        candidate = payload.get('candidate') if isinstance(payload.get('candidate'), dict) else {}
        raw = candidate.get('raw') if isinstance(candidate.get('raw'), dict) else {}
        action = str(candidate.get('action') or raw.get('action') or '').upper() or None
        reason = str(candidate.get('reason') or raw.get('reason') or '') or None
        blocker_text = str(candidate.get('blockers') or raw.get('blockers') or '')
        blockers = [b for b in blocker_text.split(';') if b]
        blocker_flags = _blocker_flags(blockers)
        gate_fail_detail = str(raw.get('gate_fail_detail') or '')
        gate_mode = str(raw.get('gate_mode') or '')
        cp_meta_missing = ('cp_fail_closed_missing_cp_meta' in gate_fail_detail) or ('cp_fail_closed_missing_cp_meta' in gate_mode)
        regime_block = str(reason or '').strip().lower() == 'regime_block'
        conf = candidate.get('conf') if candidate.get('conf') is not None else raw.get('conf')
        score = candidate.get('score') if candidate.get('score') is not None else raw.get('score')
        ev = candidate.get('ev') if candidate.get('ev') is not None else raw.get('ev')
        signal_score = _score_candidate(action, cp_meta_missing, regime_block, blockers, conf if isinstance(conf, (int, float)) else None, score if isinstance(score, (int, float)) else None, ev if isinstance(ev, (int, float)) else None)
        issue_categories = aggregate_provider_issue_texts([result['stderr'], reason, gate_fail_detail, gate_mode, blocker_text])
        dominant_reason = _dominant_reason(
            action=action,
            cp_meta_missing=cp_meta_missing,
            regime_block=regime_block,
            blocker_flags=blocker_flags,
            candidate_error=(result['returncode'] != 0),
        )

        if blocker_flags['cp_reject']:
            cp_reject_scopes += 1
        if blocker_flags['below_ev_threshold']:
            threshold_block_scopes += 1
        if blocker_flags['not_in_topk_today']:
            topk_suppressed_scopes += 1
        if blocker_flags['gate_fail_closed'] or cp_meta_missing:
            gate_fail_closed_scopes += 1

        if result['returncode'] != 0:
            candidate_error_scopes += 1
            window_state = 'error'
            recommended_action = 'inspect_candidate_stderr'
        elif action in _TRADE_ACTIONS:
            actionable_scopes += 1
            window_state = 'actionable'
            recommended_action = 'capture_practice_evidence'
        elif cp_meta_missing:
            hold_scopes += 1
            cp_meta_missing_scopes += 1
            window_state = 'hold'
            recommended_action = 'audit_cp_meta'
        elif regime_block:
            watch_scopes += 1
            regime_block_scopes += 1
            window_state = 'watch'
            recommended_action = 'wait_regime_rescan'
        else:
            hold_scopes += 1
            window_state = 'hold'
            recommended_action = 'wait_signal_and_rescan'

        scope_results.append({
            'scope': {'asset': str(scope.asset), 'interval_sec': int(scope.interval_sec), 'scope_tag': str(scope.scope_tag)},
            'command': cmd,
            'returncode': result['returncode'],
            'timed_out': result['timed_out'],
            'duration_sec': result['duration_sec'],
            'window_state': window_state,
            'recommended_action': recommended_action,
            'signal_score': signal_score,
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
            'issue_categories': issue_categories,
            'stdout_tail': '\n'.join(str(result['stdout']).splitlines()[-12:]),
            'stderr_tail': '\n'.join(str(result['stderr']).splitlines()[-12:]),
            'last_json': payload,
        })

    scope_results.sort(key=lambda item: (item.get('signal_score') or 0.0), reverse=True)
    best_actionable = next((item for item in scope_results if item.get('window_state') == 'actionable'), None)
    best_watch = next((item for item in scope_results if item.get('window_state') == 'watch'), None)
    best_hold = next((item for item in scope_results if item.get('window_state') == 'hold'), None)
    best_observed = best_actionable or best_watch or best_hold
    dominant_nontrade_reason = str((best_observed or {}).get('dominant_reason') or '') or None
    healthy_waiting_signal = _is_healthy_waiting(
        actionable_scopes=actionable_scopes,
        candidate_error_scopes=candidate_error_scopes,
        cp_meta_missing_scopes=cp_meta_missing_scopes,
    )

    if candidate_error_scopes:
        severity = 'warn' if (actionable_scopes or watch_scopes or hold_scopes) else 'error'
    elif actionable_scopes:
        severity = 'ok'
    else:
        severity = 'warn'

    if actionable_scopes:
        recommended_action = 'capture_practice_evidence'
        headline = 'Há pelo menos um scope com ação operacional candidata; capture a evidência do melhor scope.'
    elif cp_meta_missing_scopes == len(scope_results) and scope_results:
        recommended_action = 'audit_cp_meta'
        headline = 'Todos os scopes ficaram em fail-closed por ausência de cp_meta; trate isso como bloqueio de inteligência, não de provider.'
    elif best_watch and str(best_watch.get('dominant_reason') or '') == 'regime_block' and cp_meta_missing_scopes > 0:
        recommended_action = 'wait_regime_rescan_backfill_cp_meta'
        headline = 'O melhor scope está apenas em regime_block, mas há scopes secundários suprimidos por cp_meta ausente; reavalie o melhor scope e faça backfill do restante.'
    elif cp_meta_missing_scopes > 0:
        recommended_action = 'audit_cp_meta_and_rescan'
        headline = 'Há scopes saudáveis, mas parte da carteira está sendo suprimida por cp_meta ausente.'
    elif regime_block_scopes == len(scope_results) and scope_results:
        recommended_action = 'wait_regime_rescan'
        headline = 'Todos os scopes estão em no-trade operacional por regime_block; aguarde novo candle/janela.'
    elif healthy_waiting_signal:
        recommended_action = 'wait_signal_and_rescan'
        headline = 'Sem trade acionável neste candle; o canary está saudável e aguardando novo sinal.'
    else:
        recommended_action = 'rescan_next_candle'
        headline = 'Sem ação imediata; carteira em watch/hold read-only.'

    actions = [headline]
    if cp_meta_missing_scopes:
        actions.append('Rode o signal proof bundle para separar claramente ausência de sinal de bloqueio por cp_meta ausente.')
    if regime_block_scopes:
        actions.append('Regime block não é defeito de execução; trate como no-trade operacional e reavalie no próximo candle.')
    if best_actionable:
        sc = best_actionable['scope']
        actions.append(f'Best actionable scope: {sc["scope_tag"]}. Capture evidência com asset candidate/observe desse scope antes de ampliar execução.')
    elif best_watch:
        sc = best_watch['scope']
        actions.append(f'Best watch scope: {sc["scope_tag"]}. Monitore esse scope primeiro para a próxima janela.')
    elif best_hold:
        sc = best_hold['scope']
        actions.append(f'Best hold scope: {sc["scope_tag"]}. O scope está em hold estrutural; use-o apenas como diagnóstico, não como candidato imediato.')
    if governor.get('mode') == 'serial_guarded':
        actions.append('Provider degradado: mantenha candidate scan serializado e preserve top-1/single-position.')

    payload = {
        'kind': 'portfolio_canary_signal_proof',
        'at_utc': _now_iso(),
        'ok': True,
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(cfg_path),
        'all_scopes': bool(all_scopes),
        'governor': governor_payload,
        'summary': {
            'scope_count': len(scope_results),
            'actionable_scopes': actionable_scopes,
            'watch_scopes': watch_scopes,
            'hold_scopes': hold_scopes,
            'candidate_error_scopes': candidate_error_scopes,
            'cp_meta_missing_scopes': cp_meta_missing_scopes,
            'regime_block_scopes': regime_block_scopes,
            'cp_reject_scopes': cp_reject_scopes,
            'threshold_block_scopes': threshold_block_scopes,
            'topk_suppressed_scopes': topk_suppressed_scopes,
            'gate_fail_closed_scopes': gate_fail_closed_scopes,
            'healthy_waiting_signal': healthy_waiting_signal,
            'best_actionable_scope_tag': (best_actionable or {}).get('scope', {}).get('scope_tag') if best_actionable else None,
            'best_watch_scope_tag': (best_watch or {}).get('scope', {}).get('scope_tag') if best_watch else None,
            'best_hold_scope_tag': (best_hold or {}).get('scope', {}).get('scope_tag') if best_hold else None,
            'dominant_nontrade_reason': dominant_nontrade_reason,
            'recommended_action': recommended_action,
            'governor_mode': governor.get('mode'),
            'budget_scope_count': budget_meta.get('budget_scope_count'),
            'scanned_scope_count': budget_meta.get('scanned_scope_count'),
            'skipped_scope_count': budget_meta.get('skipped_scope_count'),
            'scope_cursor_before': budget_meta.get('scope_cursor_before'),
            'scope_cursor_after': budget_meta.get('scope_cursor_after'),
        },
        'best_actionable_scope': best_actionable,
        'best_watch_scope': best_watch,
        'best_hold_scope': best_hold,
        'scope_results': scope_results,
        'actions': dedupe_actions(actions),
    }
    artifact = repo / 'runs' / 'control' / '_repo' / 'portfolio_canary_signal_proof.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
    artifact.write_text(serialized, encoding='utf-8')
    (artifact.parent / 'portfolio_canary_signal_scan.json').write_text(serialized, encoding='utf-8')
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Read-only signal proof do portfolio canary: roda asset candidate nos scopes e explica por que há ou não trade acionável.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--asset', default=None)
    ap.add_argument('--interval-sec', type=int, default=None)
    ap.add_argument('--all-scopes', action='store_true')
    ap.add_argument('--timeout-sec', type=int, default=300)
    ap.add_argument('--refresh-stability', action='store_true')
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_signal_proof_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        all_scopes=bool(ns.all_scopes),
        timeout_sec=int(ns.timeout_sec or 300),
        refresh_stability=bool(ns.refresh_stability),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
