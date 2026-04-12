from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from natbin.portfolio.models import CandidateDecision, PortfolioScope
from natbin.portfolio.paths import ScopeDataPaths, ScopeRuntimePaths
from natbin.portfolio.runtime_budget import decide_prepare_strategy
from natbin.portfolio import runner
from natbin.ops import provider_session_governor as governor_mod


class _DummyOutcome:
    def __init__(self, name: str, returncode: int = 0) -> None:
        self.name = name
        self.returncode = int(returncode)

    def as_dict(self) -> dict[str, object]:
        return {'name': self.name, 'returncode': self.returncode, 'duration_sec': 0.0, 'stdout_tail': '', 'stderr_tail': ''}


class _DummyBreakerRepo:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def load_breaker(self, *_args, **_kwargs):
        return SimpleNamespace(state='closed')

    def save_breaker(self, *_args, **_kwargs) -> None:
        return None


class _DummyFailsafe:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def is_kill_switch_active(self, *_args, **_kwargs):
        return False, None

    def is_drain_mode_active(self, *_args, **_kwargs):
        return False, None

    def record_success(self, snap):
        return snap

    def record_failure(self, snap, **_kwargs):
        return snap


class _DummyPrecheckDecision:
    blocked = False
    reason = None


class _DummyAllocation:
    def as_dict(self) -> dict[str, object]:
        return {'allocation_id': 'alloc', 'selected': [], 'suppressed': [], 'max_select': 1}


class _DummyPortfolioRepo:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def save_cycle(self, *_args, **_kwargs) -> None:
        return None


def _scope(asset: str) -> PortfolioScope:
    return PortfolioScope(asset=asset, interval_sec=300, timezone='UTC', scope_tag=f'{asset}_300s')


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        multi_asset=SimpleNamespace(
            enabled=True,
            max_parallel_assets=1,
            partition_data_paths=True,
            stagger_sec=0.0,
            execution_stagger_sec=0.0,
            candidate_budget_rotation_enable=True,
            adaptive_prepare_enable=True,
            prepare_incremental_lookback_candles=256,
        ),
        execution=SimpleNamespace(enabled=False),
        failsafe=SimpleNamespace(
            kill_switch_file=Path('runs/KILL_SWITCH'),
            drain_mode_file=Path('runs/DRAIN_MODE'),
            kill_switch_env_var='THALOR_KILL_SWITCH',
            drain_mode_env_var='THALOR_DRAIN_MODE',
            breaker_failures_to_open=3,
            breaker_cooldown_minutes=15,
            breaker_half_open_trials=1,
            global_fail_closed=True,
            market_context_fail_closed=True,
        ),
        runtime=SimpleNamespace(profile='practice_portfolio_canary'),
    )


def test_decide_prepare_strategy_covers_fresh_refresh_and_incremental() -> None:
    assert decide_prepare_strategy(
        adaptive_prepare_enable=True,
        db_exists=True,
        db_rows=200,
        db_fresh=True,
        market_context_exists=True,
        market_context_fresh=True,
        market_context_dependency_available=True,
        full_lookback_candles=2000,
        incremental_lookback_candles=256,
    )['strategy'] == 'skip_fresh'

    assert decide_prepare_strategy(
        adaptive_prepare_enable=True,
        db_exists=True,
        db_rows=200,
        db_fresh=True,
        market_context_exists=True,
        market_context_fresh=False,
        market_context_dependency_available=False,
        full_lookback_candles=2000,
        incremental_lookback_candles=256,
    )['strategy'] == 'refresh_only'

    decision = decide_prepare_strategy(
        adaptive_prepare_enable=True,
        db_exists=True,
        db_rows=200,
        db_fresh=False,
        market_context_exists=True,
        market_context_fresh=False,
        market_context_dependency_available=False,
        full_lookback_candles=2000,
        incremental_lookback_candles=256,
    )
    assert decision['strategy'] == 'incremental_prepare'
    assert decision['effective_lookback_candles'] == 256


def test_prepare_scope_runtime_uses_incremental_lookback_when_db_present(tmp_path: Path, monkeypatch) -> None:
    scope = _scope('EURUSD-OTC')
    data_paths = ScopeDataPaths(db_path=tmp_path / 'market.sqlite3', dataset_path=tmp_path / 'dataset.csv')
    cfg = _cfg()
    captured: dict[str, int] = {}

    monkeypatch.setattr(runner, '_market_context_state', lambda *_args, **_kwargs: {'exists': False, 'fresh': False, 'dependency_available': False})
    monkeypatch.setattr(runner, '_candle_db_state', lambda *_args, **_kwargs: {'exists': True, 'db_rows': 120, 'fresh': False})

    def fake_prepare_scope(**kwargs):
        captured['lookback'] = int(kwargs['lookback_candles'])
        return [_DummyOutcome('collect_recent:EURUSD-OTC_300s')]

    monkeypatch.setattr(runner, 'prepare_scope', fake_prepare_scope)

    payload = runner._prepare_scope_runtime(
        repo_root=tmp_path,
        config_path=tmp_path / 'config.yaml',
        cfg=cfg,
        scope=scope,
        data_paths=data_paths,
        lookback_candles=2000,
        stagger_delay_sec=0.0,
        refresh_timeout_sec=30,
        allow_prepare_fallback=True,
    )

    assert payload['strategy'] == 'incremental_prepare'
    assert payload['uses_incremental_lookback'] is True
    assert captured['lookback'] == 256


def test_governor_degraded_caps_candidate_and_prepare_budget() -> None:
    governor = governor_mod._derive_governor(
        scope_count=6,
        provider_ready_scopes=6,
        stability_state='degraded',
        transient_noise=['websocket_lifecycle'],
        hard_blockers=[],
    )
    assert governor['max_candidate_scopes_per_run'] == 3
    assert governor['max_asset_prepare_fallback_scopes'] == 3


def test_run_portfolio_cycle_applies_candidate_budget_rotation(tmp_path: Path, monkeypatch) -> None:
    scopes = [_scope('EURUSD-OTC'), _scope('GBPUSD-OTC'), _scope('AUDUSD-OTC'), _scope('USDJPY-OTC')]
    cfg = _cfg()
    captured = {'candidate_scope_tags': [], 'payloads': []}

    monkeypatch.setattr(runner, 'load_scopes', lambda **_kwargs: (scopes, cfg))
    monkeypatch.setattr(runner, 'RuntimeControlRepository', _DummyBreakerRepo)
    monkeypatch.setattr(runner, 'RuntimeFailsafe', _DummyFailsafe)
    monkeypatch.setattr(runner, 'run_precheck', lambda *args, **kwargs: _DummyPrecheckDecision())
    monkeypatch.setattr(runner, 'PortfolioRepository', _DummyPortfolioRepo)
    monkeypatch.setattr(runner, '_allocator', SimpleNamespace(allocate=lambda *args, **kwargs: _DummyAllocation()))
    monkeypatch.setattr(runner, 'compute_asset_quotas', lambda *args, **kwargs: [])
    monkeypatch.setattr(
        runner,
        'compute_portfolio_quota',
        lambda *args, **kwargs: SimpleNamespace(
            kind='open',
            reason='ok',
            hard_max_positions_total=1,
            open_positions_total=0,
            budget_left_total=1,
            budget_left_pending_unknown_total=1,
            correlation_filter_enable=True,
        ),
    )
    monkeypatch.setattr(runner, '_scope_data_paths', lambda root, cfg, scope: ScopeDataPaths(db_path=tmp_path / f'{scope.scope_tag}.sqlite3', dataset_path=tmp_path / f'{scope.scope_tag}.csv'))
    monkeypatch.setattr(runner, '_scope_runtime_paths', lambda root, cfg, scope: ScopeRuntimePaths(signals_db_path=tmp_path / f'{scope.scope_tag}.signals.sqlite3', state_db_path=tmp_path / f'{scope.scope_tag}.state.sqlite3'))
    monkeypatch.setattr(
        runner,
        '_load_runtime_governor',
        lambda **_kwargs: {'artifact_present': False, 'artifact_fresh': False, 'governor': {'mode': 'serial_guarded', 'max_candidate_scopes_per_run': 2, 'scope_order': 'best_first_round_robin', 'sleep_between_scopes_ms': 0, 'sleep_between_candidate_scopes_ms': 0, 'refresh_market_context_timeout_sec': 30, 'max_asset_prepare_fallback_scopes': 2}},
    )
    monkeypatch.setattr(runner, '_prepare_scope_runtime', lambda **kwargs: {'scope_tag': kwargs['scope'].scope_tag, 'asset': kwargs['scope'].asset, 'interval_sec': kwargs['scope'].interval_sec, 'strategy': 'skip_fresh', 'fallback_used': False, 'steps': []})
    monkeypatch.setattr(runner, '_ordered_scopes_for_candidate_budget', lambda root, ordered_scopes: ordered_scopes)

    def fake_candidate_scope(**kwargs):
        scope = kwargs['scope']
        captured['candidate_scope_tags'].append(scope.scope_tag)
        return (
            _DummyOutcome(f'observe_once:{scope.scope_tag}'),
            CandidateDecision(
                scope_tag=scope.scope_tag,
                asset=scope.asset,
                interval_sec=scope.interval_sec,
                day='2026-04-08',
                ts=1775609700,
                action='HOLD',
                score=0.0,
                conf=0.5,
                ev=-1.0,
                reason='regime_block',
                blockers='below_ev_threshold',
                decision_path=str(tmp_path / f'{scope.scope_tag}.json'),
                raw={'kind': 'candidate'},
            ),
        )

    monkeypatch.setattr(runner, 'candidate_scope', fake_candidate_scope)

    def fake_write_portfolio_latest_payload(*args, **kwargs):
        captured['payloads'].append(kwargs['payload'])
        return {}

    monkeypatch.setattr(runner, 'write_portfolio_latest_payload', fake_write_portfolio_latest_payload)

    report = runner.run_portfolio_cycle(
        repo_root=tmp_path,
        config_path=tmp_path / 'config.yaml',
        topk=1,
        lookback_candles=2000,
    )

    assert len(captured['candidate_scope_tags']) == 2
    skipped = [item for item in report.candidate_results if bool(item.get('budget_skipped'))]
    assert len(skipped) == 2
    cycle_payload = next(payload for payload in captured['payloads'] if payload.get('candidate_budget'))
    assert cycle_payload['candidate_budget']['scanned_scope_count'] == 2
    assert cycle_payload['candidate_budget']['skipped_scope_count'] == 2
