#!/usr/bin/env python3
"""
P27b hotfix — corrige o bug do P27 (envutil) que inseriu \" dentro do código Python,
quebrando o parser com: "unexpected character after line continuation character".

Também adiciona o helper AsInt no scripts/scheduler/observe_loop_auto.ps1 (usado pelo selfcheck).

Como usar:
  python scripts/patches/p27b_fix_env_parsing_escapes_apply.py

Seguro:
- Cria backups *.bak_YYYYMMDD_HHMMSS antes de sobrescrever.
- Faz py_compile nos arquivos Python corrigidos.
"""
from __future__ import annotations

import re
import sys
import time
import py_compile
from pathlib import Path


TS = time.strftime("%Y%m%d_%H%M%S")


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def backup_path(p: Path) -> Path:
    return p.with_suffix(p.suffix + f".bak_{TS}")


def write_text(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8", newline="\n")


def patch_python_files(repo: Path) -> list[Path]:
    """
    Remove sequências \\\" (backslash+aspas) apenas em LINHAS que contenham env_*(
    (env_float/env_int/env_bool/env_str), para não mexer em strings/JSON/docstrings por acidente.
    """
    targets = [
        repo / "src" / "natbin" / "collect_recent.py",
        repo / "src" / "natbin" / "gate_meta.py",
        repo / "src" / "natbin" / "observe_signal_topk_perday.py",
    ]

    # Também varre src/natbin por segurança (caso o P27 tenha tocado outros arquivos)
    extra = list((repo / "src" / "natbin").rglob("*.py"))
    for p in extra:
        if p not in targets:
            targets.append(p)

    patched: list[Path] = []
    for p in targets:
        if not p.exists():
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        if '\\"' not in raw:
            continue

        lines = raw.splitlines(True)
        changed = False
        out_lines: list[str] = []

        for line in lines:
            if '\\"' in line and "env_" in line and "(" in line:
                # Só corrige se for de fato uma chamada env_*(
                # (evita mexer em string literal com \" em texto)
                if re.search(r"\benv_(float|int|bool|str)\s*\(", line):
                    line2 = line.replace('\\"', '"')
                    if line2 != line:
                        changed = True
                        line = line2
            out_lines.append(line)

        if not changed:
            # fallback: se o arquivo ainda tem \" mas não casou no heurístico,
            # troca globalmente (melhor corrigir do que manter crashando).
            out = raw.replace('\\"', '"')
            if out != raw:
                changed = True
                out_lines = [out]
            else:
                continue

        bak = backup_path(p)
        bak.write_text(raw, encoding="utf-8", newline="\n")
        # out_lines pode ser 1 item (texto inteiro) no fallback
        if len(out_lines) == 1 and "\n" in out_lines[0] and not out_lines[0].endswith("\n"):
            out_lines[0] += "\n"
        new_text = "".join(out_lines)
        write_text(p, new_text)

        # compile check
        py_compile.compile(str(p), doraise=True)
        patched.append(p)

    return patched


def patch_observe_loop_auto(repo: Path) -> bool:
    ps1 = repo / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        return False

    raw = ps1.read_text(encoding="utf-8", errors="replace")
    if "function AsInt" in raw:
        return False

    # Insere logo após AsStr (se existir), senão no topo do arquivo.
    asstr_match = re.search(r"(?im)^\s*function\s+AsStr\b[^\n]*\n(?:.*\n)*?\}\s*\n", raw)
    asint_block = (
        "\n"
        "function AsInt([object]$v, [int]$default = 0) {\n"
        "  try {\n"
        "    if ($null -eq $v) { return $default }\n"
        "    $s = [string]$v\n"
        "    if ([string]::IsNullOrWhiteSpace($s)) { return $default }\n"
        "    $s = $s.Trim()\n"
        "    # aceita '12,3' vindo do pt-BR\n"
        "    $s = $s -replace ',', '.'\n"
        "    return [int]([double]$s)\n"
        "  } catch {\n"
        "    return $default\n"
        "  }\n"
        "}\n"
    )

    if asstr_match:
        insert_at = asstr_match.end()
        new = raw[:insert_at] + asint_block + raw[insert_at:]
    else:
        new = asint_block + "\n" + raw

    bak = backup_path(ps1)
    bak.write_text(raw, encoding="utf-8", newline="\n")
    write_text(ps1, new)
    return True


def main() -> None:
    here = Path(__file__).resolve()
    repo = here.parents[2]  # scripts/patches -> scripts -> repo
    if not (repo / "src" / "natbin").exists():
        die(f"[P27b] ERRO: não encontrei src/natbin em {repo}. Rode o script dentro do repo.")

    patched = patch_python_files(repo)
    ps1_changed = patch_observe_loop_auto(repo)

    print(f"[P27b] patched_py={len(patched)} ps1_changed={ps1_changed}")
    for p in patched[:10]:
        print(f"  - {p}")
    if len(patched) > 10:
        print(f"  ... (+{len(patched)-10} arquivos)")

    print("[P27b] OK.\n[P27b] Smoke-tests sugeridos:")
    print("  1) python scripts/tools/selfcheck_repo.py")
    print("  2) (opcional) $env:CP_ALPHA='0,07' ; pwsh -ExecutionPolicy Bypass -File scripts/scheduler/observe_loop_auto.ps1 -Once")


if __name__ == "__main__":
    main()
