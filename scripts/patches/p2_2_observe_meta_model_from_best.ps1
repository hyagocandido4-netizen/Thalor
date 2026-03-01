param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Ative/crie a venv antes." }

$path = "src\natbin\observe_signal_topk_perday.py"
if (-not (Test-Path $path)) { throw "Nao achei $path" }

# Backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\p2_2_meta_model_from_best_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $path (Join-Path $backupDir "observe_signal_topk_perday.py") -Force
Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

& $py -c @"
from pathlib import Path
import re

p = Path(r"$path")
txt = p.read_text(encoding="utf-8")

# troca:
# meta_model_type = os.getenv("META_MODEL", "logreg").strip().lower()
# por:
# meta_model_env = os.getenv("META_MODEL", "").strip()
# meta_model_type = (meta_model_env or str(best.get("meta_model","logreg"))).strip().lower()

pat = r'(?m)^(?P<ind>[ \t]*)meta_model_type\s*=\s*os\.getenv\("META_MODEL",\s*"logreg"\)\.strip\(\)\.lower\(\)\s*$'
m = re.search(pat, txt)
if not m:
    print("WARN: nao achei linha meta_model_type (talvez ja esteja atualizado).")
else:
    ind = m.group("ind")
    rep = (
        ind + 'meta_model_env = os.getenv("META_MODEL", "").strip()\\n'
        + ind + 'meta_model_type = (meta_model_env or str(best.get("meta_model", "logreg"))).strip().lower()'
    )
    txt = re.sub(pat, rep, txt, count=1)

# também melhora o print final pra mostrar meta_model
if '"meta_model": row["meta_model"],' not in txt:
    txt = txt.replace(
        '"gate_mode": row["gate_mode"],\n            "regime_ok": row["regime_ok"],',
        '"gate_mode": row["gate_mode"],\n            "meta_model": row.get("meta_model"),\n            "regime_ok": row["regime_ok"],',
        1
    )

p.write_text(txt, encoding="utf-8")
print("OK: observe agora usa best.meta_model como default (env ainda tem prioridade).")
"@

if ($LASTEXITCODE -ne 0) { throw "Patch falhou." }

& $py -c "import py_compile; py_compile.compile(r'src/natbin/observe_signal_topk_perday.py', doraise=True); print('OK: py_compile observe_signal_topk_perday.py')"
if ($LASTEXITCODE -ne 0) { throw "py_compile falhou" }

& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK." -ForegroundColor Green
Write-Host "Teste:" -ForegroundColor Yellow
Write-Host "  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once" -ForegroundColor Yellow