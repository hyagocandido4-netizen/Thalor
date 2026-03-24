from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from natbin.control.commands import asset_candidate_payload, asset_prepare_payload
from natbin.portfolio.models import CandidateDecision, PortfolioScope


class _DummyPaths:
    def as_dict(self) -> dict[str, str]:
        return {
            'candles_db_path': 'data/candles.sqlite3',
            'dataset_path': 'data/dataset_phase2.csv',
            'summary_path': 'runs/summary_latest.json',
        }


class _DummyRuntimePaths:
    def __init__(self) -> None:
        self.signals_db_path = 'runs/signals.sqlite3'
        self.state_db_path = 'runs/state.sqlite3'


class _DummyOutcome:
    def __init__(self, name: str, returncode: int = 0) -> None:
        self.name = name
        self.returncode = int(returncode)

    def as_dict(self) -> dict[str, object]:
        return {'name': self.name, 'returncode': self.returncode}


class _Cfg:
    def __init__(self) -> None:
        self.multi_asset = SimpleNamespace(enabled=False)


def _scope() -> PortfolioScope:
    return PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s')


def test_asset_prepare_payload_no_longer_raises_nameerror(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    cfg_path = repo / 'config' / 'live_controlled_practice.yaml'
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('version: "2.0"\n', encoding='utf-8')

    cfg_obj = _Cfg()

    monkeypatch.setattr('natbin.portfolio.runner.load_scopes', lambda **_: ([_scope()], cfg_obj))
    monkeypatch.setattr('natbin.portfolio.runner._scope_data_paths', lambda *_args, **_kwargs: _DummyPaths())
    monkeypatch.setattr(
        'natbin.portfolio.runner.prepare_scope',
        lambda **_: [
            _DummyOutcome('collect_recent:EURUSD-OTC_300s'),
            _DummyOutcome('make_dataset:EURUSD-OTC_300s'),
            _DummyOutcome('refresh_market_context:EURUSD-OTC_300s'),
        ],
    )

    payload = asset_prepare_payload(
        repo_root=repo,
        config_path=cfg_path,
        asset='EURUSD-OTC',
        interval_sec=300,
        lookback_candles=2000,
    )

    assert payload['phase'] == 'asset_prepare'
    assert payload['ok'] is True
    assert payload['scope']['scope_tag'] == 'EURUSD-OTC_300s'
    assert len(payload['steps']) == 3


def test_asset_candidate_payload_passes_cfg_for_intelligence_enrichment(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    cfg_path = repo / 'config' / 'live_controlled_practice.yaml'
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('version: "2.0"\n', encoding='utf-8')

    cfg_obj = _Cfg()
    captured: dict[str, object] = {}

    monkeypatch.setattr('natbin.portfolio.runner.load_scopes', lambda **_: ([_scope()], cfg_obj))
    monkeypatch.setattr('natbin.portfolio.runner._scope_data_paths', lambda *_args, **_kwargs: _DummyPaths())
    monkeypatch.setattr('natbin.portfolio.paths.resolve_scope_runtime_paths', lambda *_args, **_kwargs: _DummyRuntimePaths())

    def fake_candidate_scope(**kwargs):
        captured['cfg'] = kwargs.get('cfg')
        return (
            _DummyOutcome('observe_once:EURUSD-OTC_300s'),
            CandidateDecision(
                scope_tag='EURUSD-OTC_300s',
                asset='EURUSD-OTC',
                interval_sec=300,
                day='2026-03-21',
                ts=1773340200,
                action='HOLD',
                score=0.51,
                conf=0.57,
                ev=0.03,
                reason='no_trade_action',
                blockers=None,
                decision_path='runs/decision_latest_EURUSD-OTC_300s.json',
                raw={'kind': 'candidate'},
            ),
        )

    monkeypatch.setattr('natbin.portfolio.runner.candidate_scope', fake_candidate_scope)

    payload = asset_candidate_payload(
        repo_root=repo,
        config_path=cfg_path,
        asset='EURUSD-OTC',
        interval_sec=300,
        topk=1,
        lookback_candles=2000,
    )

    assert payload['phase'] == 'asset_candidate'
    assert payload['ok'] is True
    assert payload['candidate']['scope_tag'] == 'EURUSD-OTC_300s'
    assert captured['cfg'] is cfg_obj
