from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np

from ..config.loader import load_thalor_config
from ..runtime.broker_surface import execution_repo_path

_TERMINAL_SETTLEMENTS = {'win', 'loss', 'refund', 'cancelled'}
_CLOSED_BROKER_STATUSES = {'closed', 'settled', 'cancelled'}


@dataclass(slots=True)
class TradeSample:
    trade_at_utc: datetime
    day_local: date
    asset: str
    account_mode: str
    amount: float
    net_pnl: float
    net_return: float
    settlement_status: str


@dataclass(slots=True)
class ScenarioSpec:
    name: str
    label: str
    trade_count_scale: float
    return_scale: float
    stake_scale: float


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _parse_utc(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _sqlite_connect_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    uri = f'file:{db_path.as_posix()}?mode=ro'
    con = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=1.0)
    con.row_factory = sqlite3.Row
    return con


def _iter_realized_rows(repo_root: Path) -> Iterable[dict[str, Any]]:
    db_path = execution_repo_path(repo_root)
    con = _sqlite_connect_ro(db_path)
    if con is None:
        return []
    try:
        rows = con.execute(
            '''
            SELECT broker_name, account_mode, asset, amount, currency, broker_status,
                   settlement_status, opened_at_utc, closed_at_utc, last_seen_at_utc,
                   gross_payout, net_pnl, estimated_pnl
            FROM broker_orders
            ORDER BY COALESCE(closed_at_utc, last_seen_at_utc, opened_at_utc) ASC
            '''
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def _normalize_trade_sample(row: dict[str, Any], tz: ZoneInfo) -> TradeSample | None:
    settlement_status = str(row.get('settlement_status') or '').strip().lower() or None
    broker_status = str(row.get('broker_status') or '').strip().lower() or None
    if settlement_status in _TERMINAL_SETTLEMENTS:
        resolved = settlement_status
    elif settlement_status is None and broker_status in _CLOSED_BROKER_STATUSES:
        net_pnl_value = row.get('net_pnl')
        pnl = _safe_float(net_pnl_value) if net_pnl_value not in (None, '') else 0.0
        if broker_status == 'cancelled':
            resolved = 'cancelled'
        elif pnl > 0:
            resolved = 'win'
        elif pnl < 0:
            resolved = 'loss'
        else:
            resolved = 'refund'
    else:
        return None

    amount = _safe_float(row.get('amount') or 0.0)
    if amount <= 0.0:
        return None
    net_pnl = _safe_float(row.get('net_pnl') or 0.0)
    trade_dt = (
        _parse_utc(row.get('closed_at_utc'))
        or _parse_utc(row.get('last_seen_at_utc'))
        or _parse_utc(row.get('opened_at_utc'))
    )
    if trade_dt is None:
        return None
    return TradeSample(
        trade_at_utc=trade_dt,
        day_local=trade_dt.astimezone(tz).date(),
        asset=str(row.get('asset') or ''),
        account_mode=str(row.get('account_mode') or '').upper() or 'UNKNOWN',
        amount=amount,
        net_pnl=net_pnl,
        net_return=net_pnl / amount if amount > 0 else 0.0,
        settlement_status=resolved,
    )


def load_trade_samples(*, repo_root: str | Path = '.', timezone: str = 'UTC') -> list[TradeSample]:
    root = Path(repo_root).resolve()
    tz = ZoneInfo(str(timezone or 'UTC'))
    rows = _iter_realized_rows(root)
    samples: list[TradeSample] = []
    for row in rows:
        sample = _normalize_trade_sample(row, tz)
        if sample is not None:
            samples.append(sample)
    samples.sort(key=lambda item: item.trade_at_utc)
    return samples


def _daily_count_samples(samples: list[TradeSample]) -> list[int]:
    if not samples:
        return []
    counts: dict[date, int] = Counter(sample.day_local for sample in samples)
    start_day = min(counts)
    end_day = max(counts)
    values: list[int] = []
    cur = start_day
    while cur <= end_day:
        values.append(int(counts.get(cur, 0)))
        cur += timedelta(days=1)
    return values


def _quantile_payload(values: Iterable[float], *, digits: int = 2) -> dict[str, float]:
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return {'p05': 0.0, 'p25': 0.0, 'p50': 0.0, 'p75': 0.0, 'p95': 0.0, 'mean': 0.0}
    return {
        'p05': round(float(np.percentile(arr, 5)), digits),
        'p25': round(float(np.percentile(arr, 25)), digits),
        'p50': round(float(np.percentile(arr, 50)), digits),
        'p75': round(float(np.percentile(arr, 75)), digits),
        'p95': round(float(np.percentile(arr, 95)), digits),
        'mean': round(float(np.mean(arr)), digits),
    }


def _fan_points(paths: np.ndarray, *, digits: int = 2) -> list[dict[str, float]]:
    # paths shape = (trials, horizon_days + 1)
    out: list[dict[str, float]] = []
    if paths.size == 0:
        return out
    for day in range(paths.shape[1]):
        col = paths[:, day]
        out.append(
            {
                'day': int(day),
                'p05': round(float(np.percentile(col, 5)), digits),
                'p10': round(float(np.percentile(col, 10)), digits),
                'p25': round(float(np.percentile(col, 25)), digits),
                'p50': round(float(np.percentile(col, 50)), digits),
                'p75': round(float(np.percentile(col, 75)), digits),
                'p90': round(float(np.percentile(col, 90)), digits),
                'p95': round(float(np.percentile(col, 95)), digits),
            }
        )
    return out


def _histogram(values: Iterable[float], *, bins: int = 12, digits: int = 2) -> list[dict[str, float | int]]:
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return []
    counts, edges = np.histogram(arr, bins=int(max(4, bins)))
    rows: list[dict[str, float | int]] = []
    for idx, count in enumerate(counts):
        rows.append(
            {
                'bucket_idx': int(idx),
                'left': round(float(edges[idx]), digits),
                'right': round(float(edges[idx + 1]), digits),
                'count': int(count),
            }
        )
    return rows


def _scenario_specs(cfg: dict[str, Any]) -> list[ScenarioSpec]:
    def _one(name: str, default_label: str, default_trade_count: float, default_return: float, default_stake: float) -> ScenarioSpec:
        raw = dict(cfg.get(name) or {})
        return ScenarioSpec(
            name=name,
            label=str(raw.get('label') or default_label),
            trade_count_scale=max(0.0, float(raw.get('trade_count_scale') or default_trade_count)),
            return_scale=max(0.0, float(raw.get('return_scale') or default_return)),
            stake_scale=max(0.0, float(raw.get('stake_scale') or default_stake)),
        )

    return [
        _one('conservative', 'Conservador', 0.85, 0.90, 0.90),
        _one('medium', 'Médio', 1.00, 1.00, 1.00),
        _one('aggressive', 'Agressivo', 1.15, 1.10, 1.10),
    ]


def _simulate_scenario(
    *,
    spec: ScenarioSpec,
    initial_capital: float,
    horizon_days: int,
    trials: int,
    amounts: np.ndarray,
    returns: np.ndarray,
    daily_counts: np.ndarray,
    max_stake_fraction_cap: float,
    rng: np.random.Generator,
) -> dict[str, Any]:
    paths = np.zeros((int(trials), int(horizon_days) + 1), dtype=float)
    ending_equity = np.zeros(int(trials), dtype=float)
    max_drawdown = np.zeros(int(trials), dtype=float)
    total_trades = np.zeros(int(trials), dtype=float)

    count_upper = int(max(1, np.max(daily_counts))) if daily_counts.size else 1

    for trial_idx in range(int(trials)):
        equity = float(initial_capital)
        peak = float(initial_capital)
        worst_dd = 0.0
        trade_counter = 0
        paths[trial_idx, 0] = equity
        for day_idx in range(1, int(horizon_days) + 1):
            if daily_counts.size:
                base_count = float(daily_counts[rng.integers(0, daily_counts.size)])
            else:
                base_count = 1.0
            count = int(round(base_count * float(spec.trade_count_scale)))
            count = max(0, min(count, max(1, count_upper * 2)))
            for _ in range(count):
                if equity <= 0.0:
                    break
                stake_amount = float(amounts[rng.integers(0, amounts.size)]) * float(spec.stake_scale)
                stake_amount = max(0.5, stake_amount)
                if max_stake_fraction_cap > 0.0:
                    stake_amount = min(stake_amount, max(0.5, equity * max_stake_fraction_cap))
                sampled_return = float(returns[rng.integers(0, returns.size)]) * float(spec.return_scale)
                equity = max(0.0, equity + stake_amount * sampled_return)
                trade_counter += 1
            peak = max(peak, equity)
            if peak > 0:
                worst_dd = max(worst_dd, (peak - equity) / peak)
            paths[trial_idx, day_idx] = equity
        ending_equity[trial_idx] = equity
        max_drawdown[trial_idx] = worst_dd
        total_trades[trial_idx] = float(trade_counter)

    ending_pnl = ending_equity - float(initial_capital)
    return {
        'name': spec.name,
        'label': spec.label,
        'trade_count_scale': round(float(spec.trade_count_scale), 4),
        'return_scale': round(float(spec.return_scale), 4),
        'stake_scale': round(float(spec.stake_scale), 4),
        'profit_probability': round(float(np.mean(ending_equity > float(initial_capital))), 4),
        'loss_probability': round(float(np.mean(ending_equity < float(initial_capital))), 4),
        'ruin_probability': round(float(np.mean(ending_equity <= float(initial_capital) * 0.5)), 4),
        'ending_equity_brl': _quantile_payload(ending_equity),
        'ending_pnl_brl': _quantile_payload(ending_pnl),
        'max_drawdown_pct': _quantile_payload(max_drawdown, digits=4),
        'total_trades': _quantile_payload(total_trades, digits=1),
        'fan_points': _fan_points(paths),
        'ending_histogram': _histogram(ending_equity),
    }


def _history_summary(samples: list[TradeSample], daily_counts: list[int]) -> dict[str, Any]:
    if not samples:
        return {
            'realized_trades': 0,
            'assets': [],
            'account_modes': {},
            'daily_counts': {'mean': 0.0, 'p50': 0.0, 'p95': 0.0},
        }
    assets = sorted({item.asset for item in samples if item.asset})
    account_modes = Counter(item.account_mode for item in samples)
    net_returns = [item.net_return for item in samples]
    amounts = [item.amount for item in samples]
    first_trade = samples[0].trade_at_utc.isoformat(timespec='seconds')
    last_trade = samples[-1].trade_at_utc.isoformat(timespec='seconds')
    counts_arr = np.asarray(list(daily_counts or [0]), dtype=float)
    return {
        'realized_trades': int(len(samples)),
        'sample_days': int(len(daily_counts)),
        'assets': assets,
        'account_modes': {str(key): int(value) for key, value in sorted(account_modes.items())},
        'first_trade_at_utc': first_trade,
        'last_trade_at_utc': last_trade,
        'stake_amount_brl': _quantile_payload(amounts),
        'trade_return': _quantile_payload(net_returns, digits=4),
        'daily_counts': {
            'mean': round(float(np.mean(counts_arr)), 2),
            'p50': round(float(np.percentile(counts_arr, 50)), 2),
            'p95': round(float(np.percentile(counts_arr, 95)), 2),
        },
    }


def build_monte_carlo_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    initial_capital_brl: float | None = None,
    horizon_days: int | None = None,
    trials: int | None = None,
    rng_seed: int | None = None,
    write_report: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    cfg = load_thalor_config(config_path=config_path, repo_root=root)
    mc_cfg = cfg.monte_carlo.model_dump(mode='python')
    timezone = str(cfg.assets[0].timezone if list(cfg.assets or []) else 'UTC')
    initial_capital = float(initial_capital_brl if initial_capital_brl is not None else mc_cfg.get('initial_capital_brl') or 1000.0)
    horizon = int(horizon_days if horizon_days is not None else mc_cfg.get('horizon_days') or 60)
    num_trials = int(trials if trials is not None else mc_cfg.get('trials') or 1000)
    seed = int(rng_seed if rng_seed is not None else mc_cfg.get('rng_seed') or 42)
    min_trades = int(mc_cfg.get('min_realized_trades') or 20)
    max_stake_fraction_cap = float(mc_cfg.get('max_stake_fraction_cap') or 0.10)

    samples = load_trade_samples(repo_root=root, timezone=timezone)
    daily_counts = _daily_count_samples(samples)
    history = _history_summary(samples, daily_counts)

    payload: dict[str, Any] = {
        'ok': False,
        'kind': 'monte_carlo',
        'generated_at_utc': _utc_now_iso(),
        'repo_root': str(root),
        'config_path': str(Path(config_path).resolve()) if config_path not in (None, '') else str(Path(cfg.config_path).resolve()),
        'execution_repo_path': str(execution_repo_path(root)),
        'profile': str(cfg.runtime.profile),
        'settings': {
            'initial_capital_brl': round(initial_capital, 2),
            'horizon_days': int(horizon),
            'trials': int(num_trials),
            'rng_seed': int(seed),
            'min_realized_trades': int(min_trades),
            'timezone': timezone,
        },
        'history': history,
        'scenarios': [],
        'report_paths': {},
    }

    if len(samples) < min_trades:
        payload.update(
            {
                'severity': 'error',
                'reason': 'insufficient_history',
                'message': f'Not enough realized historical trades for Monte Carlo: {len(samples)} < {min_trades}',
            }
        )
        return payload

    amounts = np.asarray([max(0.5, float(item.amount)) for item in samples], dtype=float)
    returns = np.asarray([float(item.net_return) for item in samples], dtype=float)
    daily_counts_arr = np.asarray(list(daily_counts or [0]), dtype=float)
    rng = np.random.default_rng(seed)

    scenario_specs = _scenario_specs(mc_cfg)
    scenarios: list[dict[str, Any]] = []
    for spec in scenario_specs:
        scenario = _simulate_scenario(
            spec=spec,
            initial_capital=initial_capital,
            horizon_days=horizon,
            trials=num_trials,
            amounts=amounts,
            returns=returns,
            daily_counts=daily_counts_arr,
            max_stake_fraction_cap=max_stake_fraction_cap,
            rng=rng,
        )
        scenarios.append(scenario)
    payload['scenarios'] = scenarios
    payload['ok'] = True
    payload['severity'] = 'ok'

    if write_report:
        from .report import export_monte_carlo_report

        report_cfg = dict(mc_cfg.get('report') or {})
        output_dir = Path(str(report_cfg.get('output_dir') or 'runs/reports/monte_carlo')).expanduser()
        if not output_dir.is_absolute():
            output_dir = root / output_dir
        try:
            payload['report_paths'] = export_monte_carlo_report(
                payload,
                output_dir=output_dir,
                export_html=bool(report_cfg.get('export_html', True)),
                export_pdf=bool(report_cfg.get('export_pdf', True)),
                export_json=bool(report_cfg.get('export_json', True)),
            )
        except RuntimeError as exc:
            payload.update(
                {
                    'ok': False,
                    'severity': 'error',
                    'reason': 'missing_report_dependencies',
                    'message': str(exc),
                }
            )
    return payload


__all__ = ['build_monte_carlo_payload', 'load_trade_samples']
