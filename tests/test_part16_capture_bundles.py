from __future__ import annotations

import json
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1] / 'scripts' / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import capture_diagnostics_bundle as diag_mod  # type: ignore


def test_practice_verdict_prefers_preflight_and_post_soak_diag(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    (bundle / 'practice_preflight').mkdir(parents=True)
    (bundle / 'diag_suite_post').mkdir(parents=True)
    (bundle / 'doctor_post').mkdir(parents=True)

    (bundle / 'practice_preflight' / 'last_json.json').write_text(
        json.dumps({'kind': 'practice_preflight', 'ok': True, 'severity': 'ok', 'ready_for_long_practice': True}),
        encoding='utf-8',
    )
    (bundle / 'diag_suite_post' / 'last_json.json').write_text(
        json.dumps({'kind': 'diag_suite', 'ok': True, 'severity': 'ok', 'ready_for_practice': True, 'blockers': [], 'warnings': []}),
        encoding='utf-8',
    )
    (bundle / 'doctor_post' / 'last_json.json').write_text(
        json.dumps({'kind': 'production_doctor', 'ok': True, 'severity': 'ok', 'ready_for_practice': True, 'ready_for_real': False}),
        encoding='utf-8',
    )

    verdict = diag_mod._practice_verdict(bundle_dir=bundle, include_post_soak_diag=True)
    assert verdict['ready_for_long_practice'] is True
    assert verdict['severity'] == 'ok'
    assert verdict['post_soak_diag_suite']['ready_for_practice'] is True
    assert verdict['doctor_ready_for_practice'] is True
