param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Ative/crie a venv antes." }

$path = "src\natbin\observe_signal_topk_perday.py"
if (-not (Test-Path $path)) { throw "Nao achei $path" }

# Backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\p2_2_fix_regime_ok_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $path (Join-Path $backupDir "observe_signal_topk_perday.py") -Force
Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

# Patch via Python (mais robusto que replace em PS)
& $py -c @"
from pathlib import Path
import re

p = Path(r"$path")
txt = p.read_text(encoding="utf-8")

def ensure_base_fields(txt: str) -> tuple[str,int]:
    m = re.search(r'BASE_FIELDS\s*=\s*\[\s*\n(?P<blk>.*?\n)\s*\]\s*\n', txt, flags=re.S)
    if not m:
        raise RuntimeError("Nao achei BASE_FIELDS.")
    blk = m.group("blk")
    if '"regime_ok"' in blk:
        return txt, 0
    if '"gate_mode",' in blk:
        blk2 = blk.replace('"gate_mode",\n', '"gate_mode",\n    "regime_ok",\n', 1)
    else:
        blk2 = '"regime_ok",\n' + blk
    txt2 = txt[:m.start("blk")] + blk2 + txt[m.end("blk"):]
    return txt2, 1

def ensure_create_table(txt: str) -> tuple[str,int]:
    if "regime_ok INTEGER" in txt:
        return txt, 0
    # insere regime_ok no SQL do CREATE TABLE
    pat = r'(gate_mode TEXT,\s*\n\s*)thresh_on TEXT,'
    txt2, n = re.subn(pat, r'\1regime_ok INTEGER NOT NULL,\n          thresh_on TEXT,', txt, count=1)
    if n == 0:
        # fallback simples
        txt2b = txt.replace(
            "gate_mode TEXT,\n          thresh_on TEXT,",
            "gate_mode TEXT,\n          regime_ok INTEGER NOT NULL,\n          thresh_on TEXT,",
            1,
        )
        return (txt2b, 1 if txt2b != txt else 0)
    return txt2, 1

def ensure_add_cols(txt: str) -> tuple[str,int]:
    m = re.search(r'add_cols\s*=\s*\{\s*\n(?P<blk>.*?\n)\s*\}\s*\n', txt, flags=re.S)
    if not m:
        raise RuntimeError("Nao achei add_cols.")
    blk = m.group("blk")
    if '"regime_ok"' in blk:
        return txt, 0
    if '"gate_mode": "TEXT",' in blk:
        blk2 = blk.replace(
            '"gate_mode": "TEXT",\n',
            '"gate_mode": "TEXT",\n        "regime_ok": "INTEGER",\n',
            1
        )
    else:
        blk2 = '        "regime_ok": "INTEGER",\n' + blk
    txt2 = txt[:m.start("blk")] + blk2 + txt[m.end("blk"):]
    return txt2, 1

def ensure_row_field(txt: str) -> tuple[str,int]:
    # garante regime_ok no row = {...}
    m = re.search(r'row\s*=\s*\{\s*\n(?P<blk>.*?\n)\s*\}\s*\n', txt, flags=re.S)
    if not m:
        raise RuntimeError("Nao achei row = {...}.")
    blk = m.group("blk")
    if '"regime_ok"' in blk:
        return txt, 0
    # insere após gate_mode
    blk2 = blk
    if '"gate_mode": gate_used,' in blk2:
        blk2 = blk2.replace(
            '"gate_mode": gate_used,\n',
            '"gate_mode": gate_used,\n        "regime_ok": int(bool(mask[now_i])),\n',
            1
        )
    else:
        blk2 = '        "regime_ok": int(bool(mask[now_i])),\n' + blk2
    txt2 = txt[:m.start("blk")] + blk2 + txt[m.end("blk"):]
    return txt2, 1

txt, n1 = ensure_base_fields(txt)
txt, n2 = ensure_create_table(txt)
txt, n3 = ensure_add_cols(txt)
txt, n4 = ensure_row_field(txt)

# Opcional: print debug inclui regime_ok
if '"regime_ok": row["regime_ok"]' not in txt:
    txt = txt.replace(
        '"gate_mode": row["gate_mode"],',
        '"gate_mode": row["gate_mode"],\n            "regime_ok": row["regime_ok"],',
        1
    )

p.write_text(txt, encoding="utf-8")
print("OK patches:", {"base_fields": n1, "create_table": n2, "add_cols": n3, "row": n4})
"@

if ($LASTEXITCODE -ne 0) { throw "Patch em Python falhou." }

# Compila só o arquivo alvo (pra erro ficar claro)
& $py -c "import py_compile; py_compile.compile(r'src/natbin/observe_signal_topk_perday.py', doraise=True); print('OK: py_compile observe_signal_topk_perday.py')"
if ($LASTEXITCODE -ne 0) { throw "py_compile falhou no observe_signal_topk_perday.py" }

# Compila tudo
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: regime_ok voltou (SQLite/CSV compatíveis). Rode observe de novo." -ForegroundColor Green