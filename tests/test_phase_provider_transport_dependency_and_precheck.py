from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from natbin.adapters import iq_client as iq_client_mod
from natbin.config.loader import load_resolved_config
from natbin.runtime.connectivity import build_runtime_connectivity_payload
from natbin.runtime.failsafe import RuntimeFailsafe
from natbin.runtime.precheck import run_precheck
from natbin.state.control_repo import RuntimeControlRepository
from natbin.utils import network_transport as network_transport_mod
from natbin.utils.network_transport import NetworkTransportManager


def test_precheck_can_allow_drain_mode_for_real_preflight(tmp_path: Path) -> None:
    drain_file = tmp_path / 'runs' / 'DRAIN_MODE'
    drain_file.parent.mkdir(parents=True, exist_ok=True)
    drain_file.write_text('1\n', encoding='utf-8')

    failsafe = RuntimeFailsafe(drain_mode_file=drain_file)
    control_repo = RuntimeControlRepository(tmp_path / 'runs' / 'runtime_control.sqlite3')
    market_context = {'market_open': True, 'stale': False}

    blocked = run_precheck(
        failsafe,
        asset='EURUSD-OTC',
        interval_sec=300,
        control_repo=control_repo,
        market_context=market_context,
        now_utc=datetime.now(timezone.utc),
        allow_drain_mode=False,
    )
    assert blocked.blocked is True
    assert blocked.snapshot is not None
    assert blocked.snapshot.drain_mode_active is True
    assert blocked.snapshot.drain_mode_ignored is False

    allowed = run_precheck(
        failsafe,
        asset='EURUSD-OTC',
        interval_sec=300,
        control_repo=control_repo,
        market_context=market_context,
        now_utc=datetime.now(timezone.utc),
        allow_drain_mode=True,
    )
    assert allowed.blocked is False
    assert allowed.snapshot is not None
    assert allowed.snapshot.drain_mode_active is True
    assert allowed.snapshot.drain_mode_ignored is True



def test_iqoption_dependency_status_reports_missing_pysocks_for_socks_transport(monkeypatch) -> None:
    manager = NetworkTransportManager.from_mapping(
        {
            'enabled': True,
            'endpoint': 'socks5h://user:pass@proxy.internal:1080?name=primary',
        }
    )

    monkeypatch.setattr(iq_client_mod, '_IQ_OPTION_CLASS', object(), raising=False)
    monkeypatch.setattr(iq_client_mod, '_IQ_OPTION_IMPORT_ERROR', None, raising=False)

    original_find_spec = network_transport_mod.importlib.util.find_spec

    def _missing_socks(name: str):
        if name == 'socks':
            return None
        return original_find_spec(name)

    monkeypatch.setattr(network_transport_mod.importlib.util, 'find_spec', _missing_socks)

    status = iq_client_mod.iqoption_dependency_status(transport_manager=manager)
    assert status['available'] is False
    assert 'PySocks' in str(status['reason'])

    dependency = manager.dependency_status()
    assert dependency['available'] is False
    assert dependency['requires_pysocks'] is True
    assert 'PySocks' in str(dependency['reason'])



def test_runtime_connectivity_payload_exposes_transport_dependency_status(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / 'repo'
    (repo / 'config').mkdir(parents=True, exist_ok=True)
    config_path = repo / 'config' / 'base.yaml'
    payload = {
        'version': '2.0',
        'network': {
            'transport': {
                'enabled': True,
                'endpoint': 'socks5h://user:pass@proxy.internal:1080?name=primary',
            }
        },
        'execution': {
            'enabled': False,
            'mode': 'disabled',
            'provider': 'iqoption',
        },
        'assets': [
            {
                'asset': 'EURUSD-OTC',
                'interval_sec': 300,
                'timezone': 'UTC',
            }
        ],
    }
    config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding='utf-8')

    original_find_spec = network_transport_mod.importlib.util.find_spec

    def _missing_socks(name: str):
        if name == 'socks':
            return None
        return original_find_spec(name)

    monkeypatch.setattr(network_transport_mod.importlib.util, 'find_spec', _missing_socks)

    resolved = load_resolved_config(repo_root=repo, config_path=config_path)
    connectivity = build_runtime_connectivity_payload(resolved_config=resolved, repo_root=repo)

    assert connectivity['transport_enabled'] is True
    assert connectivity['transport_ready'] is True
    assert connectivity['transport_dependency_available'] is False
    assert connectivity['transport_requires_pysocks'] is True
    assert 'PySocks' in str(connectivity['transport_dependency_reason'])
    assert connectivity['transport']['dependency_status']['available'] is False
