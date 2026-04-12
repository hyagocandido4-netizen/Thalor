from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.ops.diagnostic_utils import resolve_scope_paths
from natbin.ops.provider_probe import build_provider_probe_payload
from natbin.portfolio.runner import load_scopes
from natbin.runtime.scope import market_context_path
from natbin.state.control_repo import read_control_artifact, read_repo_control_artifact
from natbin.state.db import open_db, upsert_candles


class ProbeClient:
    def __init__(self) -> None:
        self.connected = False

    def connect(self, retries=None, sleep_s=None):
        self.connected = True

    def ensure_connection(self):
        if not self.connected:
            raise RuntimeError('not_connected')

    def get_candles(self, asset: str, interval_sec: int, count: int, endtime: int):
        rows = []
        for idx in range(int(count)):
            ts = int(endtime) - (int(count) - idx) * int(interval_sec)
            rows.append(
                {
                    'from': ts,
                    'open': 1.10,
                    'high': 1.20,
                    'low': 1.00,
                    'close': 1.15,
                    'volume': 100.0 + idx,
                }
            )
        return rows

    def get_market_context(self, asset: str, interval_sec: int, payout_fallback: float = 0.8):
        return {
            'asset_requested': asset,
            'asset_resolved': asset,
            'market_open': True,
            'open_source': 'provider_probe',
            'payout': 0.85,
            'payout_source': 'turbo',
        }


class ProbeAdapter:
    def _dependency_status(self):
        return {'available': True, 'reason': None}

    def _credentials(self):
        return 'user@example.com', 'super-secret'

    def _make_client(self):
        return ProbeClient()

    def _connect_kwargs(self):
        return {}



def _write_config(
    repo_root: Path,
    *,
    multi_asset: bool,
    execution_mode: str = 'live',
    account_mode: str = 'PRACTICE',
    broker_balance_mode: str = 'PRACTICE',
) -> Path:
    lines = [
        'version: "2.0"',
        'runtime:',
        '  profile: diagnostic_test',
        'execution:',
        '  enabled: true',
        f'  mode: {execution_mode}',
        '  provider: iqoption',
        f'  account_mode: {account_mode}',
        'broker:',
        '  provider: iqoption',
        f'  balance_mode: {broker_balance_mode}',
        'data:',
        '  db_path: data/market_otc.sqlite3',
        '  dataset_path: data/dataset_phase2.csv',
        'multi_asset:',
        f'  enabled: {str(multi_asset).lower()}',
        '  max_parallel_assets: 6',
        '  partition_data_paths: true',
        '  data_db_template: data/market_{scope_tag}.sqlite3',
        '  dataset_path_template: data/datasets/{scope_tag}/dataset.csv',
        'assets:',
        '  - asset: EURUSD-OTC',
        '    interval_sec: 300',
        '    timezone: UTC',
    ]
    if multi_asset:
        lines.extend(
            [
                '  - asset: GBPUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
            ]
        )
    cfg = repo_root / 'config' / 'provider_probe.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return cfg



def _seed_local_scope_state(repo_root: Path, cfg_path: Path) -> None:
    scopes, cfg = load_scopes(repo_root=repo_root, config_path=cfg_path)
    now_ts = int(datetime.now(UTC).timestamp())
    for scope in scopes:
        resolved = resolve_scope_paths(repo_root=repo_root, cfg=cfg, scope=scope)
        data_paths = resolved['data']
        con = open_db(str(data_paths.db_path))
        candles = [
            {
                'from': now_ts - (5 - idx) * int(scope.interval_sec),
                'open': 1.10,
                'high': 1.20,
                'low': 1.00,
                'close': 1.15,
                'volume': 100.0 + idx,
            }
            for idx in range(5)
        ]
        upsert_candles(con, scope.asset, scope.interval_sec, candles)
        con.close()

        dataset_path = Path(str(data_paths.dataset_path))
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        dataset_path.write_text('ts,feature_a\n1,0.1\n2,0.2\n', encoding='utf-8')

        mc_path = Path(
            market_context_path(
                asset=scope.asset,
                interval_sec=int(scope.interval_sec),
                out_dir=repo_root / 'runs',
            )
        )
        mc_path.parent.mkdir(parents=True, exist_ok=True)
        mc_path.write_text(
            json.dumps(
                {
                    'asset': scope.asset,
                    'interval_sec': int(scope.interval_sec),
                    'market_open': True,
                    'open_source': 'db_fresh',
                    'payout': 0.85,
                    'payout_source': 'turbo',
                    'last_candle_ts': now_ts,
                    'dependency_available': True,
                    'dependency_reason': None,
                    'at_utc': datetime.now(UTC).isoformat(timespec='seconds'),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )



def _clean_security_payload(repo_root, config_path, resolved_config, source_trace):
    return {
        'ok': True,
        'blocked': False,
        'severity': 'ok',
        'credential_source': 'external_secret_file',
        'checks': [],
        'source_trace': list(source_trace or []),
    }



def test_provider_probe_active_scope_with_stub_adapter(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, multi_asset=False)
    _seed_local_scope_state(tmp_path, cfg)

    from natbin.ops import provider_probe as module

    monkeypatch.setattr(module, 'audit_security_posture', _clean_security_payload)
    monkeypatch.setattr(module, 'adapter_from_context', lambda ctx, repo_root=None: ProbeAdapter())

    payload = build_provider_probe_payload(repo_root=tmp_path, config_path=cfg, active=True, sample_candles=3)

    assert payload['kind'] == 'provider_probe'
    assert payload['ok'] is True
    assert payload['severity'] == 'ok'
    assert payload['summary']['scope_count'] == 1
    assert payload['shared_provider_session']['ok'] is True
    scope = payload['scope_results'][0]
    assert scope['remote_candles']['ok'] is True
    assert scope['remote_market_context']['ok'] is True
    assert scope['local_market_context']['fresh'] is True
    assert read_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='provider_probe') is not None



def test_provider_probe_flags_mode_alignment_with_secret_bundle_hint(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, multi_asset=False, account_mode='REAL', broker_balance_mode='PRACTICE')
    _seed_local_scope_state(tmp_path, cfg)

    bundle = tmp_path / 'config' / 'broker_secrets.yaml'
    bundle.write_text('broker:\n  balance_mode: PRACTICE\n', encoding='utf-8')

    from natbin.ops import provider_probe as module

    monkeypatch.setattr(module, 'audit_security_posture', _clean_security_payload)
    monkeypatch.setenv('THALOR_SECRETS_FILE', str(bundle))

    payload = build_provider_probe_payload(repo_root=tmp_path, config_path=cfg, active=False)

    assert payload['ok'] is False
    scope = payload['scope_results'][0]
    mode_check = next(item for item in scope['checks'] if item['name'] == 'mode_alignment')
    assert mode_check['status'] == 'error'
    assert mode_check['suspected_secret_bundle_override']['present'] is True
    assert 'sobrescrevendo balance_mode' in mode_check['message']


def test_provider_probe_passive_only_warnings_are_informational(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, multi_asset=False)
    _seed_local_scope_state(tmp_path, cfg)

    from natbin.ops import provider_probe as module

    monkeypatch.setattr(module, 'audit_security_posture', _clean_security_payload)
    monkeypatch.setattr(module, 'adapter_from_context', lambda ctx, repo_root=None: ProbeAdapter())

    payload = build_provider_probe_payload(repo_root=tmp_path, config_path=cfg, active=False)

    assert payload['ok'] is True
    assert payload['severity'] == 'ok'
    assert next(item for item in payload['checks'] if item['name'] == 'provider_session')['status'] == 'warn'



def test_provider_probe_all_scopes_writes_repo_artifact(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, multi_asset=True)
    _seed_local_scope_state(tmp_path, cfg)

    from natbin.ops import provider_probe as module

    monkeypatch.setattr(module, 'audit_security_posture', _clean_security_payload)
    monkeypatch.setattr(module, 'adapter_from_context', lambda ctx, repo_root=None: ProbeAdapter())

    payload = build_provider_probe_payload(repo_root=tmp_path, config_path=cfg, all_scopes=True, active=True, sample_candles=2)

    assert payload['summary']['scope_count'] == 2
    assert payload['summary']['provider_ready_scopes'] == 2
    assert payload['summary']['multi_asset_enabled'] is True
    assert payload['summary']['max_parallel_assets'] == 6
    assert read_repo_control_artifact(repo_root=tmp_path, name='provider_probe') is not None


def test_provider_probe_keeps_local_cache_staleness_informational_when_remote_path_is_healthy(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, multi_asset=False)
    _seed_local_scope_state(tmp_path, cfg)

    from natbin.ops import provider_probe as module

    monkeypatch.setattr(module, 'audit_security_posture', _clean_security_payload)
    monkeypatch.setattr(module, 'adapter_from_context', lambda ctx, repo_root=None: ProbeAdapter())

    scopes, resolved_cfg = load_scopes(repo_root=tmp_path, config_path=cfg)
    scope = scopes[0]
    resolved = resolve_scope_paths(repo_root=tmp_path, cfg=resolved_cfg, scope=scope)
    data_paths = resolved['data']
    con = open_db(str(data_paths.db_path))
    con.execute('DELETE FROM candles WHERE asset=? AND interval_sec=?', (scope.asset, int(scope.interval_sec)))
    con.commit()
    con.close()

    payload = build_provider_probe_payload(repo_root=tmp_path, config_path=cfg, active=True, sample_candles=3)

    assert payload['ok'] is True
    assert payload['severity'] == 'ok'
    scope_payload = payload['scope_results'][0]
    checks = {item['name']: item for item in scope_payload['checks']}
    assert checks['candle_db_local']['status'] == 'ok'
    assert checks['candle_db_local']['advisory_only'] is True
    assert scope_payload['remote_candles']['ok'] is True



class ProbeBudgetClient(ProbeClient):
    def __init__(self) -> None:
        super().__init__()
        self.connect_calls: list[dict[str, object]] = []

    def connect(self, retries=None, sleep_s=None, connect_timeout_s=None):
        self.connect_calls.append({
            'retries': retries,
            'sleep_s': sleep_s,
            'connect_timeout_s': connect_timeout_s,
        })
        self.connected = True


class ProbeBudgetAdapter(ProbeAdapter):
    def __init__(self) -> None:
        self.client = ProbeBudgetClient()

    def _make_client(self):
        return self.client

    def _connect_kwargs(self):
        return {'retries': 8, 'sleep_s': 2.5, 'connect_timeout_s': 25.0}



def test_provider_probe_caps_connect_budget_for_active_probe(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, multi_asset=False)
    _seed_local_scope_state(tmp_path, cfg)

    from natbin.ops import provider_probe as module

    adapter = ProbeBudgetAdapter()
    monkeypatch.setattr(module, 'audit_security_posture', _clean_security_payload)
    monkeypatch.setattr(module, 'adapter_from_context', lambda ctx, repo_root=None: adapter)
    monkeypatch.delenv('THALOR_PROVIDER_PROBE_CONNECT_TIMEOUT_S', raising=False)

    payload = build_provider_probe_payload(repo_root=tmp_path, config_path=cfg, active=True, sample_candles=1)

    assert payload['ok'] is True
    assert adapter.client.connect_calls
    call = adapter.client.connect_calls[-1]
    assert call['retries'] == 3
    assert call['sleep_s'] == 1.0
    assert call['connect_timeout_s'] == 25.0
