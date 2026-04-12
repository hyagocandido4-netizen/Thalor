from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

from natbin.domain.gate_meta import GATE_VERSION, compute_scores, train_base_cal_iso_meta

from ...runtime.gates.cpreg import maybe_apply_cp_alpha_env
from ...runtime.observability import (
    append_incident_event,
    build_incident_from_decision,
    write_detailed_decision_snapshot,
    write_latest_decision_snapshot,
)
from .config import load_cfg
from .model_cache import cache_supports_gate, feat_hash, get_model_version, load_cache, save_cache, should_retrain
from .selection import make_regime_mask, resolve_observer_settings
from .signal_store import (
    already_executed,
    already_state_only,
    append_csv,
    executed_today_count,
    heal_state_from_signals,
    last_executed_ts,
    mark_executed,
    signals_db_path,
    write_sqlite_signal,
)
from .summary import write_daily_summary


REQUIRED_COLUMNS = ('ts', 'y_open_close', 'f_vol48', 'f_bb_width20', 'f_atr14')


def _cache_refresh_payload(
    *,
    asset: str,
    interval_sec: int,
    settings,
    cal: object,
    iso: object,
    meta_model: object,
    train_rows: int,
    train_end_ts: int,
    best_source: str,
    fhash: str,
    refresh_reason: str,
) -> dict[str, object]:
    cp_available = bool(getattr(meta_model, 'cp', None) is not None) if meta_model is not None else False
    meta_iso_available = bool(getattr(meta_model, 'iso', None) is not None) if meta_model is not None else False
    return {
        'cal': cal,
        'iso': iso,
        'meta_model': meta_model,
        'meta': {
            'asset': asset,
            'interval_sec': int(interval_sec),
            'created_at': datetime.now(tz=ZoneInfo('UTC')).isoformat(timespec='seconds'),
            'train_rows': int(train_rows),
            'train_end_ts': int(train_end_ts),
            'best_source': best_source,
            'feat_hash': fhash,
            'gate_version': GATE_VERSION,
            'meta_model': settings.meta_model_type,
            'model_version': get_model_version(),
            'gate_mode_requested': settings.gate_mode_requested,
            'cp_available': bool(cp_available),
            'meta_iso_available': bool(meta_iso_available),
            'refresh_reason': str(refresh_reason or 'schedule'),
        },
    }


def _load_dataset(dataset_path: str) -> pd.DataFrame:
    df = pd.read_csv(dataset_path)
    if len(df) == 0:
        raise ValueError('Dataset vazio.')
    for req in REQUIRED_COLUMNS:
        if req not in df.columns:
            raise ValueError(f'Dataset sem coluna obrigatoria: {req}')
    return df


def _dataset_day_slice(df: pd.DataFrame, *, day: str, tz: ZoneInfo) -> pd.DataFrame:
    dt_all = pd.to_datetime(df['ts'], unit='s', utc=True).dt.tz_convert(tz)
    df_day = df.loc[dt_all.dt.strftime('%Y-%m-%d') == day].copy()
    if len(df_day) == 0:
        raise ValueError('Sem dados no dia atual no dataset.')
    return df_day


def _normalize_gate_fail_closed(
    *,
    score: np.ndarray,
    gate_used: str,
    gate_mode_requested: str,
    gate_fail_closed_enabled: bool,
) -> tuple[np.ndarray, bool, str]:
    gate_fail_closed_active = False
    gate_fail_detail = ''
    gate_used_s = str(gate_used or '').strip()
    score_out = np.asarray(score, dtype=float)
    if gate_fail_closed_enabled and gate_mode_requested in ('meta', 'cp'):
        legit = False
        if gate_mode_requested == 'meta':
            legit = gate_used_s in ('meta', 'meta_iso')
        elif gate_mode_requested == 'cp':
            cp_bootstrap_fallback_allowed = str(os.getenv('CP_BOOTSTRAP_FALLBACK', 'off') or 'off').strip().lower() not in ('', '0', 'false', 'off', 'none', 'fail_closed')
            legit = (
                gate_used_s.startswith('cp_')
                and (not gate_used_s.startswith('cp_fallback'))
                and (not gate_used_s.startswith('cp_fail_closed'))
            )
            if gate_used_s.startswith('cp_fallback') and cp_bootstrap_fallback_allowed:
                legit = True
        if not legit:
            gate_fail_closed_active = True
            gate_fail_detail = gate_used_s or 'unknown'
            score_out = np.zeros_like(score_out, dtype=float)
    return score_out, gate_fail_closed_active, gate_fail_detail


def main() -> None:
    cfg, best = load_cfg()
    if not best:
        raise RuntimeError(
            'Nao achei configuracao de decisao na config resolvida. Defina decision.threshold/tune_dir/bounds no config selecionado.'
        )

    tz = ZoneInfo(cfg.get('data', {}).get('timezone', 'UTC'))
    asset = cfg.get('data', {}).get('asset', 'UNKNOWN')
    interval_sec = int(cfg.get('data', {}).get('interval_sec', 300))
    settings = resolve_observer_settings(cfg, best)

    df = _load_dataset(settings.dataset_path)
    feat = [c for c in df.columns if c.startswith('f_')]

    last_ts = int(df['ts'].iloc[-1])
    last_dt = datetime.fromtimestamp(last_ts, tz=tz)
    day = last_dt.strftime('%Y-%m-%d')
    dt_local = last_dt.strftime('%Y-%m-%d %H:%M:%S')

    min_train_rows = int(os.getenv('MIN_TRAIN_ROWS', '3000'))
    tail_holdout = int(os.getenv('TRAIN_TAIL_HOLDOUT', '200'))
    cut = max(min_train_rows, len(df) - tail_holdout)
    train = df.iloc[:cut].copy()

    train_end_ts = int(train['ts'].iloc[-1])
    train_rows = int(len(train))
    fhash = feat_hash(feat)
    best_source = settings.tune_dir or 'unknown'

    os.environ.setdefault('SIGNALS_INTERVAL_SEC', str(interval_sec))
    cache = load_cache(asset, interval_sec)
    meta = (cache or {}).get('meta') if cache else None
    cache_gate_compatible = cache_supports_gate(cache, settings.gate_mode_requested)
    refresh_reason = ''
    if cache is None:
        refresh_reason = 'cache_missing'
    elif not cache_gate_compatible:
        refresh_reason = f'cache_incompatible_gate:{settings.gate_mode_requested}'
    elif should_retrain(
        meta,
        train_end_ts=train_end_ts,
        best_source=best_source,
        fhash=fhash,
        interval_sec=interval_sec,
        meta_model_type=settings.meta_model_type,
    ):
        refresh_reason = 'schedule'

    if refresh_reason:
        cal, iso, meta_model = train_base_cal_iso_meta(
            train_df=train,
            feat_cols=feat,
            tz=tz,
            meta_model_type=settings.meta_model_type,
        )
        payload = _cache_refresh_payload(
            asset=asset,
            interval_sec=interval_sec,
            settings=settings,
            cal=cal,
            iso=iso,
            meta_model=meta_model,
            train_rows=train_rows,
            train_end_ts=train_end_ts,
            best_source=best_source,
            fhash=fhash,
            refresh_reason=refresh_reason,
        )
        payload['meta']['created_at'] = datetime.now(tz=tz).isoformat(timespec='seconds')
        save_cache(asset, interval_sec, payload)
        cache = payload
        meta = payload['meta']
        print(f'[P31] observer_cache_refresh reason={refresh_reason}')

    if cache is None:
        raise RuntimeError('observer_cache_unavailable')

    cal = cache['cal']
    iso = cache.get('iso', None)
    meta_model = cache.get('meta_model', None)

    df_day = _dataset_day_slice(df, day=day, tz=tz)

    cp_alpha_applied: float | None = None
    executed_before = executed_today_count(asset, interval_sec, day)
    if settings.gate_mode_requested == 'cp':
        cp_alpha_applied = maybe_apply_cp_alpha_env(last_dt, executed_today=executed_before)

    proba, conf, score_raw, gate_used = compute_scores(
        df=df_day,
        feat_cols=feat,
        tz=tz,
        cal_model=cal,
        iso=iso,
        meta_model=meta_model,
        gate_mode=settings.gate_mode_requested,
    )

    score, gate_fail_closed_active, gate_fail_detail = _normalize_gate_fail_closed(
        score=score_raw,
        gate_used=str(gate_used or ''),
        gate_mode_requested=settings.gate_mode_requested,
        gate_fail_closed_enabled=bool(os.getenv('GATE_FAIL_CLOSED', '1').strip().lower() not in ('0', 'false', 'f', 'no', 'n', 'off')),
    )

    mask = make_regime_mask(df_day, settings.bounds) if settings.bounds else np.ones(len(df_day), dtype=bool)
    ev_metric = score * settings.payout - (1.0 - score)
    if settings.thresh_on == 'score':
        metric = score
    elif settings.thresh_on == 'conf':
        metric = conf
    else:
        metric = ev_metric

    mask_gate = mask if settings.regime_mode == 'hard' else np.ones(len(mask), dtype=bool)
    cand = mask_gate & (metric >= settings.threshold)

    rank = score * settings.payout - (1.0 - score)
    order = np.argsort(-rank, kind='mergesort')
    if settings.rolling_min > 0:
        start_ts = int(last_ts) - int(settings.rolling_min) * 60
        win_mask = df_day['ts'].to_numpy(dtype=int) >= start_ts
    else:
        win_mask = np.ones(len(df_day), dtype=bool)
    sel = order[(cand & win_mask)[order]]
    topk = sel[: settings.k]

    now_i = len(df_day) - 1
    in_topk = bool(now_i in set(topk.tolist()))
    rank_in_day = int(np.where(topk == now_i)[0][0] + 1) if in_topk else -1

    executed_today = executed_before
    pacing_allowed = int(settings.k)
    if settings.pacing_enabled and int(settings.k) > 1:
        dt_now = pd.Timestamp(int(last_ts), unit='s', tz='UTC').tz_convert(tz)
        sec_of_day = int(dt_now.hour) * 3600 + int(dt_now.minute) * 60 + int(dt_now.second)
        frac_day = min(1.0, max(0.0, float(sec_of_day) / 86400.0))
        pacing_allowed = min(int(settings.k), max(1, int(np.floor(float(settings.k) * frac_day)) + 1))

    cp_rejected_now = (
        (not gate_fail_closed_active)
        and str(gate_used or '').startswith('cp_')
        and (not str(gate_used or '').startswith('cp_fallback'))
        and (float(score[now_i]) <= 0.0)
    )

    action = 'HOLD'
    reason = 'ok'
    blockers: list[str] = []

    market_context_stale_now = bool(settings.market_context_fail_closed and settings.market_context_stale)
    hard_regime_block = (settings.regime_mode == 'hard') and (not bool(mask[now_i]))

    threshold_reason = ''
    if float(metric[now_i]) < settings.threshold:
        if settings.thresh_on == 'score':
            threshold_reason = 'below_score_threshold'
        elif settings.thresh_on == 'conf':
            threshold_reason = 'below_conf_threshold'
        else:
            threshold_reason = 'below_ev_threshold'

    pacing_reason = ''
    if settings.pacing_enabled and executed_today >= pacing_allowed:
        pacing_reason = f'pacing_day_progress({pacing_allowed}/{settings.k})'

    if market_context_stale_now:
        blockers.append('market_context_stale')
    if not settings.market_open:
        blockers.append('market_closed')
    if executed_today >= settings.k:
        blockers.append('max_k_reached')
    if already_executed(asset, interval_sec, day, last_ts):
        blockers.append('already_emitted_for_ts')
    if hard_regime_block:
        blockers.append('regime_block')
    if pacing_reason:
        blockers.append(pacing_reason)
    if gate_fail_closed_active:
        blockers.append('gate_fail_closed')
    if cp_rejected_now:
        blockers.append('cp_reject')
    if threshold_reason:
        blockers.append(threshold_reason)
    if not in_topk:
        blockers.append('not_in_topk_today')

    cooldown_reason = ''
    if settings.min_gap_min > 0 and executed_today > 0:
        prev_ts = last_executed_ts(asset, interval_sec, day)
        if prev_ts is not None and (int(last_ts) - int(prev_ts)) < int(settings.min_gap_min) * 60:
            cooldown_reason = f'cooldown_min_gap({settings.min_gap_min}m)'

    if market_context_stale_now:
        reason = 'market_context_stale'
    elif not settings.market_open:
        reason = 'market_closed'
    elif executed_today >= settings.k:
        reason = 'max_k_reached'
    elif already_executed(asset, interval_sec, day, last_ts):
        reason = 'already_emitted_for_ts'
    elif pacing_reason:
        reason = pacing_reason
    elif hard_regime_block:
        reason = 'regime_block'
    elif gate_fail_closed_active:
        reason = 'gate_fail_closed'
    elif cp_rejected_now:
        reason = 'cp_reject'
    elif threshold_reason:
        reason = threshold_reason
    elif not in_topk:
        reason = 'not_in_topk_today'
    elif cooldown_reason:
        reason = cooldown_reason
        blockers.append(cooldown_reason)

    emitted_now = False
    if reason == 'ok':
        action = 'CALL' if float(proba[now_i]) >= 0.5 else 'PUT'
        reason = 'topk_emit'
        emitted_now = True

    executed_after = int(executed_today) + (1 if emitted_now else 0)
    blockers_csv = ';'.join(dict.fromkeys([b for b in blockers if b and b != reason]))
    budget_left = max(0, int(settings.k) - int(executed_after))
    ev = float(score[now_i]) * settings.payout - (1.0 - float(score[now_i]))

    row = {
        'dt_local': dt_local,
        'day': day,
        'ts': int(last_ts),
        'interval_sec': int(interval_sec),
        'proba_up': float(proba[now_i]),
        'conf': float(conf[now_i]),
        'score': float(score[now_i]),
        'gate_mode': gate_used,
        'gate_mode_requested': settings.gate_mode_requested,
        'gate_fail_closed': int(bool(gate_fail_closed_active)),
        'gate_fail_detail': gate_fail_detail,
        'regime_ok': int(bool(mask[now_i])),
        'thresh_on': settings.thresh_on,
        'threshold': float(settings.threshold),
        'k': int(settings.k),
        'rank_in_day': int(rank_in_day),
        'executed_today': int(executed_after),
        'budget_left': int(budget_left),
        'action': action,
        'reason': reason,
        'blockers': blockers_csv,
        'close': float(df_day['close'].iloc[now_i]) if 'close' in df_day.columns else None,
        'payout': float(settings.payout),
        'ev': float(ev),
        'asset': asset,
        'model_version': str(meta.get('model_version') if meta else get_model_version()),
        'train_rows': int(meta.get('train_rows') if meta else train_rows),
        'train_end_ts': int(meta.get('train_end_ts') if meta else train_end_ts),
        'best_source': str(meta.get('best_source') if meta else best_source),
        'tune_dir': settings.tune_dir,
        'feat_hash': fhash,
        'gate_version': GATE_VERSION,
        'meta_model': settings.meta_model_type,
        'market_context_stale': int(1 if market_context_stale_now else 0),
        'market_context_fail_closed': int(1 if settings.market_context_fail_closed else 0),
        'cp_bootstrap_fallback': str(os.getenv('CP_BOOTSTRAP_FALLBACK', '')).strip().lower() or None,
        'cp_bootstrap_fallback_active': int(1 if str(gate_used or '').startswith('cp_fallback') else 0),
        'cp_available': int(1 if bool(meta.get('cp_available')) else 0) if meta else None,
    }

    write_sqlite_signal(row)
    try:
        out_csv = append_csv(row)
    except Exception as e:
        from .signal_store import resolve_live_signals_csv_path

        out_csv = resolve_live_signals_csv_path(row)
        print(f'[WARN] csv_write failed (non-fatal): {e}')

    if emitted_now:
        heal_state_from_signals(asset, interval_sec, day, ts=int(last_ts))
        if not already_state_only(asset, interval_sec, day, int(last_ts)):
            mark_executed(asset, interval_sec, day, last_ts, action, float(conf[now_i]), float(score[now_i]))
        executed_after = executed_today_count(asset, interval_sec, day)
        row['executed_today'] = int(executed_after)
        row['budget_left'] = max(0, int(settings.k) - int(executed_after))

    summary_path = ''
    try:
        summary_path = write_daily_summary(
            day=day,
            tz=tz,
            asset=asset,
            interval_sec=int(interval_sec),
            dataset_path=settings.dataset_path,
            gate_mode=gate_used,
            meta_model=settings.meta_model_type,
            thresh_on=settings.thresh_on,
            threshold=float(settings.threshold),
            k=int(settings.k),
            payout=float(settings.payout),
        )
    except Exception as e:
        print(f'[WARN] daily_summary failed: {e}')
    if summary_path:
        print(f'summary_ok: {summary_path}')

    latest_snapshot_path = ''
    detailed_snapshot_path = ''
    incident_path = ''
    incident_kind = ''
    try:
        latest_snapshot_path = str(write_latest_decision_snapshot(row))
        detailed = write_detailed_decision_snapshot(row)
        if detailed is not None:
            detailed_snapshot_path = str(detailed)
        incident = build_incident_from_decision(row)
        if incident is not None:
            incident_kind = str(incident.get('incident_type') or '')
            incident_p = append_incident_event(incident)
            incident_path = str(incident_p)
    except Exception as e:
        print(f'[WARN] observability_write failed: {e}')
    else:
        if detailed_snapshot_path or incident_path:
            print(
                f'[P38] observability_ok latest={latest_snapshot_path or "-"} '
                f'detailed={detailed_snapshot_path or "-"} incident={incident_kind or "-"}'
            )

    if cp_alpha_applied is not None:
        print(f'[CPREG] cp_alpha_applied={cp_alpha_applied:.4f} slot={int(executed_before) + 1}')

    print('=== OBSERVE TOPK-PERDAY (latest) ===')
    print(
        {
            'dt_local': row['dt_local'],
            'day': row['day'],
            'ts': row['ts'],
            'proba_up': row['proba_up'],
            'conf': row['conf'],
            'score': row['score'],
            'gate_mode': row['gate_mode'],
            'meta_model': row.get('meta_model'),
            'regime_ok': row['regime_ok'],
            'thresh_on': row['thresh_on'],
            'threshold': row['threshold'],
            'k': row['k'],
            'rank_in_day': row['rank_in_day'],
            'executed_today': row['executed_today'],
            'action': row['action'],
            'reason': row['reason'],
        }
    )
    print(f'csv_ok: {out_csv}')
    print(f'sqlite_ok: {signals_db_path()} (signals_v2)')


__all__ = ['main']
