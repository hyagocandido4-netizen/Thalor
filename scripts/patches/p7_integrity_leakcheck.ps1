# P7: Integrity suite (leakage guard + audit helpers)
# - Adds src/natbin/leak_check.py (stdlib-only)
# - Adds docs/leak_check.md
# - Adds GitHub Actions workflow .github/workflows/integrity.yml (runs leak_check in code mode)
# Safe to run multiple times (idempotent-ish).

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Content
  )
  $dir = Split-Path -Parent $Path
  if ($dir -and !(Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  # Normalize line endings to LF to reduce diff noise across machines
  $normalized = $Content.Replace("`r`n", "`n").Replace("`r", "`n")
  Set-Content -Path $Path -Value $normalized -Encoding utf8NoBOM
  Write-Host "Wrote: $Path"
}

# --- leak_check.py ---
$leakCheckPy = @'
#!/usr/bin/env python3
"""
Leakage checks (two modes):
  - code: scan repository source for obvious time-leak patterns (CI-safe, stdlib-only)
  - data: analyze a dataset CSV if available (optional; uses pandas if installed)

This is not a full proof of "no leakage". The goal is to catch the most common mistakes early:
  - features computed with shift(-k) (future)
  - feature lists containing "*_next" columns
  - accidental inclusion of next-candle prices as features
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


BIDI_PATTERN = re.compile(r"[\uFEFF\u200E\u200F\u061C\u202A-\u202E\u2066-\u2069]")

# Heuristics: "hard" leakage patterns
RE_SHIFT_NEG = re.compile(r"\.shift\(\s*-\d+\s*\)")
RE_SUS_COL = re.compile(r"(open_next|high_next|low_next|close_next|ts_next)\b", re.IGNORECASE)

# Weak signals (warnings)
RE_WEAK_TOKENS = re.compile(r"\b(next|future|t\+1|lead)\b", re.IGNORECASE)


@dataclass
class Finding:
    level: str  # "ERROR" or "WARN"
    path: str
    line_no: int
    message: str
    line: str


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    return BIDI_PATTERN.sub("", text)


def _iter_py_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.py"):
        if any(part in (".venv", "venv", "__pycache__", "build", "dist") for part in p.parts):
            continue
        yield p


def scan_code(root: Path) -> List[Finding]:
    findings: List[Finding] = []
    py_root = root / "src" / "natbin"
    if not py_root.exists():
        return [Finding("ERROR", str(py_root), 0, "Missing src/natbin (unexpected repo layout)", "")]

    for p in _iter_py_files(py_root):
        try:
            text = _read_text(p)
        except Exception as e:
            findings.append(Finding("ERROR", str(p), 0, f"Failed to read: {e}", ""))
            continue

        for i, line in enumerate(text.splitlines(), start=1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue

            # HARD: negative shift in a likely feature line
            if RE_SHIFT_NEG.search(s):
                looks_like_feature = ("f_" in s) or ("feat" in s.lower()) or ("feature" in s.lower())
                looks_like_label = ("y_" in s) or ("label" in s.lower()) or ("target" in s.lower())
                if looks_like_feature and not looks_like_label:
                    findings.append(
                        Finding(
                            "ERROR",
                            str(p),
                            i,
                            "Possible leakage: negative shift used in feature context",
                            s,
                        )
                    )

            # HARD: obvious next-candle cols mentioned near feature definition/list
            if RE_SUS_COL.search(s) and ("FEATURE" in s or "feat" in s.lower()):
                findings.append(
                    Finding(
                        "ERROR",
                        str(p),
                        i,
                        "Suspicious '*_next' column referenced near feature definition/list",
                        s,
                    )
                )

            # WARN: weak tokens in code
            if RE_WEAK_TOKENS.search(s) and ("y_" not in s) and ("label" not in s.lower()):
                findings.append(
                    Finding(
                        "WARN",
                        str(p),
                        i,
                        "Weak leakage signal: token suggests future/next usage (review manually)",
                        s,
                    )
                )

    return findings


def _try_import_pandas():
    try:
        import pandas as pd  # type: ignore
        return pd
    except Exception:
        return None


def scan_data(csv_path: Path, label: str) -> List[Finding]:
    findings: List[Finding] = []
    if not csv_path.exists():
        findings.append(Finding("ERROR", str(csv_path), 0, "Dataset CSV not found", ""))
        return findings

    pd = _try_import_pandas()
    if pd is None:
        # Minimal header-only check without pandas
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
        if not header:
            findings.append(Finding("ERROR", str(csv_path), 0, "Empty CSV header", ""))
            return findings
        if label not in header:
            findings.append(Finding("ERROR", str(csv_path), 0, f"Label column '{label}' not found", ""))
        for col in header:
            if RE_SUS_COL.search(col):
                findings.append(Finding("WARN", str(csv_path), 0, f"Suspicious column present: {col}", ""))
        return findings

    df = pd.read_csv(csv_path)
    if label not in df.columns:
        findings.append(Finding("ERROR", str(csv_path), 0, f"Label column '{label}' not found", ""))
        return findings

    n = len(df)
    if n < 1000:
        findings.append(Finding("WARN", str(csv_path), 0, f"Dataset looks small (rows={n}); leakage tests are weak", ""))

    for c in df.columns:
        if RE_SUS_COL.search(c):
            findings.append(Finding("WARN", str(csv_path), 0, f"Suspicious column present: {c}", ""))

    # Heuristic: near-perfect correlation with label for feature-like cols
    y = df[label]
    try:
        y_num = y.astype(float)
    except Exception:
        y_num = y

    for c in df.columns:
        if c == label:
            continue
        if not (c.startswith("f_") or c in ("conf", "proba_up", "iso_score", "score", "ev")):
            continue
        s = df[c]
        try:
            s_num = s.astype(float)
        except Exception:
            continue
        if s_num.isna().all():
            continue
        corr = s_num.corr(y_num)
        if corr is None:
            continue
        if abs(corr) >= 0.98:
            findings.append(Finding("WARN", str(csv_path), 0, f"Very high |corr| with label for '{c}': {corr:.4f}", ""))

    return findings


def _print_findings(findings: Sequence[Finding]) -> None:
    if not findings:
        print("OK: no findings")
        return
    for f in findings:
        loc = f"{f.path}:{f.line_no}" if f.line_no else f.path
        print(f"[{f.level}] {loc} - {f.message}")
        if f.line:
            print(f"    {f.line}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["code", "data"], default="code", help="Check mode")
    ap.add_argument("--root", default=".", help="Repo root (for code mode)")
    ap.add_argument("--csv", default="data/dataset_phase2.csv", help="Dataset CSV path (for data mode)")
    ap.add_argument("--label", default="y_open_close", help="Label column name (for data mode)")
    ap.add_argument("--fail-on-warn", action="store_true", help="Treat WARN as failure (exit 1)")
    args = ap.parse_args(argv)

    if args.mode == "code":
        findings = scan_code(Path(args.root).resolve())
    else:
        findings = scan_data(Path(args.csv).resolve(), args.label)

    _print_findings(findings)

    has_error = any(f.level == "ERROR" for f in findings)
    has_warn = any(f.level == "WARN" for f in findings)

    if has_error:
        return 1
    if has_warn and args.fail_on_warn:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'@

# --- docs/leak_check.md ---
$leakDoc = @'
# Leak check

This repo uses time-series evaluation (walk-forward / pseudo-future) and ONLINE Top-K per day.
Small leakage bugs can inflate paper/tune results and then fail in LIVE.

This tool provides basic guardrails.

## Run (code mode, CI-safe)

```powershell
python src/natbin/leak_check.py --mode code
```

- Scans `src/natbin/*.py` for obvious time-leak patterns.
- Uses stdlib only (no pandas) so it can run in CI even without data files.

## Run (data mode, local)

```powershell
python src/natbin/leak_check.py --mode data --csv data/dataset_phase2.csv --label y_open_close
```

Notes:
- `data/` is ignored by git, so this is for local use.
- If `pandas` is installed, the tool runs a few extra heuristics.

## Interpreting results

- `ERROR`: likely time leakage (must be fixed)
- `WARN`: suspicious; review manually
'@

# --- GitHub Actions workflow (integrity) ---
$workflow = @'
name: Integrity

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  leak_check:
    runs-on: windows-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Leak check (source)
        run: |
          python src/natbin/leak_check.py --mode code
'@

Write-Utf8NoBomFile -Path "src/natbin/leak_check.py" -Content $leakCheckPy
Write-Utf8NoBomFile -Path "docs/leak_check.md" -Content $leakDoc
Write-Utf8NoBomFile -Path ".github/workflows/integrity.yml" -Content $workflow

# Quick syntax check (optional)
$py = Join-Path -Path "." -ChildPath ".venv\Scripts\python.exe"
if (Test-Path $py) {
  & $py -m compileall -q "src\natbin" | Out-Null
  Write-Host "compileall: OK"
} else {
  Write-Host "Note: .venv not found; skipped compileall."
}

Write-Host "P7 applied. Next:"
Write-Host "  python src/natbin/leak_check.py --mode code"
Write-Host "  python src/natbin/leak_check.py --mode data --csv data/dataset_phase2.csv --label y_open_close"
