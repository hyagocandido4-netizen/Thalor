from __future__ import annotations

from natbin.portfolio.runner import compute_stagger_delay


def test_stagger_zero():
    assert compute_stagger_delay(0, stagger_sec=0.0, workers=2) == 0.0
    assert compute_stagger_delay(3, stagger_sec=0.0, workers=2) == 0.0


def test_stagger_parallel():
    assert compute_stagger_delay(0, stagger_sec=1.0, workers=2) == 0.0
    assert compute_stagger_delay(1, stagger_sec=1.0, workers=2) == 1.0
    assert compute_stagger_delay(2, stagger_sec=1.5, workers=2) == 3.0


def test_stagger_sequential_workers_1():
    # In sequential mode, we delay each scope after the first by a constant amount.
    assert compute_stagger_delay(0, stagger_sec=2.0, workers=1) == 0.0
    assert compute_stagger_delay(1, stagger_sec=2.0, workers=1) == 2.0
    assert compute_stagger_delay(3, stagger_sec=2.0, workers=1) == 2.0
