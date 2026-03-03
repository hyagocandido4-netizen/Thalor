# P7.1: Refine leak_check (reduce false positives, strengthen strong patterns)
# - Updates src/natbin/leak_check.py
# - Updates docs/leak_check.md
# - Keeps .github/workflows/integrity.yml compatible (still runs code mode)
# Safe to run multiple times.

#requires -Version 7.0
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Content
  )
  $dir = Split-Path -Parent $Path
  if ($dir -and !(Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $normalized = $Content.Replace("`r`n", "`n").Replace("`r", "`n")
  Set-Content -Path $Path -Value $normalized -Encoding utf8NoBOM
  Write-Host "Wrote: $Path"
}

$leakCheckPy = @'
#!/usr/bin/env python3
"""
Leakage checks (two modes):
  - code: scan repository source for obvious time-leak patterns (CI-safe, stdlib-only)
  - data: analyze a dataset CSV if available (optional; uses pandas if installed)

This is NOT a proof of "no leakage". It is a guardrail to catch the most common mistakes:
  - feature definitions accidentally using future rows (shift(-k), roll(-k), lead)
  - feature lists accidentally including "*_next" columns
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


BIDI_PATTERN = re.compile(r"[\ufeff\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069]")

# Strong-ish patterns (likely leakage when used inside features)
RE_SHIFT_NEG = re.compile(r"shift\(\s*-\s*\d+\s*\)")
RE_ROLL_NEG = re.compile(r"np\.roll\([^)]*,\s*-\s*\d+\s*\)")
RE_SUS_COL = re.compile(r"(open_next|high_next|low_next|close_next|ts_next)", re.IGNORECASE)

# Weak signals (warnings). NOTE: we intentionally do NOT include plain "next"
# because Python's built-in next() and iterator patterns are common and create noise.
RE_WEAK_TOKENS = re.compile(r"(future|lookahead|t\+1|lead)", re.IGNORECASE)


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


def _looks_like_feature_context(s: str) -> bool:
    s_low = s.lower()
    return ("f_" in s) or ("feature" in s_low) or ("feat" in s_low)


def _looks_like_label_context(s: str) -> bool:
    s_low = s.lower()
    return ("y_" in s) or ("y_open_close" in s_low) or ("label" in s_low) or ("target" in s_low)


def scan_code(root: Path) -> List[Finding]:
    findings: List[Finding] = []
    py_root = root / "src" / "natbin"
    if not py_root.exists():
        return [Finding("ERROR", str(py_root), 0, "Missing src/natbin (unexpected repo layout)", "")]

    for p in _iter_py_files(py_root):
        # Don't scan this tool itself (prevents self-noise)
        if p.name == "leak_check.py":
            continue

        try:
            text = _read_text(p)
        except Exception as e:
            findings.append(Finding("ERROR", str(p), 0, f"Failed to read: {e}", ""))
            continue

        for i, line in enumerate(text.splitlines(), start=1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue

            feat_ctx = _looks_like_feature_context(s)
            label_ctx = _looks_like_label_context(s)

            # HARD-ish: negative shift/roll inside feature context (likely leakage)
            if (RE_SHIFT_NEG.search(s) or RE_ROLL_NEG.search(s)) and feat_ctx and not label_ctx:
                findings.append(
                    Finding(
                        "ERROR",
                        str(p),
                        i,
                        "Possible leakage: future shift/roll used in feature context",
                        s,
                    )
                )

            # HARD-ish: obvious next-candle cols mentioned near features
            if RE_SUS_COL.search(s) and feat_ctx and not label_ctx:
                findings.append(
                    Finding(
                        "ERROR",
                        str(p),
                        i,
                        "Suspicious '*_next' column referenced in feature context",
                        s,
                    )
                )

            # WARN: weak tokens (review)
            if RE_WEAK_TOKENS.search(s) and not label_ctx:
                findings.append(
                    Finding(
                        "WARN",
                        str(p),
                        i,
                        "Weak leakage signal: token suggests lookahead usage (review manually)",
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
        if RE_SUS_COL.search(c) and c != label:
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
        if getattr(s_num, "isna")().all():
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

$leakDoc = @'
# Leak check

O projeto usa avaliação temporal (walk-forward / pseudo-futuro) e ONLINE Top‑K por dia.
Pequenos bugs de *leakage* podem inflar paper/tune e quebrar no LIVE.

Este utilitário é um *guardrail* (não é prova formal de ausência de leakage).

## Code mode (CI-safe)

```powershell
python src/natbin/leak_check.py --mode code
```

- Escaneia `src/natbin/*.py` em busca de padrões comuns de lookahead (shift/roll negativos em contexto de features, etc.)
- Só usa stdlib, então roda em CI mesmo sem dataset.

## Data mode (local)

```powershell
python src/natbin/leak_check.py --mode data --csv data/dataset_phase2.csv --label y_open_close
```

- `data/` é ignorado pelo git, então isso é para rodar localmente.
- Se `pandas` existir, roda heurísticas extras (ex.: correlação extrema com o label).

## Severidade

- `ERROR`: provável leakage (corrigir)
- `WARN`: suspeito (revisar)
'@

Write-Utf8NoBomFile -Path "src/natbin/leak_check.py" -Content $leakCheckPy
Write-Utf8NoBomFile -Path "docs/leak_check.md" -Content $leakDoc

# Optional: quick syntax check
$py = Join-Path -Path "." -ChildPath ".venv\Scripts\python.exe"
if (Test-Path $py) {
  & $py -m compileall -q "src
atbin" | Out-Null
  Write-Host "compileall: OK"
} else {
  Write-Host "Note: .venv not found; skipped compileall."
}

Write-Host "P7.1 applied. Re-run:"
Write-Host "  python src/natbin/leak_check.py --mode code"
Write-Host "  python src/natbin/leak_check.py --mode data --csv data/dataset_phase2.csv --label y_open_close"