from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from natbin.ops.practice_preflight import build_practice_preflight_payload
from natbin.ops.safe_refresh import maybe_heal_breaker
from natbin.runtime.failsafe import CircuitBreakerSnapshot
from natbin.state.control_repo import RuntimeControlRepository


def _write_cfg(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: live_controlled_practice',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: PRACTICE',
                'broker:',
                '  provider: iqoption',
                '  balance_mode: PRACTICE',
                'failsafe:',
                '  breaker_failures_to_open: 3',
                '  breaker_cooldown_minutes: 15',
                '  breaker_half_open_trials: 1',
                'data:',
                '  db_path: data/market_otc.sqlite3',
                '  dataset_path: data/dataset_phase2.csv',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
            ]
        )
        + '\n',
        encoding='utf-8',
    )
    return cfg


def _seed_breaker(repo_root: Path, *, age_hours: float, state: str = 'half_open', trials_used: int = 1, reason: str = 'collect_recent:timeout') -> None:
    now = datetime.now(tz=UTC)
    control_repo = RuntimeControlRepository(repo_root / 'runs' / 'runtime_control.sqlite3')
    control_repo.save_breaker(
        CircuitBreakerSnapshot(
            asset='EURUSD-OTC',
            interval_sec=300,
            state=state,
            failures=3,
            last_failure_utc=now - timedelta(hours=age_hours),
            opened_until_utc=now - timedelta(hours=max(0.0, age_hours - 0.5)),
            half_open_trials_used=trials_used,
            reason=reason,
        )
    )


def test_maybe_heal_breaker_resets_stale_half_open(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path)
    _seed_breaker(tmp_path, age_hours=3.0)

    payload = maybe_heal_breaker(
        repo_root=tmp_path,
        config_path=cfg,
        asset='EURUSD-OTC',
        interval_sec=300,
        enabled=True,
        dry_run=False,
        stale_after_sec=1800,
    )

    assert payload['status'] == 'ok'
    assert payload['before']['state'] == 'half_open'
    assert payload['before']['stale'] is True
    assert payload['after']['state'] == 'closed'
    assert payload['after']['stale'] is False


def test_maybe_heal_breaker_skips_recent_half_open(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path)
    _seed_breaker(tmp_path, age_hours=0.1)

    payload = maybe_heal_breaker(
        repo_root=tmp_path,
        config_path=cfg,
        asset='EURUSD-OTC',
        interval_sec=300,
        enabled=True,
        dry_run=False,
        stale_after_sec=1800,
    )

    assert payload['status'] == 'skip'
    assert payload['message'] == 'circuit_breaker_not_stale'
    assert payload['before']['state'] == 'half_open'
    assert payload['before']['stale'] is False


def test_practice_preflight_repairs_stale_breaker_before_diag_suite(monkeypatch, tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path)
    _seed_breaker(tmp_path, age_hours=3.0)

    import natbin.ops.practice_preflight as module

    captured: dict[str, object] = {}

    monkeypatch.setattr(module, 'maybe_heal_market_context', lambda **kwargs: {'name': 'market_context', 'status': 'skip', 'enabled': True, 'attempted': False, 'message': 'fresh'})
    monkeypatch.setattr(module, 'maybe_heal_control_freshness', lambda **kwargs: {'name': 'control_freshness', 'status': 'skip', 'enabled': True, 'attempted': False, 'message': 'fresh'})

    def fake_diag_suite_payload(**kwargs):
        control_repo = RuntimeControlRepository(tmp_path / 'runs' / 'runtime_control.sqlite3')
        snap = control_repo.load_breaker('EURUSD-OTC', 300)
        captured['breaker_state_when_diag_runs'] = snap.state
        return {
            'kind': 'diag_suite',
            'ok': True,
            'severity': 'ok',
            'checks': [],
            'actions': [],
            'results': {
                'practice': {
                    'kind': 'practice_readiness',
                    'ok': True,
                    'severity': 'ok',
                    'ready_for_practice': True,
                    'checks': [
                        {'name': 'drain_mode', 'status': 'ok'},
                        {'name': 'runtime_soak', 'status': 'ok'},
                        {'name': 'production_doctor', 'status': 'ok'},
                    ],
                }
            },
        }

    monkeypatch.setattr(module, 'build_diag_suite_payload', fake_diag_suite_payload)
    monkeypatch.setattr(module, 'build_transport_smoke_payload', lambda **kwargs: {'kind': 'transport_smoke', 'ok': True, 'severity': 'ok', 'actions': [], 'scope_results': [{'actions': []}]})
    monkeypatch.setattr(module, 'build_module_smoke_payload', lambda **kwargs: {'kind': 'module_smoke', 'ok': True, 'severity': 'ok', 'actions': []})

    payload = build_practice_preflight_payload(repo_root=tmp_path, config_path=cfg, dry_run=False)

    assert captured['breaker_state_when_diag_runs'] == 'closed'
    breaker_repairs = [item for item in payload['repairs'] if item.get('name') == 'circuit_breaker']
    assert breaker_repairs and breaker_repairs[0]['status'] == 'ok'
    assert payload['ok'] is True
    assert payload['ready_for_long_practice'] is True
