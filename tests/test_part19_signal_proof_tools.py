
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.tools import portfolio_canary_warmup as warmup  # type: ignore
from scripts.tools import portfolio_canary_signal_proof as proof  # type: ignore


class _Scope:
    def __init__(self, asset: str, interval_sec: int) -> None:
        self.asset = asset
        self.interval_sec = interval_sec
        self.scope_tag = f"{asset}_{interval_sec}s"


def test_warmup_effective_ok_when_market_context_fresh(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'runs').mkdir()
    cfg = repo / 'config.yaml'
    cfg.write_text('x: 1\n', encoding='utf-8')
    scope = _Scope('EURUSD-OTC', 300)
    mc = repo / 'runs' / 'market_context_EURUSD-OTC_300s.json'
    mc.write_text(json.dumps({'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'), 'market_open': True, 'open_source': 'db_fresh', 'dependency_available': True}), encoding='utf-8')

    monkeypatch.setattr(warmup, 'load_selected_scopes', lambda **kwargs: (repo, cfg, object(), [scope]))
    monkeypatch.setattr(warmup, '_find_python', lambda repo_root: sys.executable)

    class _Proc:
        returncode = 2
        stdout = '{}\n'
        stderr = ''

    monkeypatch.setattr(warmup.subprocess, 'run', lambda *args, **kwargs: _Proc())

    payload = warmup.build_warmup_payload(repo_root=repo, config_path=cfg, all_scopes=True)
    assert payload['ok'] is True
    assert payload['severity'] == 'ok'
    assert payload['summary']['prepare_ok_scopes'] == 0
    assert payload['summary']['effective_ready_scopes'] == 1
    assert payload['summary']['prepare_failed_but_fresh_scopes'] == 1


def test_signal_proof_flags_cp_meta_missing(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    cfg = repo / 'config.yaml'
    cfg.write_text('x: 1\n', encoding='utf-8')
    scope = _Scope('AUDUSD-OTC', 300)
    monkeypatch.setattr(proof, 'load_selected_scopes', lambda **kwargs: (repo, cfg, object(), [scope]))

    payload = {
        'phase': 'asset_candidate',
        'ok': True,
        'candidate': {
            'action': 'HOLD',
            'reason': 'regime_block',
            'blockers': 'gate_fail_closed;below_ev_threshold;not_in_topk_today',
            'raw': {
                'gate_mode': 'cp_fail_closed_missing_cp_meta',
                'gate_fail_detail': 'cp_fail_closed_missing_cp_meta',
                'reason': 'regime_block',
                'conf': 0.55,
                'score': 0.0,
                'ev': -1.0,
            },
        },
    }

    monkeypatch.setattr(proof, '_candidate_cmd', lambda repo, cfg_path, asset, interval_sec: ['python', '-m', 'x'])
    monkeypatch.setattr(proof, '_run_cmd', lambda *args, **kwargs: {'returncode': 0, 'timed_out': False, 'duration_sec': 1.2, 'stdout': json.dumps(payload), 'stderr': '', 'last_json': payload})

    out = proof.build_signal_proof_payload(repo_root=repo, config_path=cfg, all_scopes=True)
    assert out['ok'] is True
    assert out['summary']['cp_meta_missing_scopes'] == 1
    assert out['summary']['actionable_scopes'] == 0
    assert out['summary']['recommended_action'] == 'audit_cp_meta'
    assert out['best_watch_scope'] is None
    assert out['scope_results'][0]['cp_meta_missing'] is True
    assert out['scope_results'][0]['recommended_action'] == 'audit_cp_meta'
