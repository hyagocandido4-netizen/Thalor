from datetime import datetime

from natbin.runtime.gates.cpreg import compute_cp_alpha_from_env


def test_cpreg_disabled_returns_none(monkeypatch):
    monkeypatch.delenv("CPREG_ENABLE", raising=False)
    assert compute_cp_alpha_from_env(datetime(2026, 3, 9, 12, 0, 0), executed_today=0) is None


def test_cpreg_piecewise_and_slot2_mult(monkeypatch):
    monkeypatch.setenv("CPREG_ENABLE", "1")
    monkeypatch.setenv("CPREG_ALPHA_START", "0.06")
    monkeypatch.setenv("CPREG_ALPHA_END", "0.09")
    monkeypatch.setenv("CPREG_WARMUP_FRAC", "0.50")
    monkeypatch.setenv("CPREG_RAMP_END_FRAC", "0.90")
    monkeypatch.setenv("CPREG_SLOT2_MULT", "0.85")

    # 10:00 is within warmup (frac_day ~= 0.4167)
    a_warm = compute_cp_alpha_from_env(datetime(2026, 3, 9, 10, 0, 0), executed_today=0)
    assert a_warm is not None
    assert abs(a_warm - 0.06) < 1e-9

    # 23:00 is after ramp end (frac_day ~= 0.9583)
    a_late = compute_cp_alpha_from_env(datetime(2026, 3, 9, 23, 0, 0), executed_today=0)
    assert a_late is not None
    assert abs(a_late - 0.09) < 1e-9

    # 16:48 gives frac_day = 0.70 exactly (16*3600 + 48*60 = 60480; 60480/86400=0.70)
    a_mid = compute_cp_alpha_from_env(datetime(2026, 3, 9, 16, 48, 0), executed_today=0)
    assert a_mid is not None
    assert abs(a_mid - 0.075) < 1e-9

    # Slot 2 multiplier applies when executed_today >= 1.
    a_mid_slot2 = compute_cp_alpha_from_env(datetime(2026, 3, 9, 16, 48, 0), executed_today=1)
    assert a_mid_slot2 is not None
    assert abs(a_mid_slot2 - (0.075 * 0.85)) < 1e-9
