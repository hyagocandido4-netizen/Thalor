from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
import py_compile


def find_repo_root() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        Path.cwd(),
        here.parents[2],  # scripts/patches -> scripts -> repo root
        here.parents[1],
        here.parents[0],
    ]
    for root in candidates:
        if (root / "src" / "natbin" / "observe_signal_topk_perday.py").exists():
            return root
    raise SystemExit(
        "Não achei src/natbin/observe_signal_topk_perday.py.\n"
        "Rode este script dentro do repo (C:\\Users\\hyago\\Documents\\bot)."
    )


def backup_file(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def insert_after_import_block(text: str, insert_block: str, marker: str) -> tuple[str, bool]:
    if marker in text:
        return text, False

    lines = text.splitlines(True)

    # pula docstring inicial se existir
    i = 0
    if i < len(lines) and lines[i].lstrip().startswith(('"""', "'''")):
        q = lines[i].lstrip()[:3]
        i += 1
        while i < len(lines) and q not in lines[i]:
            i += 1
        if i < len(lines):
            i += 1  # inclui a linha final do docstring

    # agora avança por bloco de imports iniciais
    j = i
    while j < len(lines):
        s = lines[j].strip()
        if s == "" or s.startswith("#"):
            j += 1
            continue
        if s.startswith("from __future__ import"):
            j += 1
            continue
        if s.startswith("import ") or s.startswith("from "):
            j += 1
            continue
        break

    new_text = "".join(lines[:j]) + insert_block + ("\n" if not insert_block.endswith("\n") else "") + "".join(lines[j:])
    return new_text, True


def fix_covreg_thr_defaults(text: str) -> tuple[str, int]:
    """
    Corrige defaults quebrados do tipo str(thr) em getenv, que causam NameError em import.
    """
    n = 0
    # COVREG_THR_LO default str(thr)
    pat1 = re.compile(r'os\.getenv\(\s*["\']COVREG_THR_LO["\']\s*,\s*str\(\s*thr\s*\)\s*\)')
    text, k = pat1.subn('os.getenv("COVREG_THR_LO", "0.10")', text)
    n += k

    # COVREG_THR_HI default str(thr)
    pat2 = re.compile(r'os\.getenv\(\s*["\']COVREG_THR_HI["\']\s*,\s*str\(\s*thr\s*\)\s*\)')
    text, k = pat2.subn('os.getenv("COVREG_THR_HI", "0.10")', text)
    n += k

    # linha exata mais comum
    bad = 'thr_lo = float(os.getenv("COVREG_THR_LO", str(thr)))'
    if bad in text:
        text = text.replace(bad, 'thr_lo = float(os.getenv("COVREG_THR_LO", "0.10"))')
        n += 1

    return text, n


def fix_thr_eff_nameerror(text: str) -> tuple[str, int]:
    """
    Evita NameError em linhas do tipo:
      thr_eff = float(thr)
    quando elas aparecem fora de função / no import.
    """
    n = 0
    # substitui apenas quando é assignment de thr_eff/thr_hi
    pat = re.compile(r'^(\s*thr_eff\s*=\s*)float\(\s*thr\s*\)\s*$', flags=re.M)
    text, k = pat.subn(r'\1float(globals().get("thr", 0.10))', text)
    n += k

    pat2 = re.compile(r'^(\s*thr_hi\s*=\s*)float\(\s*thr\s*\)\s*$', flags=re.M)
    text, k = pat2.subn(r'\1float(globals().get("thr", 0.10))', text)
    n += k

    return text, n


def ensure_cp_allowed(text: str) -> tuple[str, bool]:
    """
    Garante que o gate_mode aceite 'cp' (se ainda estiver limitado a meta/iso/conf).
    """
    changed = False
    # variação com aspas duplas
    if '("meta", "iso", "conf")' in text and '"cp"' not in text:
        text = text.replace('("meta", "iso", "conf")', '("meta", "iso", "conf", "cp")')
        changed = True
    # variação com aspas simples
    if "('meta', 'iso', 'conf')" in text and "'cp'" not in text:
        text = text.replace("('meta', 'iso', 'conf')", "('meta', 'iso', 'conf', 'cp')")
        changed = True
    return text, changed


def inject_cpreg_before_compute_scores(text: str) -> tuple[str, bool]:
    """
    Injeta CPREG (P8b) imediatamente antes do primeiro compute_scores(...)
    com indentação correta.
    """
    if "P8b: CPREG" in text:
        return text, False

    lines = text.splitlines(True)
    idx = None
    for i, line in enumerate(lines):
        if "compute_scores(" in line and not line.lstrip().startswith("#"):
            idx = i
            break
    if idx is None:
        raise SystemExit("Não encontrei 'compute_scores(' no observe_signal_topk_perday.py")

    indent = re.match(r"^(\s*)", lines[idx]).group(1)

    block = [
        f"{indent}# --- P8b: CPREG (alpha schedule + slot-aware) ---\n",
        f"{indent}import os as _os\n",
        f"{indent}from datetime import datetime as _dt\n",
        f"{indent}if _os.getenv('CPREG_ENABLE', '0').strip() == '1':\n",
        f"{indent}    _gm = (locals().get('gate_mode_eff') or locals().get('gate_mode') or '').strip().lower()\n",
        f"{indent}    if _gm == 'cp':\n",
        f"{indent}        _now = _dt.now()\n",
        f"{indent}        _sec = (_now.hour * 3600) + (_now.minute * 60) + _now.second\n",
        f"{indent}        _frac = _sec / 86400.0\n",
        f"{indent}        try:\n",
        f"{indent}            _slot = int(executed_today_count(asset, day)) + 1\n",
        f"{indent}        except Exception:\n",
        f"{indent}            _slot = int(locals().get('executed_today', 0)) + 1\n",
        f"{indent}        _a0 = float(_os.getenv('CPREG_ALPHA_START', '0.06'))\n",
        f"{indent}        _a1 = float(_os.getenv('CPREG_ALPHA_END',   '0.09'))\n",
        f"{indent}        _w  = float(_os.getenv('CPREG_WARMUP_FRAC',  '0.50'))\n",
        f"{indent}        _e  = float(_os.getenv('CPREG_RAMP_END_FRAC','0.90'))\n",
        f"{indent}        _m2 = float(_os.getenv('CPREG_SLOT2_MULT',   '0.85'))\n",
        f"{indent}        if _frac <= _w:\n",
        f"{indent}            _a = _a0\n",
        f"{indent}        elif _frac >= _e:\n",
        f"{indent}            _a = _a1\n",
        f"{indent}        else:\n",
        f"{indent}            _u = (_frac - _w) / max(1e-9, (_e - _w))\n",
        f"{indent}            _a = _a0 + (_a1 - _a0) * _u\n",
        f"{indent}        if _slot >= 2:\n",
        f"{indent}            _a = _a * _m2\n",
        f"{indent}        _a = max(0.001, min(0.50, _a))\n",
        f"{indent}        _os.environ['CP_ALPHA'] = '%.4f' % (_a,)\n",
        f"{indent}# --- /P8b ---\n",
    ]

    new_lines = lines[:idx] + block + lines[idx:]
    return "".join(new_lines), True


def main() -> None:
    root = find_repo_root()
    obs = root / "src" / "natbin" / "observe_signal_topk_perday.py"

    print(f"[P8b] Repo: {root}")
    print(f"[P8b] File: {obs}")

    bkp = backup_file(obs)
    print(f"[P8b] Backup: {bkp}")

    text = obs.read_text(encoding="utf-8")

    # 1) corrigir defaults e NameError de thr_eff
    text, n1 = fix_covreg_thr_defaults(text)
    text, n2 = fix_thr_eff_nameerror(text)

    # 2) inserir “thr guard” (define thr cedo) para qualquer bloco legado no import
    thr_guard = """\
# --- P8b safety: thr guard (evita NameError de patches legados) ---
try:
    thr  # type: ignore[name-defined]
except Exception:
    try:
        import yaml as _yaml
        from pathlib import Path as _Path
        _cfg = _yaml.safe_load(_Path("config.yaml").read_text(encoding="utf-8")) or {}
        _best = _cfg.get("best") or {}
        thr = float(_best.get("threshold", 0.10))
    except Exception:
        import os as _os
        thr = float(_os.getenv("THRESHOLD", "0.10"))
# --- /P8b safety ---
"""
    text, inserted_guard = insert_after_import_block(
        text, thr_guard, marker="P8b safety: thr guard"
    )

    # 3) garantir cp permitido
    text, cp_changed = ensure_cp_allowed(text)

    # 4) inserir CPREG (P8b)
    text, cpreg_inserted = inject_cpreg_before_compute_scores(text)

    obs.write_text(text, encoding="utf-8")

    # valida sintaxe
    py_compile.compile(str(obs), doraise=True)

    print(f"[P8b] FIX covreg defaults: {n1}")
    print(f"[P8b] FIX thr_eff/thr_hi: {n2}")
    print(f"[P8b] inserted thr_guard: {inserted_guard}")
    print(f"[P8b] ensured cp allowed: {cp_changed}")
    print(f"[P8b] inserted CPREG: {cpreg_inserted}")
    print("[P8b] OK: py_compile passou. Observe loop deve rodar.")


if __name__ == "__main__":
    main()