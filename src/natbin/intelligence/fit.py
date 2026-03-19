
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config.loader import load_thalor_config
from ..config.paths import resolve_config_path, resolve_repo_root
from ..portfolio.models import PortfolioScope
from ..portfolio.paths import resolve_scope_data_paths, resolve_scope_runtime_paths, scope_tag as compute_scope_tag
from .anti_overfit import build_anti_overfit_report, load_json as load_summary_json
from .coverage import build_coverage_profile
from .drift import build_drift_baseline
from .learned_gate import build_training_rows, fit_learned_gate
from .paths import pack_path
from .policy import resolve_scope_policy
from .slot_profile import build_slot_profile


def _find_default_multiwindow_summary(repo_root: Path, cfg: Any) -> Path | None:
    tune_dir = str(getattr(getattr(cfg, 'decision', None), 'tune_dir', '') or '').strip()
    if not tune_dir:
        return None
    p = Path(tune_dir)
    if not p.is_absolute():
        p = repo_root / p
    summary = p / 'summary.json'
    return summary if summary.exists() else None


def fit_intelligence_pack(
    *,
    repo_root: str | Path,
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    lookback_days: int = 14,
    signals_db_path: str | Path | None = None,
    dataset_path: str | Path | None = None,
    multiwindow_summary_path: str | Path | None = None,
    out_path: str | Path | None = None,
) -> tuple[dict[str, Any], Path]:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    cfg = load_thalor_config(config_path=cfg_path, repo_root=root)

    chosen = None
    if asset is not None:
        for item in list(cfg.assets or []):
            if str(item.asset) == str(asset) and (interval_sec is None or int(item.interval_sec) == int(interval_sec)):
                chosen = item
                break
    if chosen is None:
        chosen = list(cfg.assets or [None])[0]
        if chosen is None:
            raise ValueError('config has no assets')
    if interval_sec is not None and int(chosen.interval_sec) != int(interval_sec):
        chosen = chosen.model_copy(update={'interval_sec': int(interval_sec)})
    if asset is not None and str(chosen.asset) != str(asset):
        chosen = chosen.model_copy(update={'asset': str(asset)})

    scope_tag = compute_scope_tag(str(chosen.asset), int(chosen.interval_sec))
    partition = bool(getattr(cfg.multi_asset, 'enabled', False)) and bool(getattr(cfg.multi_asset, 'partition_data_paths', True))
    data_paths = resolve_scope_data_paths(
        root,
        asset=str(chosen.asset),
        interval_sec=int(chosen.interval_sec),
        partition_enable=partition,
        db_template=str(getattr(cfg.multi_asset, 'data_db_template', 'data/market_{scope_tag}.sqlite3')),
        dataset_template=str(getattr(cfg.multi_asset, 'dataset_path_template', 'data/datasets/{scope_tag}/dataset.csv')),
        default_db_path=getattr(cfg.data, 'db_path', 'data/market_otc.sqlite3'),
        default_dataset_path=getattr(cfg.data, 'dataset_path', 'data/dataset_phase2.csv'),
    )
    runtime_paths = resolve_scope_runtime_paths(root, scope_tag=scope_tag, partition_enable=bool(getattr(cfg.multi_asset, 'enabled', False)))

    signals_path = Path(signals_db_path) if signals_db_path is not None else runtime_paths.signals_db_path
    ds_path = Path(dataset_path) if dataset_path is not None else data_paths.dataset_path
    if not signals_path.is_absolute():
        signals_path = root / signals_path
    if not ds_path.is_absolute():
        ds_path = root / ds_path

    runs_dir = root / 'runs'
    tz = ZoneInfo(str(getattr(chosen, 'timezone', 'UTC')))
    from ..autos.summary_loader import collect_checked_summaries

    scan_result = collect_checked_summaries(
        now=datetime.now(tz),
        lookback_days=int(max(1, lookback_days)),
        asset=str(chosen.asset),
        interval_sec=int(chosen.interval_sec),
        runs_dir=runs_dir,
        expected_timezone=str(getattr(chosen, 'timezone', 'UTC')),
    )
    summaries = scan_result.summaries

    int_cfg = getattr(cfg, 'intelligence', None)
    slot_profile = build_slot_profile(
        summaries,
        min_trades=int(getattr(int_cfg, 'slot_aware_min_trades', 6)),
        prior_weight=float(getattr(int_cfg, 'slot_aware_prior_weight', 8.0)),
        multiplier_min=float(getattr(int_cfg, 'slot_aware_multiplier_min', 0.85)),
        multiplier_max=float(getattr(int_cfg, 'slot_aware_multiplier_max', 1.15)),
        score_delta_cap=float(getattr(int_cfg, 'slot_aware_score_delta_cap', 0.05)),
        threshold_delta_cap=float(getattr(int_cfg, 'slot_aware_threshold_delta_cap', 0.03)),
    )
    coverage_profile = build_coverage_profile(
        summaries,
        target_trades_per_day=getattr(int_cfg, 'coverage_target_trades_per_day', None) or getattr(cfg.quota, 'target_trades_per_day', None),
        curve_power=float(getattr(int_cfg, 'coverage_curve_power', 1.20)),
    )

    scope = PortfolioScope(asset=str(chosen.asset), interval_sec=int(chosen.interval_sec), timezone=str(getattr(chosen, 'timezone', 'UTC')), scope_tag=scope_tag)

    training_rows = build_training_rows(
        signals_db_path=signals_path,
        dataset_path=ds_path,
        asset=str(chosen.asset),
        interval_sec=int(chosen.interval_sec),
        timezone_name=str(getattr(chosen, 'timezone', 'UTC')),
        slot_profile=slot_profile,
        limit=None,
    )
    learned_gate = None
    if bool(getattr(int_cfg, 'learned_gating_enable', True)):
        learned_gate = fit_learned_gate(training_rows, min_rows=int(getattr(int_cfg, 'learned_gating_min_rows', 50)))
    scope_policy = resolve_scope_policy(int_cfg, scope)

    drift_rows = [
        {
            'score': row.get('base_score'),
            'conf': row.get('base_conf'),
            'ev': row.get('base_ev'),
        }
        for row in training_rows
    ]
    drift_baseline = build_drift_baseline(drift_rows)

    summary_path = Path(multiwindow_summary_path) if multiwindow_summary_path is not None else _find_default_multiwindow_summary(root, cfg)
    anti_payload = load_summary_json(summary_path) if summary_path is not None else None
    anti_overfit = build_anti_overfit_report(
        anti_payload,
        min_robustness=float(getattr(int_cfg, 'anti_overfit_min_robustness', 0.50)),
        min_windows=int(getattr(int_cfg, 'anti_overfit_min_windows', 3)),
        gap_penalty_weight=float(getattr(int_cfg, 'anti_overfit_gap_penalty_weight', 0.10)),
    )

    pack = {
        'kind': 'intelligence_pack',
        'schema_version': 'phase1-intelligence-pack-v3',
        'generated_at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'asset': str(chosen.asset),
        'interval_sec': int(chosen.interval_sec),
        'timezone': str(getattr(chosen, 'timezone', 'UTC')),
        'scope_tag': scope_tag,
        'lookback_days': int(max(1, lookback_days)),
        'summary_scan': scan_result.scan,
        'slot_profile': slot_profile,
        'coverage_profile': coverage_profile,
        'learned_gate': learned_gate,
        'scope_policy': scope_policy,
        'drift_baseline': drift_baseline,
        'anti_overfit': anti_overfit,
        'metadata': {
            'repo_root': str(root),
            'config_path': str(cfg_path),
            'signals_db_path': str(signals_path),
            'dataset_path': str(ds_path),
            'multiwindow_summary_path': str(summary_path) if summary_path is not None else None,
            'training_rows': int(len(training_rows)),
            'components': {
                'slot_aware_enable': bool(getattr(int_cfg, 'slot_aware_enable', True)),
                'learned_gating_enable': bool(getattr(int_cfg, 'learned_gating_enable', True)),
                'drift_monitor_enable': bool(getattr(int_cfg, 'drift_monitor_enable', True)),
                'coverage_regulator_enable': bool(getattr(int_cfg, 'coverage_regulator_enable', True)),
                'anti_overfit_enable': bool(getattr(int_cfg, 'anti_overfit_enable', True)),
                'learned_stacking_enable': bool(getattr(int_cfg, 'learned_stacking_enable', True)),
            },
            'phase1': {
                'slot_aware_score_delta_cap': float(getattr(int_cfg, 'slot_aware_score_delta_cap', 0.05)),
                'slot_aware_threshold_delta_cap': float(getattr(int_cfg, 'slot_aware_threshold_delta_cap', 0.03)),
                'learned_promote_above': float(getattr(int_cfg, 'learned_promote_above', 0.62)),
                'learned_suppress_below': float(getattr(int_cfg, 'learned_suppress_below', 0.42)),
                'learned_abstain_band': float(getattr(int_cfg, 'learned_abstain_band', 0.03)),
                'learned_min_reliability': float(getattr(int_cfg, 'learned_min_reliability', 0.50)),
                'stack_max_bonus': float(getattr(int_cfg, 'stack_max_bonus', 0.05)),
                'stack_max_penalty': float(getattr(int_cfg, 'stack_max_penalty', 0.05)),
                'coverage_curve_power': float(getattr(int_cfg, 'coverage_curve_power', 1.20)),
                'anti_overfit_min_windows': int(getattr(int_cfg, 'anti_overfit_min_windows', 3)),
                'anti_overfit_gap_penalty_weight': float(getattr(int_cfg, 'anti_overfit_gap_penalty_weight', 0.10)),
                'retrain_cooldown_hours': int(getattr(int_cfg, 'retrain_cooldown_hours', 12)),
            },
        },
    }

    out = Path(out_path) if out_path is not None else pack_path(repo_root=root, scope_tag=scope_tag, artifact_dir=getattr(int_cfg, 'artifact_dir', 'runs/intelligence'))
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding='utf-8')
    return pack, out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Build the M5 intelligence pack for a scope.')
    p.add_argument('--repo-root', default='.')
    p.add_argument('--config', dest='config_path', default=None)
    p.add_argument('--asset', default=None)
    p.add_argument('--interval-sec', type=int, default=None)
    p.add_argument('--lookback-days', type=int, default=14)
    p.add_argument('--signals-db', dest='signals_db_path', default=None)
    p.add_argument('--dataset-path', default=None)
    p.add_argument('--multiwindow-summary', dest='multiwindow_summary_path', default=None)
    p.add_argument('--out', dest='out_path', default=None)
    p.add_argument('--json', action='store_true')
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    pack, out = fit_intelligence_pack(
        repo_root=ns.repo_root,
        config_path=ns.config_path,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        lookback_days=ns.lookback_days,
        signals_db_path=ns.signals_db_path,
        dataset_path=ns.dataset_path,
        multiwindow_summary_path=ns.multiwindow_summary_path,
        out_path=ns.out_path,
    )
    payload = {
        'ok': True,
        'out_path': str(out),
        'scope_tag': pack.get('scope_tag'),
        'asset': pack.get('asset'),
        'interval_sec': pack.get('interval_sec'),
        'training_rows': ((pack.get('metadata') or {}).get('training_rows')),
        'learned_gate_available': bool(pack.get('learned_gate')),
        'anti_overfit': pack.get('anti_overfit'),
    }
    if ns.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f'intelligence_pack_ok: {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
