from __future__ import annotations

from pathlib import Path

import yaml

import natbin.ops.evidence_window_scan as module


def test_canary_config_keeps_conservative_envelope() -> None:
    cfg_path = Path('config/practice_portfolio_canary.yaml')
    assert cfg_path.exists()
    payload = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))
    assert payload['multi_asset']['enabled'] is True
    assert payload['multi_asset']['max_parallel_assets'] == 1
    assert payload['multi_asset']['portfolio_topk_total'] == 1
    assert payload['multi_asset']['portfolio_hard_max_positions'] == 1
    assert payload['execution']['account_mode'] == 'PRACTICE'
    assert payload['broker']['balance_mode'] == 'PRACTICE'
    assert float(payload['execution']['stake']['amount']) <= 5.0
    assert len(payload['assets']) == 6


def test_score_scope_prefers_provider_ready_open_scope(tmp_path: Path) -> None:
    repo = tmp_path
    cfg = tmp_path / 'config.yaml'
    cfg.write_text('version: "2.0"\nassets: []\n', encoding='utf-8')
    scope = {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': 'EURUSD-OTC_300s'}
    doctor = {'severity': 'ok', 'ready_for_cycle': True, 'ready_for_practice': True, 'blockers': [], 'warnings': []}
    probe = {
        'severity': 'ok',
        'ok': True,
        'checks': [],
        'actions': [],
        'shared_provider_session': {'attempted': True, 'ok': True},
        'local_market_context': {'fresh': True, 'market_open': True},
        'remote_candles': {'attempted': True, 'ok': True},
        'remote_market_context': {'attempted': True, 'ok': True},
    }
    board = {'budget_left': 1, 'pending_unknown': 0, 'open_positions': 0, 'latest_action': 'CALL'}
    intel = {'feedback_blocked': False, 'portfolio_score': 0.8, 'intelligence_score': 0.7}
    scored = module._score_scope(repo=repo, cfg_path=cfg, scope=scope, doctor=doctor, probe=probe, board=board, intel=intel)
    assert scored['window_state'] == 'ready'
    assert float(scored['score']) > 70.0
    assert scored['commands']['asset_candidate'].endswith('--json')


def test_score_scope_holds_when_blocked_by_feedback(tmp_path: Path) -> None:
    repo = tmp_path
    cfg = tmp_path / 'config.yaml'
    cfg.write_text('version: "2.0"\nassets: []\n', encoding='utf-8')
    scope = {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': 'EURUSD-OTC_300s'}
    doctor = {'severity': 'ok', 'ready_for_cycle': True, 'ready_for_practice': True, 'blockers': [], 'warnings': []}
    probe = {
        'severity': 'ok',
        'ok': True,
        'checks': [],
        'actions': [],
        'shared_provider_session': {'attempted': True, 'ok': True},
        'local_market_context': {'fresh': True, 'market_open': True},
        'remote_candles': {'attempted': True, 'ok': True},
        'remote_market_context': {'attempted': True, 'ok': True},
    }
    board = {'budget_left': 1, 'pending_unknown': 0, 'open_positions': 0, 'latest_action': 'CALL'}
    intel = {'feedback_blocked': True, 'feedback_reason': 'regime_block'}
    scored = module._score_scope(repo=repo, cfg_path=cfg, scope=scope, doctor=doctor, probe=probe, board=board, intel=intel)
    assert scored['window_state'] in {'watch', 'hold'}
    assert scored['recommended_action'] == 'hold_regime_block'



def test_runtime_app_parser_accepts_evidence_window_scan() -> None:
    from natbin.control.app import _build_parser

    parser = _build_parser()
    ns = parser.parse_args(['--config', 'config/practice_portfolio_canary.yaml', 'evidence-window-scan', '--all-scopes', '--json'])
    assert ns.command == 'evidence-window-scan'
    assert ns.all_scopes is True
    assert ns.json is True
