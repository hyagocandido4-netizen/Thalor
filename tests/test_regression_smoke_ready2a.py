from __future__ import annotations


def test_root_observer_compat_exports_private_live_signals_helper() -> None:
    from natbin.observe_signal_topk_perday import _resolve_live_signals_csv_path

    row = {
        "day": "2026-02-28",
        "asset": "EURUSD-OTC",
        "interval_sec": 300,
        "ts": 1772316300,
        "dt_local": "2026-02-28 19:05:00",
        "proba_up": 0.5,
        "conf": 0.5,
        "regime_ok": 1,
        "threshold": 0.02,
        "action": "HOLD",
        "reason": "smoke",
    }

    path = _resolve_live_signals_csv_path(row)
    assert "20260228" in path
    assert "EURUSD-OTC" in path
