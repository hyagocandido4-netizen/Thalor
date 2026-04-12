from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from natbin.ops.practice_preflight import build_practice_preflight_payload
from natbin.ops.safe_refresh import maybe_heal_market_context


class _DummyConfig(SimpleNamespace):
    config_path: str
    asset: str
    interval_sec: int


class _DummyScope(SimpleNamespace):
    scope_tag: str


class _DummyCtx(SimpleNamespace):
    repo_root: str
    config: _DummyConfig
    scope: _DummyScope
    resolved_config: dict


def test_maybe_heal_market_context_dry_run_reports_planned(tmp_path: Path) -> None:
    runs = tmp_path / 'runs'
    runs.mkdir(parents=True, exist_ok=True)
    payload = maybe_heal_market_context(
        repo_root=tmp_path,
        config_path=tmp_path / 'config.yaml',
        asset='EURUSD-OTC',
        interval_sec=300,
        max_age_sec=900,
        enabled=True,
        dry_run=True,
    )
    assert payload['status'] == 'planned'
    assert payload['safe'] is True
    assert payload['potentially_submits'] is False
    assert payload['attempted'] is False


def test_practice_preflight_runs_safe_market_context_repair_first(monkeypatch, tmp_path: Path) -> None:
    ctx = _DummyCtx(
        repo_root=str(tmp_path),
        config=_DummyConfig(config_path=str(tmp_path / 'config.yaml'), asset='EURUSD-OTC', interval_sec=300),
        scope=_DummyScope(scope_tag='EURUSD-OTC_300s'),
        resolved_config={},
    )
    heal_calls: list[dict] = []

    monkeypatch.setattr('natbin.ops.practice_preflight.build_context', lambda **_: ctx)
    monkeypatch.setattr('natbin.ops.practice_preflight.collect_sensitive_values', lambda _cfg: [])
    monkeypatch.setattr('natbin.ops.practice_preflight.sanitize_payload', lambda payload, sensitive_values=None: payload)
    monkeypatch.setattr('natbin.ops.practice_preflight.write_scope_artifact', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.maybe_heal_market_context',
        lambda **kwargs: heal_calls.append(kwargs) or {
            'name': 'market_context',
            'enabled': True,
            'attempted': True,
            'status': 'ok',
            'message': 'market_context_refreshed',
            'safe': True,
            'potentially_submits': False,
        },
    )
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.build_diag_suite_payload',
        lambda **kwargs: {
            'ok': True,
            'severity': 'ok',
            'actions': [],
            'results': {
                'practice': {
                    'ready_for_practice': True,
                    'checks': [
                        {'name': 'production_doctor', 'status': 'ok', 'message': 'ok'},
                        {'name': 'runtime_soak', 'status': 'ok', 'message': 'ok'},
                    ],
                }
            },
            'ready_for_practice': True,
        },
    )
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.build_transport_smoke_payload',
        lambda **kwargs: {'ok': True, 'severity': 'ok', 'actions': [], 'scope_results': [{'actions': []}]},
    )
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.build_module_smoke_payload',
        lambda **kwargs: {'ok': True, 'severity': 'ok', 'actions': []},
    )

    payload = build_practice_preflight_payload(repo_root=tmp_path, config_path=tmp_path / 'config.yaml', dry_run=False)
    assert heal_calls, 'safe market_context repair should be attempted by default'
    assert payload['ready_for_long_practice'] is True
    repair_names = [item.get('name') for item in payload['repairs']]
    assert 'market_context' in repair_names
    market_repair = next(item for item in payload['repairs'] if item.get('name') == 'market_context')
    assert market_repair['status'] == 'ok'


def test_practice_preflight_can_refresh_soak_when_only_soak_is_pending(monkeypatch, tmp_path: Path) -> None:
    ctx = _DummyCtx(
        repo_root=str(tmp_path),
        config=_DummyConfig(config_path=str(tmp_path / 'config.yaml'), asset='EURUSD-OTC', interval_sec=300),
        scope=_DummyScope(scope_tag='EURUSD-OTC_300s'),
        resolved_config={},
    )
    diag_calls: list[dict] = []
    bootstrap_calls: list[dict] = []

    first_practice = {
        'ready_for_practice': False,
        'checks': [
            {'name': 'production_doctor', 'status': 'ok', 'message': 'ok'},
            {'name': 'runtime_soak', 'status': 'warn', 'message': 'stale'},
        ],
    }
    second_practice = {
        'ready_for_practice': True,
        'checks': [
            {'name': 'production_doctor', 'status': 'ok', 'message': 'ok'},
            {'name': 'runtime_soak', 'status': 'ok', 'message': 'fresh'},
        ],
    }

    def _diag_suite(**kwargs):
        diag_calls.append(kwargs)
        practice = first_practice if len(diag_calls) == 1 else second_practice
        return {
            'ok': True,
            'severity': 'ok',
            'actions': [],
            'results': {'practice': practice},
            'ready_for_practice': bool(practice['ready_for_practice']),
        }

    monkeypatch.setattr('natbin.ops.practice_preflight.build_context', lambda **_: ctx)
    monkeypatch.setattr('natbin.ops.practice_preflight.collect_sensitive_values', lambda _cfg: [])
    monkeypatch.setattr('natbin.ops.practice_preflight.sanitize_payload', lambda payload, sensitive_values=None: payload)
    monkeypatch.setattr('natbin.ops.practice_preflight.write_scope_artifact', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.maybe_heal_market_context',
        lambda **kwargs: {
            'name': 'market_context',
            'enabled': True,
            'attempted': False,
            'status': 'skip',
            'message': 'market_context_fresh',
            'safe': True,
            'potentially_submits': False,
        },
    )
    monkeypatch.setattr('natbin.ops.practice_preflight.build_diag_suite_payload', _diag_suite)
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.build_transport_smoke_payload',
        lambda **kwargs: {'ok': True, 'severity': 'ok', 'actions': [], 'scope_results': [{'actions': []}]},
    )
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.build_module_smoke_payload',
        lambda **kwargs: {'ok': True, 'severity': 'ok', 'actions': []},
    )
    monkeypatch.setattr(
        'natbin.ops.practice_bootstrap.build_practice_bootstrap_payload',
        lambda **kwargs: bootstrap_calls.append(kwargs) or {'ok': True, 'kind': 'practice_bootstrap'},
    )

    payload = build_practice_preflight_payload(
        repo_root=tmp_path,
        config_path=tmp_path / 'config.yaml',
        heal_soak=True,
        soak_cycles=6,
        dry_run=False,
    )

    assert len(diag_calls) == 2, 'diag suite should rerun after soak repair'
    assert bootstrap_calls, 'practice bootstrap should run when only runtime_soak is pending'
    assert payload['ready_for_long_practice'] is True
    soak_repairs = [item for item in payload['repairs'] if item.get('name') == 'runtime_soak']
    assert soak_repairs and soak_repairs[0]['attempted'] is True
    assert soak_repairs[0]['potentially_submits'] is True
