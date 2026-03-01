#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P22 - gate_meta compat + restore public constants

Fixes:
- train_base_cal_iso_meta() signature compatibility with older callers that pass meta_model_type=...
- restore GATE_VERSION / META_FEATURES exports used by observe_signal_topk_perday.py
- re-enable "logreg" as meta_model option (optional, but keeps historical compat)

This patch is designed to be idempotent and minimally invasive.
"""
from __future__ import annotations

import re
import sys
import shutil
import datetime as _dt
from pathlib import Path
import py_compile


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(path: Path) -> Path:
    b = path.with_suffix(path.suffix + f".bak_{_ts()}")
    shutil.copy2(path, b)
    return b


def ensure_imports(txt: str) -> tuple[str, bool]:
    changed = False
    need_lr = "from sklearn.linear_model import LogisticRegression" not in txt
    need_pipe = "from sklearn.pipeline import Pipeline" not in txt
    need_scaler = "from sklearn.preprocessing import StandardScaler" not in txt

    if not (need_lr or need_pipe or need_scaler):
        return txt, False

    # Insert after the IsotonicRegression import if possible.
    anchor = "from sklearn.isotonic import IsotonicRegression"
    if anchor not in txt:
        # fallback: insert after sklearn.ensemble import
        anchor = "from sklearn.ensemble import HistGradientBoostingClassifier"
        if anchor not in txt:
            # last resort: insert after timezone import
            anchor = "from zoneinfo import ZoneInfo"

    src_lines = txt.splitlines()
    out: list[str] = []
    inserted = False
    for line in src_lines:
        out.append(line)
        if (not inserted) and (line.strip() == anchor.strip()):
            out.append("")
            if need_lr:
                out.append("from sklearn.linear_model import LogisticRegression")
            if need_pipe:
                out.append("from sklearn.pipeline import Pipeline")
            if need_scaler:
                out.append("from sklearn.preprocessing import StandardScaler")
            inserted = True
            changed = True

    if not inserted:
        out.append("")
        if need_lr:
            out.append("from sklearn.linear_model import LogisticRegression")
        if need_pipe:
            out.append("from sklearn.pipeline import Pipeline")
        if need_scaler:
            out.append("from sklearn.preprocessing import StandardScaler")
        changed = True

    return "\n".join(out) + ("\n" if txt.endswith("\n") else ""), changed


def ensure_public_constants(txt: str) -> tuple[str, bool]:
    # If they already exist, do nothing.
    if re.search(r"(?m)^\s*GATE_VERSION\s*=", txt) and re.search(r"(?m)^\s*META_FEATURES\s*=", txt):
        return txt, False

    m = re.search(r"(?m)^\s*def\s+_truthy\s*\(", txt)
    if not m:
        return txt, False

    insert = [
        "",
        "# Public constants used by observe_signal_topk_perday.py",
        'GATE_VERSION = "P2.2-meta+P9-cp+P15-meta_iso"',
        "META_FEATURES = [",
        '    "dow_sin", "dow_cos", "min_sin", "min_cos",',
        '    "proba_up", "conf", "vol", "bb", "atr", "iso_score",',
        "]",
        "",
    ]
    pos = m.start()
    new_txt = txt[:pos] + "\n".join(insert) + txt[pos:]
    return new_txt, True


def patch_signature_and_compat_block(txt: str) -> tuple[str, bool]:
    """
    Replace train_base_cal_iso_meta signature to accept meta_model_type and unknown kwargs.

    Also inject a compat mapping block after the docstring if missing.
    """
    changed = False
    src_lines = txt.splitlines()

    # Find def line
    def_i = None
    for i, l in enumerate(src_lines):
        if l.startswith("def train_base_cal_iso_meta"):
            def_i = i
            break
    if def_i is None:
        return txt, False

    # Find end of signature (line containing ') ->' and ending with ':')
    end_i = None
    ret_type = None
    for j in range(def_i, min(def_i + 60, len(src_lines))):
        if ") ->" in src_lines[j] and src_lines[j].rstrip().endswith(":"):
            end_i = j
            m = re.search(r"\)\s*->\s*(.*)\s*:\s*$", src_lines[j].strip())
            if m:
                ret_type = m.group(1).strip()
            break
    if end_i is None:
        return txt, False
    if ret_type is None:
        ret_type = "tuple[CalibratedClassifierCV, Optional[IsotonicRegression], Optional[MetaPack]]"

    sig_block = "\n".join(src_lines[def_i : end_i + 1])
    if not ("meta_model_type" in sig_block and "**" in sig_block):
        new_sig = [
            "def train_base_cal_iso_meta(",
            "    train_df: pd.DataFrame,",
            "    feat_cols: list[str],",
            "    tz: ZoneInfo,",
            "    meta_model_type: Optional[str] = None,",
            "    *,",
            "    base_model: Optional[str] = None,",
            "    meta_model: Optional[str] = None,",
            "    base_model_type: Optional[str] = None,",
            "    **_compat_kwargs: Any,",
            f") -> {ret_type}:",
        ]
        src_lines[def_i : end_i + 1] = new_sig
        changed = True

    # Re-find signature end after potential replacement
    def_i2 = None
    for i, l in enumerate(src_lines):
        if l.startswith("def train_base_cal_iso_meta"):
            def_i2 = i
            break
    if def_i2 is None:
        return "\n".join(src_lines) + ("\n" if txt.endswith("\n") else ""), changed

    end_i2 = None
    for j in range(def_i2, min(def_i2 + 60, len(src_lines))):
        if ") ->" in src_lines[j] and src_lines[j].rstrip().endswith(":"):
            end_i2 = j
            break
    if end_i2 is None:
        return "\n".join(src_lines) + ("\n" if txt.endswith("\n") else ""), changed

    func_slice = "\n".join(src_lines[end_i2 + 1 : min(end_i2 + 160, len(src_lines))])
    if ("Compat: older callers" in func_slice) or ("meta_model_type" in func_slice) or ("base_model_type" in func_slice):
        return "\n".join(src_lines) + ("\n" if txt.endswith("\n") else ""), changed

    # Locate docstring start
    doc_start = None
    doc_quote = None
    for j in range(end_i2 + 1, min(end_i2 + 25, len(src_lines))):
        s = src_lines[j].lstrip()
        if s.startswith('"""') or s.startswith("'''"):
            doc_start = j
            doc_quote = s[:3]
            break

    if doc_start is None:
        insert_at = end_i2 + 1
    else:
        # Same-line docstring?
        if src_lines[doc_start].count(doc_quote) >= 2:
            insert_at = doc_start + 1
        else:
            insert_at = None
            for k in range(doc_start + 1, min(doc_start + 80, len(src_lines))):
                if doc_quote in src_lines[k]:
                    insert_at = k + 1
                    break
            if insert_at is None:
                insert_at = doc_start + 1

    compat_block = [
        "",
        "    # Compat: older callers pass meta_model_type/base_model_type (and we ignore unknown kwargs).",
        "    if meta_model is None:",
        "        meta_model = meta_model_type",
        "    if base_model is None:",
        "        base_model = base_model_type",
        "    if meta_model is None:",
        "        meta_model = \"hgb\"",
        "    if base_model is None:",
        "        base_model = \"hgb\"",
        "",
        "    meta_model = str(meta_model).strip().lower()",
        "    base_model = str(base_model).strip().lower()",
        "    if meta_model in (\"lr\", \"logistic\", \"logisticregression\"):",
        "        meta_model = \"logreg\"",
        "",
    ]
    src_lines[insert_at:insert_at] = compat_block
    changed = True

    return "\n".join(src_lines) + ("\n" if txt.endswith("\n") else ""), changed


def patch_meta_model_support(txt: str) -> tuple[str, bool]:
    """Replace the hard-coded "meta_model suportado: hgb" block with hgb/logreg support."""
    if ("meta_model == \"logreg\"" in txt) and ("LogisticRegression" in txt) and ("Pipeline(" in txt):
        return txt, False

    pattern = re.compile(
        r"(?P<indent>^[ \t]+)if\s+meta_model\.lower\(\)\s+not\s+in\s+\(\s*[\"\']hgb[\"\']\s*,?\s*\)\s*:\s*\n"        r"(?P=indent)[ \t]+raise\s+ValueError\([^\n]*\)\s*\n"        r"(?P=indent)mm\s*=\s*HistGradientBoostingClassifier\([^\n]*\)\s*\n",
        re.M,
    )

    repl = (
        r"\g<indent>if meta_model not in (\"hgb\", \"logreg\"):\n"
        r"\g<indent>    meta_model = \"hgb\"\n\n"
        r"\g<indent>if meta_model == \"logreg\":\n"
        r"\g<indent>    mm = Pipeline(\n"
        r"\g<indent>        [\n"
        r"\g<indent>            (\"scaler\", StandardScaler()),\n"
        r"\g<indent>            (\"lr\", LogisticRegression(max_iter=500, solver=\"lbfgs\")),\n"
        r"\g<indent>        ]\n"
        r"\g<indent>    )\n"
        r"\g<indent>else:\n"
        r"\g<indent>    mm = HistGradientBoostingClassifier(random_state=42)\n"
    )

    new_txt, n = pattern.subn(repl, txt, count=1)
    if n == 0:
        if "meta_model suportado: hgb" in txt:
            return txt.replace("meta_model suportado: hgb", "meta_model suportado: hgb|logreg"), True
        return txt, False
    return new_txt, True


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    target = root / "src" / "natbin" / "gate_meta.py"
    if not target.exists():
        print(f"[P22] ERROR: gate_meta.py not found at: {target}")
        sys.exit(2)

    txt0 = target.read_text(encoding="utf-8", errors="replace")
    txt = txt0

    any_change = False

    txt, ch = ensure_imports(txt)
    any_change = any_change or ch

    txt, ch = ensure_public_constants(txt)
    any_change = any_change or ch

    txt, ch = patch_signature_and_compat_block(txt)
    any_change = any_change or ch

    txt, ch = patch_meta_model_support(txt)
    any_change = any_change or ch

    if not any_change:
        print("[P22] No changes needed (already patched).")
        py_compile.compile(str(target), doraise=True)
        print("[P22] OK (compile).")
        return

    b = backup(target)
    target.write_text(txt, encoding="utf-8")
    py_compile.compile(str(target), doraise=True)

    print(f"[P22] OK {target}")
    print(f"[P22] backup: {b}")
    print("[P22] Suggested smoke-test:")
    print("  - pwsh -ExecutionPolicy Bypass -File .\\scripts\\scheduler\\observe_loop_auto.ps1 -Once")
    print("  - python -m natbin.paper_pnl_backtest --gate-mode cp --meta-model hgb (and vary CP_ALPHA)")


if __name__ == "__main__":
    main()
