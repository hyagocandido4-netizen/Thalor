from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
import py_compile


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    raise SystemExit("Não encontrei .git. Rode dentro do repo (ex: C:\\Users\\hyago\\Documents\\bot).")


def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def try_compile(p: Path) -> bool:
    try:
        py_compile.compile(str(p), doraise=True)
        return True
    except Exception:
        return False


def find_best_backup(p: Path) -> Path | None:
    """
    Returns the newest backup that compiles (observe_signal_topk_perday.py.bak_YYYY...).
    """
    backups = sorted(
        p.parent.glob(p.name + ".bak_*"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )
    for b in backups:
        if try_compile(b):
            return b
    return None


def restore_from_backup(p: Path) -> Path | None:
    b = find_best_backup(p)
    if b is None:
        return None
    shutil.copy2(b, p)
    return b


def ensure_numpy_import(txt: str) -> str:
    if re.search(r"^\s*import\s+numpy\s+as\s+np\s*$", txt, flags=re.M):
        return txt
    ins = "import numpy as np\n"
    # try insert after "import os"
    m = re.search(r"^\s*import\s+os\s*$", txt, flags=re.M)
    if m:
        i = txt.find("\n", m.end())
        if i == -1:
            return txt + "\n" + ins
        return txt[: i + 1] + ins + txt[i + 1 :]
    # else after future import
    m2 = re.search(r"^from\s+__future__\s+import\s+.*$", txt, flags=re.M)
    if m2:
        i = txt.find("\n", m2.end())
        if i == -1:
            return txt + "\n" + ins
        return txt[: i + 1] + ins + txt[i + 1 :]
    # else at top
    return ins + txt


def patch_observe(root: Path) -> None:
    target = root / "src" / "natbin" / "observe_signal_topk_perday.py"
    if not target.exists():
        raise SystemExit(f"[P13c] Não achei {target}")

    # If file is broken, restore a compiling backup automatically
    if not try_compile(target):
        b = restore_from_backup(target)
        if b is None or (not try_compile(target)):
            raise SystemExit(
                "[P13c] Seu observe_signal_topk_perday.py está com erro e não achei backup compilável.\n"
                "Rode (recomendado): git restore src\\natbin\\observe_signal_topk_perday.py\n"
                "Ou me mande o trecho em torno do erro."
            )
        print(f"[P13c] Restored from backup: {b}")

    txt = target.read_text(encoding="utf-8", errors="replace")
    marker = "# --- P13c: REGIME_MODE soft/off (mask_gate) ---"
    if marker in txt:
        print("[P13c] observe_signal_topk_perday.py: já patchado.")
        return

    txt = ensure_numpy_import(txt)

    # Find cand line: cand = mask & ...  OR cand = (mask & ...
    pat_cand = re.compile(
        r"^(?P<indent>[ \t]*)cand\s*=\s*(?P<prefix>\(?\s*)mask(?P<rest>\s*&.*)$",
        re.M,
    )
    m = pat_cand.search(txt)
    if not m:
        raise SystemExit(
            "[P13c] Não encontrei a linha 'cand = mask & ...' no observe_signal_topk_perday.py.\n"
            "Rode: findstr /n /i \"cand =\" src\\natbin\\observe_signal_topk_perday.py"
        )

    indent = m.group("indent")
    prefix = m.group("prefix")
    rest = m.group("rest")

    insert_block = (
        f"{indent}{marker}\n"
        f'{indent}_rm = os.getenv("REGIME_MODE", "hard").strip().lower()\n'
        f'{indent}if _rm not in ("hard","soft","off"):\n'
        f'{indent}    _rm = "hard"\n'
        f"{indent}mask_gate = mask if _rm == \"hard\" else np.ones(len(mask), dtype=bool)\n"
        f"{indent}# --- /P13c ---\n"
    )

    new_cand_line = f"{indent}cand = {prefix}mask_gate{rest}\n"

    # Replace the whole cand line with (insert_block + new_cand_line)
    start = m.start()
    line_start = txt.rfind("\n", 0, start) + 1
    next_nl = txt.find("\n", start)
    if next_nl == -1:
        next_nl = len(txt)
        tail = ""
    else:
        tail = txt[next_nl + 1 :]
    txt = txt[:line_start] + insert_block + new_cand_line + tail

    # Patch regime_block checks (now_i) to be hard-only
    def repl_rb(mo: re.Match) -> str:
        ind = mo.group("indent")
        return (
            f'{ind}if (os.getenv("REGIME_MODE","hard").strip().lower() == "hard") '
            f'and (not bool(mask[now_i])):'
        )

    txt, _ = re.subn(
        r"^(?P<indent>[ \t]*)if\s+not\s+bool\(\s*mask\[\s*now_i\s*\]\s*\)\s*:\s*$",
        repl_rb,
        txt,
        flags=re.M,
    )
    txt, _ = re.subn(
        r"^(?P<indent>[ \t]*)if\s+not\s+mask\[\s*now_i\s*\]\s*:\s*$",
        repl_rb,
        txt,
        flags=re.M,
    )

    # Patch mask empty checks, if present
    def repl_any(mo: re.Match) -> str:
        ind = mo.group("indent")
        expr = mo.group("expr")
        return (
            f'{ind}if (os.getenv("REGIME_MODE","hard").strip().lower() == "hard") and ({expr}):'
        )

    txt, _ = re.subn(
        r"^(?P<indent>[ \t]*)if\s+(?P<expr>not\s+mask\.any\(\))\s*:\s*$",
        repl_any,
        txt,
        flags=re.M,
    )
    txt, _ = re.subn(
        r"^(?P<indent>[ \t]*)if\s+(?P<expr>mask\.sum\(\)\s*==\s*0)\s*:\s*$",
        repl_any,
        txt,
        flags=re.M,
    )

    bkp = backup(target)
    target.write_text(txt, encoding="utf-8")

    try:
        py_compile.compile(str(target), doraise=True)
    except Exception as e:
        shutil.copy2(bkp, target)
        raise SystemExit(f"[P13c] ERRO: patch quebrou sintaxe. Rollback feito. Detalhe: {e}")

    print(f"[P13c] OK observe patched: {target}")
    print(f"[P13c] Backup: {bkp}")


def patch_auto_volume(root: Path) -> None:
    target = root / "src" / "natbin" / "auto_volume.py"
    if not target.exists():
        raise SystemExit(f"[P13c] Não achei {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")
    marker = "# --- P13c: regime_mode in recommended ---"
    if marker in txt:
        print("[P13c] auto_volume.py: já patchado.")
        return

    m = re.search(r'^(?P<indent>[ \t]*)"cpreg_slot2_mult"\s*:\s*.*,\s*$', txt, flags=re.M)
    if not m:
        raise SystemExit('[P13c] Não encontrei a linha "cpreg_slot2_mult" no auto_volume.py')

    indent = m.group("indent")
    insert = (
        f"{indent}{marker}\n"
        f'{indent}"regime_mode": (os.getenv("VOL_REGIME_MODE_STUCK","soft") '
        f'if any("bootstrap" in n for n in notes) else os.getenv("VOL_REGIME_MODE_NORMAL","hard")).strip().lower(),\n'
        f"{indent}# --- /P13c ---\n"
    )

    line_end = txt.find("\n", m.end())
    if line_end == -1:
        txt2 = txt + "\n" + insert
    else:
        txt2 = txt[: line_end + 1] + insert + txt[line_end + 1 :]

    bkp = backup(target)
    target.write_text(txt2, encoding="utf-8")

    try:
        py_compile.compile(str(target), doraise=True)
    except Exception as e:
        shutil.copy2(bkp, target)
        raise SystemExit(f"[P13c] ERRO auto_volume: rollback feito. Detalhe: {e}")

    print(f"[P13c] OK auto_volume patched: {target}")
    print(f"[P13c] Backup: {bkp}")


def patch_wrapper(root: Path) -> None:
    ps1 = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        raise SystemExit(f"[P13c] Não achei {ps1}")

    txt = ps1.read_text(encoding="utf-8", errors="replace")
    marker = "# P13c: apply REGIME_MODE from auto_volume"
    if marker in txt:
        print("[P13c] observe_loop_auto.ps1: já patchado.")
        return

    lines = txt.splitlines(True)

    inserted = False
    for i, line in enumerate(lines):
        if "$rec.cpreg_slot2_mult" in line and "$env:CPREG_SLOT2_MULT" in line:
            lines.insert(
                i + 1,
                marker + "\n" + 'if ($rec.regime_mode -ne $null) { $env:REGIME_MODE = [string]$rec.regime_mode }\n',
            )
            inserted = True
            break
    if not inserted:
        raise SystemExit("[P13c] Não achei a linha do $rec.cpreg_slot2_mult para inserir REGIME_MODE.")

    for i, line in enumerate(lines):
        if "$pr.cpreg_slot2_mult" in line and "$env:CPREG_SLOT2_MULT" in line:
            lines.insert(i + 1, 'if ($pr.regime_mode -ne $null) { $env:REGIME_MODE = [string]$pr.regime_mode }\n')
            break

    bkp = backup(ps1)
    ps1.write_text("".join(lines), encoding="utf-8")
    print(f"[P13c] OK wrapper patched: {ps1}")
    print(f"[P13c] Backup: {bkp}")


def main() -> None:
    root = repo_root()
    patch_observe(root)
    patch_auto_volume(root)
    patch_wrapper(root)
    print("[P13c] Done.")


if __name__ == "__main__":
    main()