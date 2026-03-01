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


def main() -> None:
    root = repo_root()
    p = root / "src" / "natbin" / "auto_volume.py"
    if not p.exists():
        raise SystemExit(f"[P12g] Não achei {p}")

    txt = p.read_text(encoding="utf-8", errors="replace")
    marker = "# --- P12g: enforce P14 safe guardrails ---"
    if marker in txt:
        print("[P12g] auto_volume.py: já patchado.")
        return

    # 1) inserir enforce block antes do '# hard clamps coerentes'
    m = re.search(r"^(?P<indent>[ \t]*)# hard clamps coerentes\s*$", txt, flags=re.M)
    if not m:
        raise SystemExit("[P12g] Não encontrei '# hard clamps coerentes' para inserir enforce block.")

    ind = m.group("indent")
    block = (
        f"{ind}{marker}\n"
        f'{ind}enforce_p14 = _truthy(os.getenv("VOL_ENFORCE_P14", "1"))\n'
        f'{ind}safe_thr_min = _f(os.getenv("VOL_SAFE_THR_MIN"), 0.10)\n'
        f'{ind}safe_alpha_max = _f(os.getenv("VOL_SAFE_ALPHA_MAX"), 0.08)\n'
        f"{ind}p14_enforced = False\n"
        f"{ind}if enforce_p14:\n"
        f"{ind}    p14_enforced = True\n"
        f"{ind}    thr_min = max(thr_min, safe_thr_min)\n"
        f"{ind}    boot_thr_floor = max(boot_thr_floor, safe_thr_min)\n"
        f"{ind}    stuck_thr_floor = max(stuck_thr_floor, safe_thr_min)\n"
        f"{ind}    thr_max = max(thr_max, thr_min)\n"
        f"{ind}    a_max = min(a_max, safe_alpha_max)\n"
        f"{ind}    boot_alpha_end_ceil = min(boot_alpha_end_ceil, safe_alpha_max)\n"
        f"{ind}# --- /P12g ---\n\n"
    )
    txt = txt[: m.start()] + block + txt[m.start() :]

    # 2) inserir info auditável dentro do dict "p12f": { ... }
    m2 = re.search(r'^(?P<indent>[ \t]*)"p12f"\s*:\s*\{\s*$', txt, flags=re.M)
    if not m2:
        raise SystemExit('[P12g] Não encontrei a linha \'"p12f": {\' para inserir audit fields.')

    base = m2.group("indent")
    keyind = base + "    "
    insert_keys = (
        f'{keyind}"enforce_p14": bool(enforce_p14),\n'
        f'{keyind}"p14_enforced": bool(p14_enforced),\n'
        f'{keyind}"safe_thr_min": float(safe_thr_min),\n'
        f'{keyind}"safe_alpha_max": float(safe_alpha_max),\n'
    )

    line_end = txt.find("\n", m2.end())
    if line_end == -1:
        txt = txt + "\n" + insert_keys
    else:
        txt = txt[: line_end + 1] + insert_keys + txt[line_end + 1 :]

    bkp = backup(p)
    p.write_text(txt, encoding="utf-8")

    py_compile.compile(str(p), doraise=True)
    print(f"[P12g] OK patched: {p}")
    print(f"[P12g] Backup: {bkp}")


if __name__ == "__main__":
    main()