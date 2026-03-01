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
    raise SystemExit("Não encontrei .git. Rode dentro do repo (C:\\Users\\hyago\\Documents\\bot).")


def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def patch_observe_regime_mode(root: Path) -> None:
    target = root / "src" / "natbin" / "observe_signal_topk_perday.py"
    if not target.exists():
        raise SystemExit(f"[P13] Não achei {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")
    marker = "# --- P13: REGIME_MODE soft/off support ---"
    if marker in txt:
        print("[P13] observe_signal_topk_perday.py: já patchado.")
        return

    # garante import os (na prática você já tem, mas fica safe)
    if re.search(r"^\s*import\s+os\s*$", txt, flags=re.M) is None and "os.getenv" in txt:
        # os já deve estar importado. se não estiver e estiver usando getenv, é bug antigo.
        pass

    # aplica substituições no if que bloqueia regime
    # (somente sintaxe de linha inteira para não estragar outras condições)
    sub_count = 0

    def _sub(pat: str, repl: str) -> None:
        nonlocal txt, sub_count
        txt2, n = re.subn(pat, repl, txt, flags=re.M)
        txt = txt2
        sub_count += n

    # if not regime_ok:
    _sub(
        r'^(\s*)if\s+not\s+regime_ok\s*:\s*$',
        r'\1if (not regime_ok) and (os.getenv("REGIME_MODE","hard").strip().lower() == "hard"):',
    )
    # if regime_ok == 0:  / if (regime_ok == 0):
    _sub(
        r'^(\s*)if\s+\(?\s*regime_ok\s*==\s*0\s*\)?\s*:\s*$',
        r'\1if (regime_ok == 0) and (os.getenv("REGIME_MODE","hard").strip().lower() == "hard"):',
    )

    if sub_count == 0:
        raise SystemExit(
            "[P13] Não achei nenhum if de regime_ok para patchar. "
            "Me mande o trecho onde aparece 'regime_block' no observe_signal_topk_perday.py."
        )

    # injeta marker no topo (auditável)
    txt = marker + "\n" + txt

    bkp = backup(target)
    target.write_text(txt, encoding="utf-8")
    py_compile.compile(str(target), doraise=True)
    print(f"[P13] Patched observe_signal_topk_perday.py (backup: {bkp})")


def patch_auto_volume_recommend_regime_mode(root: Path) -> None:
    target = root / "src" / "natbin" / "auto_volume.py"
    if not target.exists():
        raise SystemExit(f"[P13] Não achei {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")
    marker = "# --- P13: recommend REGIME_MODE when stuck ---"
    if marker in txt:
        print("[P13] auto_volume.py: já patchado.")
        return

    # 1) injeta cálculo regime_mode antes do "rec = {"
    m = re.search(r"^(?P<indent>[ \t]*)rec\s*=\s*\{\s*$", txt, flags=re.M)
    if not m:
        raise SystemExit("[P13] Não encontrei 'rec = {' em auto_volume.py")

    indent = m.group("indent")
    insert_block = "\n".join(
        [
            f"{indent}{marker}",
            f"{indent}_regime_mode_normal = os.getenv('VOL_REGIME_MODE_NORMAL','hard').strip().lower()",
            f"{indent}_regime_mode_stuck  = os.getenv('VOL_REGIME_MODE_STUCK','soft').strip().lower()",
            f"{indent}regime_mode = _regime_mode_normal",
            f"{indent}if any('bootstrap_stuck' in n for n in notes):",
            f"{indent}    regime_mode = _regime_mode_stuck",
            f"{indent}# --- /P13 ---",
            "",
        ]
    )

    txt = txt[: m.start()] + insert_block + txt[m.start() :]

    # 2) adiciona "regime_mode" dentro do dict rec (logo após a linha rec = { )
    # Faz isso substituindo só a primeira ocorrência.
    def add_regime_mode_line(match: re.Match) -> str:
        ind = match.group("indent")
        return f"{ind}rec = {{\n{ind}    \"regime_mode\": regime_mode,"

    txt, n2 = re.subn(
        r"^(?P<indent>[ \t]*)rec\s*=\s*\{\s*$",
        add_regime_mode_line,
        txt,
        count=1,
        flags=re.M,
    )
    if n2 == 0:
        raise SystemExit("[P13] Falhou injetar regime_mode no rec dict em auto_volume.py")

    bkp = backup(target)
    target.write_text(txt, encoding="utf-8")
    py_compile.compile(str(target), doraise=True)
    print(f"[P13] Patched auto_volume.py (backup: {bkp})")


def patch_wrapper_apply_regime_mode(root: Path) -> None:
    ps1 = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        raise SystemExit(f"[P13] Não achei {ps1}")

    txt = ps1.read_text(encoding="utf-8", errors="replace")
    marker = "# P13: apply REGIME_MODE from auto_volume"
    if marker in txt:
        print("[P13] observe_loop_auto.ps1: já patchado.")
        return

    # aplica regime_mode após aplicar slot2_mult (recommended)
    m = re.search(r"^\s*if\s*\(\$rec\.cpreg_slot2_mult.*\)\s*\{.*\}\s*$", txt, flags=re.M)
    if not m:
        raise SystemExit("[P13] Não encontrei linha de CPREG_SLOT2_MULT no wrapper para inserir REGIME_MODE.")

    insert = "\n" + "\n".join(
        [
            marker,
            "if ($rec.regime_mode -ne $null) { $env:REGIME_MODE = [string]$rec.regime_mode }",
            "",
        ]
    )
    # insere após a linha encontrada
    line_end = txt.find("\n", m.end())
    if line_end == -1:
        line_end = m.end()
    txt = txt[: line_end + 1] + insert + txt[line_end + 1 :]

    # também seed do estado anterior (opcional) se existir bloco $pr.cpreg_slot2_mult
    m2 = re.search(r"^\s*if\s*\(\$pr\.cpreg_slot2_mult.*\)\s*\{.*\}\s*$", txt, flags=re.M)
    if m2:
        insert2 = "if ($pr.regime_mode -ne $null) { $env:REGIME_MODE = [string]$pr.regime_mode }\n"
        line_end2 = txt.find("\n", m2.end())
        if line_end2 == -1:
            line_end2 = m2.end()
        txt = txt[: line_end2 + 1] + insert2 + txt[line_end2 + 1 :]

    bkp = backup(ps1)
    ps1.write_text(txt, encoding="utf-8")
    print(f"[P13] Patched observe_loop_auto.ps1 (backup: {bkp})")


def main() -> None:
    root = repo_root()
    patch_observe_regime_mode(root)
    patch_auto_volume_recommend_regime_mode(root)
    patch_wrapper_apply_regime_mode(root)
    print("[P13] Done.")


if __name__ == "__main__":
    main()