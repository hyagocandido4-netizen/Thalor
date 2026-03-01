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
    raise SystemExit("Não encontrei .git. Rode dentro do diretório do repo (ex: C:\\Users\\hyago\\Documents\\bot).")


def backup(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = path.with_suffix(path.suffix + f".bak_{ts}")
    shutil.copy2(path, b)
    return b


def main() -> None:
    root = repo_root()
    target = root / "src" / "natbin" / "observe_signal_topk_perday.py"
    if not target.exists():
        raise SystemExit(f"Arquivo não encontrado: {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")

    marker = "# --- P8c: THRESHOLD env override (fixed order) ---"
    if marker in txt:
        print("[P8c] Já aplicado (marker encontrado).")
        return

    # 1) Remove bloco antigo P8b (se existir)
    txt2, _ = re.subn(
        r"\n[ \t]*# --- P8b: THRESHOLD env override ---.*?\n[ \t]*# --- /P8b ---\n",
        "\n",
        txt,
        flags=re.S,
    )

    # 2) Encontra a linha de definição do thr do config
    m = re.search(
        r'^(?P<indent>[ \t]*)thr\s*=\s*float\(best\.get\((["\'])threshold\2,\s*([^\)]*)\)\)\s*$',
        txt2,
        flags=re.M,
    )
    if not m:
        raise SystemExit(
            "[P8c] Não encontrei a linha 'thr = float(best.get(\"threshold\", ...))'. "
            "Me mande esse trecho do arquivo que eu adapto o patch."
        )

    indent = m.group("indent")

    insert = (
        "\n"
        f"{indent}{marker}\n"
        f'{indent}_thr_env = os.getenv("THRESHOLD", "").strip()\n'
        f"{indent}if _thr_env:\n"
        f"{indent}    try:\n"
        f"{indent}        thr = float(_thr_env)\n"
        f"{indent}    except Exception:\n"
        f"{indent}        pass\n"
        f"{indent}# --- /P8c ---\n"
    )

    # 3) Injeta o bloco logo após a linha do thr
    txt3 = txt2[: m.end()] + insert + txt2[m.end() :]

    bkp = backup(target)
    target.write_text(txt3, encoding="utf-8")

    # 4) Sanity check: compila o arquivo para garantir que não quebrou sintaxe
    try:
        py_compile.compile(str(target), doraise=True)
    except Exception as e:
        # rollback
        shutil.copy2(bkp, target)
        raise SystemExit(f"[P8c] ERRO: patch quebrou sintaxe. Rollback feito. Detalhe: {e}")

    print(f"[P8c] OK: patched {target}")
    print(f"[P8c] Backup: {bkp}")


if __name__ == "__main__":
    main()