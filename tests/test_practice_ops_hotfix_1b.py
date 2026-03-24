from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from natbin.adapters.iq_client import IQClient, IQConfig
from natbin.runtime.reconciliation import reconcile_scope
from natbin.runtime.soak import build_runtime_soak_summary


class _NoBrokerScanAdapter:
    def fetch_open_orders(self, scope):  # pragma: no cover - should never run
        raise AssertionError('fetch_open_orders should not be called when there are no pending intents')

    def fetch_closed_orders(self, scope, since_utc):  # pragma: no cover - should never run
        raise AssertionError('fetch_closed_orders should not be called when there are no pending intents')


class _HangingHistoryBackend:
    def __init__(self) -> None:
        self.calls = 0

    def get_optioninfo_v2(self, limit: int):
        self.calls += 1
        threading.Event().wait(0.25)
        return {'msg': {'closed_options': []}}


def _ctx(*, repo_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        repo_root=repo_root,
        resolved_config={
            'execution': {
                'account_mode': 'PRACTICE',
                'reconcile': {
                    'history_lookback_sec': 3600,
                    'not_found_grace_sec': 20,
                    'settle_grace_sec': 30,
                    'scan_without_pending': False,
                },
            }
        },
        config=SimpleNamespace(
            asset='EURUSD-OTC',
            interval_sec=300,
            config_path=repo_root / 'config' / 'live_controlled_practice.yaml',
        ),
        scope=SimpleNamespace(
            scope_tag='EURUSD-OTC_300s',
        ),
    )


def test_reconcile_scope_skips_broker_scan_without_pending(tmp_path: Path) -> None:
    result, detail = reconcile_scope(
        repo_root=tmp_path,
        ctx=_ctx(repo_root=tmp_path),
        adapter=_NoBrokerScanAdapter(),
    )

    assert result.pending_before == 0
    assert result.updated_intents == 0
    assert result.new_orphans == 0
    assert detail['skipped_broker_scan'] is True
    assert detail['reason'] == 'no_pending_intents'
    assert detail['open_orders_count'] == 0
    assert detail['closed_orders_count'] == 0


def test_iq_client_recent_closed_options_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _HangingHistoryBackend()
    client = object.__new__(IQClient)
    client.cfg = IQConfig(email='user@example.com', password='secret', balance_mode='PRACTICE')
    client.iq = backend
    client._guarded_call_cooldowns = {}
    client._maybe_throttle = lambda label: None
    client.ensure_connection = lambda: None
    client._new_api = lambda: None

    monkeypatch.setenv('IQ_EXEC_HISTORY_TIMEOUT_S', '0.05')
    monkeypatch.setenv('IQ_EXEC_HISTORY_COOLDOWN_S', '60')
    monkeypatch.setenv('IQ_EXEC_HISTORY_RETRIES', '1')

    t0 = time.perf_counter()
    payload1 = client.get_recent_closed_options(20)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.20
    assert payload1['msg']['closed_options'] == []
    assert payload1['skipped']['reason'] == 'timeout'
    assert backend.calls == 1

    payload2 = client.get_recent_closed_options(20)
    assert payload2['msg']['closed_options'] == []
    assert payload2['skipped']['reason'] == 'history_timeout_cooldown'
    assert backend.calls == 1


def test_runtime_soak_writes_interrupted_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = SimpleNamespace(
        repo_root=tmp_path,
        config=SimpleNamespace(
            asset='EURUSD-OTC',
            interval_sec=300,
            config_path=tmp_path / 'config' / 'live_controlled_practice.yaml',
        ),
        scope=SimpleNamespace(scope_tag='EURUSD-OTC_300s'),
    )

    class _Freshness:
        def as_dict(self):
            return {'artifacts': [], 'stale_artifacts': []}

    monkeypatch.setattr('natbin.runtime.soak.build_context', lambda **kwargs: ctx)
    monkeypatch.setattr('natbin.runtime.soak.inspect_runtime_freshness', lambda **kwargs: _Freshness())
    monkeypatch.setattr('natbin.runtime.soak.run_daemon', lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        build_runtime_soak_summary(repo_root=tmp_path, config_path=ctx.config.config_path, write_artifact=True, max_cycles=1)

    out_path = tmp_path / 'runs' / 'soak' / 'soak_latest_EURUSD-OTC_300s.json'
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding='utf-8'))
    assert payload['state'] == 'interrupted'
    assert payload['interrupted'] is True
    assert payload['exit_code'] == 130
    assert payload['scope']['scope_tag'] == 'EURUSD-OTC_300s'
