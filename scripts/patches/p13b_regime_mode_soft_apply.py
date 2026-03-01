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


def patch_observe(root: Path) -> None:
    target = root / "src" / "natbin" / "observe_signal_topk_perday.py"
    if not target.exists():
        raise SystemExit(f"[P13b] Não achei {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")
    marker = "# --- P13b: REGIME_MODE soft/off (mask_gate) ---"
    if marker in txt:
        print("[P13b] observe_signal_topk_perday.py: já patchado.")
        return

    # 1) trocar cand = mask & (...) -> mask_gate
    txt2, n_cand = re.subn(
        r"(\bcand\s*=\s*)mask(\s*&\s*\()",
        r"\1mask_gate\2",
        txt,
        count=1,
        flags=re.M,
    )
    if n_cand == 0:
        # fallback: pega qualquer "cand = mask &"
        txt2, n_cand2 = re.subn(
            r"(\bcand\s*=\s*)mask(\s*&)",
            r"\1mask_gate\2",
            txt,
            count=1,
            flags=re.M,
        )
        if n_cand2 == 0:
            raise SystemExit("[P13b] Não encontrei a linha 'cand = mask & ...' para substituir.")

    txt = txt2

    # 2) inserir regime_mode/mask_gate logo após a definição do mask
    m = re.search(r"^[ \t]*mask\s*=\s*make_regime_mask\(", txt, flags=re.M)
    if not m:
        raise SystemExit("[P13b] Não achei a definição do 'mask = make_regime_mask(...)'.")

    line_start = txt.rfind("\n", 0, m.start()) + 1
    indent = re.match(r"[ \t]*", txt[line_start:m.start()]).group(0)
    line_end = txt.find("\n", m.start())
    if line_end == -1:
        line_end = len(txt)

    insert_block = (
        f"{indent}{marker}\n"
        f'{indent}regime_mode = os.getenv("REGIME_MODE", "hard").strip().lower()\n'
        f'{indent}if regime_mode not in ("hard", "soft", "off"):\n'
        f'{indent}    regime_mode = "hard"\n'
        f"{indent}mask_gate = mask if regime_mode == \"hard\" else np.ones(len(df_day), dtype=bool)\n"
        f"{indent}# --- /P13b ---\n"
    )

    txt = txt[: line_end + 1] + insert_block + txt[line_end + 1 :]

    # 3) regime_block só quando hard
    # padrões comuns:
    #   if not bool(mask[now_i]):
    #   if not mask[now_i]:
    pat1 = r"^(?P<ind>[ \t]*)if\s+not\s+bool\(\s*mask\[\s*now_i\s*\]\s*\)\s*:\s*$"
    pat2 = r"^(?P<ind>[ \t]*)if\s+not\s+mask\[\s*now_i\s*\]\s*:\s*$"

    def repl(mo: re.Match) -> str:
        ind = mo.group("ind")
        return f'{ind}if (regime_mode == "hard") and (not bool(mask[now_i])):'

    txt_new, n1 = re.subn(pat1, repl, txt, count=1, flags=re.M)
    if n1 == 0:
        txt_new, n2 = re.subn(pat2, repl, txt, count=1, flags=re.M)
        if n2 == 0:
            # fallback: se estiver numa linha "if not bool(mask[now_i]): reason=..."
            txt_new, n3 = re.subn(
                r"if\s+not\s+bool\(\s*mask\[\s*now_i\s*\]\s*\)\s*:",
                'if (regime_mode == "hard") and (not bool(mask[now_i])):',
                txt,
                count=1,
            )
            if n3 == 0:
                raise SystemExit("[P13b] Não consegui achar o if do regime_block (mask[now_i]).")

    txt = txt_new

    bkp = backup(target)
    target.write_text(txt, encoding="utf-8")
    py_compile.compile(str(target), doraise=True)
    print(f"[P13b] OK observe patched: {target}")
    print(f"[P13b] Backup: {bkp}")


def patch_auto_volume(root: Path) -> None:
    target = root / "src" / "natbin" / "auto_volume.py"
    if not target.exists():
        raise SystemExit(f"[P13b] Não achei {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")
    marker = "# --- P13b: regime_mode in recommended ---"
    if marker in txt:
        print("[P13b] auto_volume.py: já patchado.")
        return

    # Insere dentro do dict rec, depois de cpreg_slot2_mult
    pos_rec = txt.find("rec = {")
    if pos_rec == -1:
        raise SystemExit("[P13b] Não encontrei 'rec = {' em auto_volume.py")

    pos_key = txt.find('"cpreg_slot2_mult"', pos_rec)
    if pos_key == -1:
        raise SystemExit('[P13b] Não encontrei "cpreg_slot2_mult" dentro do rec dict.')

    line_start = txt.rfind("\n", 0, pos_key) + 1
    line_end = txt.find("\n", pos_key)
    if line_end == -1:
        line_end = len(txt)

    indent = re.match(r"[ \t]*", txt[line_start:]).group(0)

    insert = (
        f"{indent}{marker}\n"
        f'{indent}"regime_mode": (os.getenv("VOL_REGIME_MODE_STUCK","soft") if any("bootstrap" in n for n in notes) '
        f'else os.getenv("VOL_REGIME_MODE_NORMAL","hard")).strip().lower(),\n'
    )

    txt = txt[: line_end + 1] + insert + txt[line_end + 1 :]

    bkp = backup(target)
    target.write_text(txt, encoding="utf-8")
    py_compile.compile(str(target), doraise=True)
    print(f"[P13b] OK auto_volume patched: {target}")
    print(f"[P13b] Backup: {bkp}")


def patch_wrapper(root: Path) -> None:
    ps1 = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        raise SystemExit(f"[P13b] Não achei {ps1}")

    txt = ps1.read_text(encoding="utf-8", errors="replace")
    marker = "# P13b: apply REGIME_MODE from auto_volume"
    if marker in txt:
        print("[P13b] observe_loop_auto.ps1: já patchado.")
        return

    lines = txt.splitlines(True)

    # inserir após aplicar CPREG_SLOT2_MULT (recommended)
    inserted = False
    for i, line in enumerate(lines):
        if "$rec.cpreg_slot2_mult" in line and "$env:CPREG_SLOT2_MULT" in line:
            ins = (
                marker + "\n"
                + 'if ($rec.regime_mode -ne $null) { $env:REGIME_MODE = [string]$rec.regime_mode }\n'
            )
            lines.insert(i + 1, ins)
            inserted = True
            break
    if not inserted:
        raise SystemExit("[P13b] Não achei a linha do $rec.cpreg_slot2_mult para inserir REGIME_MODE.")

    # inserir também no seed do auto_params.json (previous recommended), se existir
    for i, line in enumerate(lines):
        if "$pr.cpreg_slot2_mult" in line and "$env:CPREG_SLOT2_MULT" in line:
            lines.insert(i + 1, 'if ($pr.regime_mode -ne $null) { $env:REGIME_MODE = [string]$pr.regime_mode }\n')
            break

    bkp = backup(ps1)
    ps1.write_text("".join(lines), encoding="utf-8")
    print(f"[P13b] OK wrapper patched: {ps1}")
    print(f"[P13b] Backup: {bkp}")


def main() -> None:
    root = repo_root()
    patch_observe(root)
    patch_auto_volume(root)
    patch_wrapper(root)
    print("[P13b] Done.")


if __name__ == "__main__":
    main()