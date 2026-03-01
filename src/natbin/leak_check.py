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
from typing import Iterable, List


# Remove bidi/zero-width controls that can hide code (defense-in-depth)
BIDI_PATTERN = re.compile(r"[\u200b\u200c\u200d\u2060\uFEFF]")


# Strong patterns (very likely leakage when used outside label construction)
RE_SHIFT_NEG = re.compile(r"\bshift\(\s*-\s*\d+\s*\)")
RE_ROLL_NEG = re.compile(r"\bnp\.roll\([^)]*,\s*-\s*\d+\s*\)")
RE_SUS_COL = re.compile(r"\b(open_next|high_next|low_next|close_next|ts_next)\b", re.IGNORECASE)

# Weak signals (warnings). NOTE: intentionally do NOT include plain "next" because
# Python's built-in next() and iterator patterns are common and create noise.
RE_WEAK_TOKENS = re.compile(r"\b(future|lookahead|t\+1|lead)\b", re.IGNORECASE)


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

            # Hard-ish: negative shift/roll inside feature context (likely leakage)
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

            # Hard-ish: obvious next-candle cols mentioned near features
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

            # Warn: weak tokens (review)
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
        if RE_SUS_COL.search(str(c)) and str(c) != label:
            findings.append(Finding("WARN", str(csv_path), 0, f"Suspicious column present: {c}", ""))

    # Simple sanity: make sure sorted by ts (if present)
    if "ts" in df.columns:
        try:
            ts = df["ts"].astype(int).to_numpy()
            if (ts[1:] < ts[:-1]).any():
                findings.append(Finding("ERROR", str(csv_path), 0, "Dataset not sorted by ts (time)", ""))
        except Exception:
            pass

    return findings


def _print(findings: List[Finding]) -> int:
    errors = 0
    for f in findings:
        if f.level.upper() == "ERROR":
            errors += 1
        print(f"[{f.level}] {f.path}:{f.line_no} - {f.message}")
        if f.line:
            print(f"    {f.line}")
    if errors == 0:
        print("OK: no findings")
    return 1 if errors else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["code", "data"], default="code")
    ap.add_argument("--csv", type=str, default="data/dataset_phase2.csv")
    ap.add_argument("--label", type=str, default="y_open_close")
    args = ap.parse_args()

    root = Path(".").resolve()
    if args.mode == "code":
        rc = _print(scan_code(root))
        raise SystemExit(rc)

    csv_path = Path(args.csv)
    rc = _print(scan_data(csv_path, label=args.label))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
