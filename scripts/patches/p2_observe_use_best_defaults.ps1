param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Ative/crie a venv antes." }

$path = "src\natbin\observe_signal_topk_perday.py"
if (-not (Test-Path $path)) { throw "Nao achei $path" }

# Backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\observe_best_defaults_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $path (Join-Path $backupDir "observe_signal_topk_perday.py") -Force
Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

# Patch via Python (mais confiável que replace no PowerShell)
& $py -c @"
from pathlib import Path
import re

path = Path(r"src/natbin/observe_signal_topk_perday.py")
txt = path.read_text(encoding="utf-8")

# 1) k: env TOPK_K tem prioridade; se vazio -> best.k; fallback 2
pat_k = r'(?m)^\s*k\s*=\s*int\(os\.getenv\("TOPK_K",\s*"2"\)\)\s*$'
rep_k = (
'    k_env = os.getenv("TOPK_K", "").strip()\\n'
'    try:\\n'
'        k = int(k_env) if k_env else int(best.get("k", 2))\\n'
'    except Exception:\\n'
'        k = 2'
)
txt, n1 = re.subn(pat_k, rep_k, txt)

# 2) thresh_on: env THRESH_ON tem prioridade; se vazio -> best.thresh_on; fallback score
pat_thr = r'(?m)^\s*thresh_on\s*=\s*os\.getenv\("THRESH_ON",\s*"score"\)\.strip\(\)\.lower\(\)\s*\\n\s*if\s+thresh_on\s+not\s+in\s*\("score",\s*"conf"\)\s*:\s*\\n\s*thresh_on\s*=\s*"score"\s*$'
rep_thr = (
'    thresh_on_env = os.getenv("THRESH_ON", "").strip()\\n'
'    thresh_on = (thresh_on_env or str(best.get("thresh_on", "score"))).strip().lower()\\n'
'    if thresh_on not in ("score", "conf"):\\n'
'        thresh_on = "score"'
)
txt, n2 = re.subn(pat_thr, rep_thr, txt)

if n1 == 0:
    print("WARN: nao achei a linha TOPK_K para substituir (talvez ja esteja atualizado).")
if n2 == 0:
    print("WARN: nao achei o bloco THRESH_ON para substituir (talvez ja esteja atualizado).")

# 3) Print debug: adiciona k no print final
if '"k": k' not in txt and '"threshold": row["threshold"],' in txt:
    txt = txt.replace('"threshold": row["threshold"],', '"threshold": row["threshold"],\\n            "k": k,')

path.write_text(txt, encoding="utf-8")
print("OK: observe usa best.k/best.thresh_on por default (env tem prioridade).")
"@

& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK." -ForegroundColor Green
Write-Host "Teste:" -ForegroundColor Yellow
Write-Host "  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once" -ForegroundColor Yellow