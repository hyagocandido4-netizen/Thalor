#!/usr/bin/env python
from __future__ import annotations

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / 'scripts' / 'tools' / 'intelligence_pack_smoke.py'

if __name__ == '__main__':
    runpy.run_path(str(TARGET), run_name='__main__')
