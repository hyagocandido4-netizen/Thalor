#!/usr/bin/env python3
"""p15d_autovol_safe_k3_iso_apply.py

Objetivo (P15d): alinhar o *observe_loop_auto* e o *auto_volume* com o novo setup
(meta_iso + K=3, e floors/ceils vindos dos sweeps recentes), evitando que o
controle de volume fique travado no piso antigo (thr>=0.10 e alpha_end<=0.08)
por causa do clamp "VOL_ENFORCE_P14".

O que este patch faz:
1) scripts/scheduler/observe_loop_auto.ps1
   - muda o default do parâmetro -TopK para 3 (assim o observe_loop.ps1 não
     remove TOPK_K e você sai do "sem override")
   - adiciona/atualiza um bloco [P15d] com defaults (META_ISO + guardrails de
     auto-volume) e, principalmente, define VOL_SAFE_THR_MIN / VOL_SAFE_ALPHA_MAX
     para NÃO deixar o clamp antigo te prender em thr>=0.10.

2) src/natbin/auto_volume.py
   - atualiza os defaults internos (caso o ambiente não defina as variáveis)
     para:
       THRESHOLD default ~0.03
       VOL_THR_MIN default 0.02
       VOL_SAFE_THR_MIN default 0.02
       VOL_ALPHA_MAX default 0.12
       VOL_SAFE_ALPHA_MAX default 0.12
       CPREG_ALPHA_END default 0.12

3) config.yaml (opcional, se existir)
   - best.k -> 3
   - best.threshold -> 0.02
   - volume_control.threshold_floor -> 0.02 (se existir)
   - volume_control.threshold_ceiling -> 0.14 (se existir)
   - volume_control.alpha_end_ceiling -> 0.12 (se existir)

Rodar:
  .\.venv\Scripts\python.exe .\scripts\patches\p15d_autovol_safe_k3_iso_apply.py

Depois testar:
  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once

Você deve ver:
  - "TOPK_K override: 3" (ou algo equivalente)
  - Em bootstrap sem trades, o P12 consegue reduzir THRESHOLD abaixo de 0.10
    (ex.: 0.09, 0.08, ... até o piso 0.02)
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
import py_compile


def _now_tag() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def find_repo_root(start_dir: Path) -> Path:
    """Sobe diretórios até achar uma estrutura típica do repo."""
    start_dir = start_dir.resolve()
    for p in [start_dir] + list(start_dir.parents):
        if (p / "src" / "natbin").exists() and (p / "scripts").exists():
            return p
    raise SystemExit(
        "[P15d] Não encontrei o repo root. Rode este script de dentro do repo (ex.: scripts/patches)."
    )


def backup_file(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + f".bak_{_now_tag()}")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def patch_auto_volume_py(text: str) -> tuple[str, int]:
    """Atualiza defaults internos no auto_volume.py (sem depender de env vars)."""

    rules: list[tuple[str, str]] = [
        (r'os\.getenv\("THRESHOLD",\s*"[^"]*"\)', 'os.getenv("THRESHOLD", "0.03")'),
        (r'os\.getenv\("CPREG_ALPHA_END",\s*"[^"]*"\)', 'os.getenv("CPREG_ALPHA_END", "0.12")'),
        (r'os\.getenv\("VOL_THR_MIN",\s*"[^"]*"\)', 'os.getenv("VOL_THR_MIN", "0.02")'),
        (r'os\.getenv\("VOL_ALPHA_MAX",\s*"[^"]*"\)', 'os.getenv("VOL_ALPHA_MAX", "0.12")'),
        (r'os\.getenv\("VOL_SAFE_THR_MIN",\s*"[^"]*"\)', 'os.getenv("VOL_SAFE_THR_MIN", "0.02")'),
        (r'os\.getenv\("VOL_SAFE_ALPHA_MAX",\s*"[^"]*"\)', 'os.getenv("VOL_SAFE_ALPHA_MAX", "0.12")'),
    ]

    out = text
    edits = 0
    for pat, repl in rules:
        out2, n = re.subn(pat, repl, out)
        if n:
            edits += n
            out = out2

    return out, edits


def patch_observe_loop_auto_ps1(text: str) -> tuple[str, int]:
    """Patch do observe_loop_auto.ps1 (TopK default + bloco P15d)."""

    edits = 0

    # 1) Default TopK=3 (apenas a primeira ocorrência)
    out2, n = re.subn(r"\[int\]\$TopK\s*=\s*0", "[int]$TopK = 3", text, count=1)
    if n:
        edits += n
        text = out2

    block = (
        "# [P15d] defaults for meta_iso + K=3 and auto-volume safety (override if already set externally)\n"
        "if (-not $env:META_ISO_ENABLE) { $env:META_ISO_ENABLE = \"1\" }\n"
        "if (-not $env:META_ISO_BLEND)  { $env:META_ISO_BLEND  = \"0.75\" }\n\n"
        "# auto-volume guardrails tuned from sweeps (K=3, payout=0.8)\n"
        "if (-not $env:VOL_THR_MIN)              { $env:VOL_THR_MIN = \"0.02\" }\n"
        "if (-not $env:VOL_THR_MAX)              { $env:VOL_THR_MAX = \"0.14\" }\n"
        "if (-not $env:VOL_ALPHA_MIN)            { $env:VOL_ALPHA_MIN = \"0.05\" }\n"
        "if (-not $env:VOL_ALPHA_MAX)            { $env:VOL_ALPHA_MAX = \"0.12\" }\n"
        "if (-not $env:VOL_BOOT_THR_FLOOR)       { $env:VOL_BOOT_THR_FLOOR = \"0.02\" }\n"
        "if (-not $env:VOL_BOOT_ALPHA_END_CEIL)  { $env:VOL_BOOT_ALPHA_END_CEIL = \"0.12\" }\n"
        "if (-not $env:VOL_STUCK_THR_FLOOR)      { $env:VOL_STUCK_THR_FLOOR = \"0.02\" }\n"
        "if (-not $env:VOL_STUCK_ALPHA_END_CEIL) { $env:VOL_STUCK_ALPHA_END_CEIL = \"0.12\" }\n\n"
        "# keep clamp ON, but align safe caps/floors with the new setup\n"
        "if (-not $env:VOL_ENFORCE_P14)          { $env:VOL_ENFORCE_P14 = \"1\" }\n"
        "if (-not $env:VOL_SAFE_THR_MIN)         { $env:VOL_SAFE_THR_MIN = \"0.02\" }\n"
        "if (-not $env:VOL_SAFE_ALPHA_MAX)       { $env:VOL_SAFE_ALPHA_MAX = \"0.12\" }\n"
    )

    # 2) Se já existe bloco P15c, substitui por P15d
    if "# [P15c] defaults for production" in text:
        # substitui do [P15c] até antes de "$repo ="
        out2, n = re.subn(r"# \[P15c\][\s\S]*?(?=\$repo\s*=)", block + "\n", text, count=1)
        if n:
            edits += 1
            text = out2
    # 3) Se não existe, injeta após o log do P12
    elif "# [P15d] defaults for meta_iso" not in text:
        marker = 'Write-Host "[P12] auto volume: computing params..." -ForegroundColor Cyan'
        if marker in text:
            text = text.replace(marker, marker + "\n\n" + block, 1)
            edits += 1
        else:
            # fallback: tenta após o bloco param(...)
            m = re.search(r"param\([^)]*\)", text)
            if m:
                idx = m.end()
                text = text[:idx] + "\n\n" + block + text[idx:]
                edits += 1
            else:
                text = block + "\n" + text
                edits += 1

    return text, edits


def patch_config_yaml(text: str) -> tuple[str, int]:
    """Atualiza best.k/best.threshold e alguns campos de volume_control (se existirem)."""

    lines = text.splitlines()
    out: list[str] = []
    edits = 0

    in_best = False
    best_indent: int | None = None
    in_vc = False
    vc_indent: int | None = None

    for line in lines:
        stripped = line.strip()

        if re.match(r"^\s*best:\s*$", line):
            in_best = True
            best_indent = len(line) - len(line.lstrip())
            in_vc = False
        elif re.match(r"^\s*volume_control:\s*$", line):
            in_vc = True
            vc_indent = len(line) - len(line.lstrip())
            in_best = False

        # detect end of sections
        if in_best and stripped and not stripped.startswith("#"):
            indent = len(line) - len(line.lstrip())
            if indent <= (best_indent or 0) and not re.match(r"^\s*best:\s*$", line):
                in_best = False
                best_indent = None

        if in_vc and stripped and not stripped.startswith("#"):
            indent = len(line) - len(line.lstrip())
            if indent <= (vc_indent or 0) and not re.match(r"^\s*volume_control:\s*$", line):
                in_vc = False
                vc_indent = None

        new_line = line

        if in_best:
            if re.match(r"^\s*k:\s*\d+\s*(#.*)?$", line):
                new_line = re.sub(r"(^\s*k:\s*)\d+", r"\g<1>3", line)
            elif re.match(r"^\s*threshold:\s*[-+]?\d*\.?\d+\s*(#.*)?$", line):
                new_line = re.sub(r"(^\s*threshold:\s*)[-+]?\d*\.?\d+", r"\g<1>0.02", line)

        if in_vc:
            repl = {
                "threshold_floor": "0.02",
                "threshold_ceiling": "0.14",
                "alpha_end_ceiling": "0.12",
            }
            for key, val in repl.items():
                if re.match(rf"^\s*{re.escape(key)}:\s*[-+]?\d*\.?\d+\s*(#.*)?$", line):
                    new_line = re.sub(
                        rf"(^\s*{re.escape(key)}:\s*)[-+]?\d*\.?\d+",
                        rf"\g<1>{val}",
                        line,
                    )
                    break

        if new_line != line:
            edits += 1
        out.append(new_line)

    out_text = "\n".join(out)
    if text.endswith("\n"):
        out_text += "\n"

    return out_text, edits


def main() -> None:
    repo = find_repo_root(Path(__file__).resolve().parent)
    print(f"[P15d] Repo: {repo}")

    # 1) auto_volume.py
    auto_path = repo / "src" / "natbin" / "auto_volume.py"
    if auto_path.exists():
        txt = auto_path.read_text(encoding="utf-8")
        new_txt, n = patch_auto_volume_py(txt)
        if n:
            bak = backup_file(auto_path)
            auto_path.write_text(new_txt, encoding="utf-8")
            py_compile.compile(str(auto_path), doraise=True)
            print(f"[P15d] OK auto_volume.py ({n} replacements). Backup: {bak.name}")
        else:
            print("[P15d] auto_volume.py: nada para mudar (já ok)")
    else:
        print("[P15d] WARN: src/natbin/auto_volume.py não encontrado (skip)")

    # 2) observe_loop_auto.ps1
    ps_path = repo / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if ps_path.exists():
        txt = ps_path.read_text(encoding="utf-8")
        new_txt, n = patch_observe_loop_auto_ps1(txt)
        if n:
            bak = backup_file(ps_path)
            ps_path.write_text(new_txt, encoding="utf-8")
            print(f"[P15d] OK observe_loop_auto.ps1 ({n} edit(s)). Backup: {bak.name}")
        else:
            print("[P15d] observe_loop_auto.ps1: nada para mudar (já ok)")
    else:
        print("[P15d] WARN: scripts/scheduler/observe_loop_auto.ps1 não encontrado (skip)")

    # 3) config.yaml
    cfg = repo / "config.yaml"
    if cfg.exists():
        txt = cfg.read_text(encoding="utf-8")
        new_txt, n = patch_config_yaml(txt)
        if n:
            bak = backup_file(cfg)
            cfg.write_text(new_txt, encoding="utf-8")
            print(f"[P15d] OK config.yaml ({n} edit(s)). Backup: {bak.name}")
        else:
            print("[P15d] config.yaml: nada para mudar (já ok)")
    else:
        print("[P15d] config.yaml não encontrado (skip)")

    print("\n[P15d] Pronto. Teste sugerido:")
    print(r"  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once")
    print("\nSe você já tinha runs/auto_params.json com THRESHOLD alto (0.10+),")
    print("o P12 deve começar a baixar de 0.01 em 0.01 durante o bootstrap")
    print("até o piso (0.02).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[P15d] ERRO: {e}")
        sys.exit(1)
