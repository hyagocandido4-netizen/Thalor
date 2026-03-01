#!/usr/bin/env python3
"""
P35d — Hard reset envutil to be stdlib-only and fix env_* imports across natbin.

Why:
- Selfcheck shows: "cannot import name 'env_bool' from partially initialized module natbin.envutil"
  which is a circular import symptom.
- Also observed NameError: env_int not defined (missing import) after the envutil refactor.

What this patch does:
1) Overwrites src/natbin/envutil.py with a standalone implementation (no natbin imports).
2) Ensures any natbin module that calls env_* has a stable import block:
      from .envutil import env_float, env_int, env_bool, env_str
   (plus a fallback for running as a script)
3) Runs a minimal smoke import (envutil + gate_meta) and py_compile checks.

Safe: creates timestamped backups for every modified file.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import time
import py_compile
from pathlib import Path
from typing import Iterable


ENVUTIL_TEXT = r