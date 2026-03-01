param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Ative/crie a venv antes." }

$path = "src\natbin\observe_signal_topk_perday.py"
if (-not (Test-Path $path)) { throw "Nao achei $path" }

# Backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\observe_best_defaults_v2_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $path (Join-Path $backupDir "observe_signal_topk_perday.py") -Force
Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

# Patch via Python (com \n reais)
& $py -c @"
from pathlib import Path
import re

path = Path(r"$path")
txt = path.read_text(encoding="utf-8")

# 1) k: env TOPK_K tem prioridade; se vazio -> best.k; fallback 2
pat_k = r'(?m)^(?P<ind>[ \t]*)k\s*=\s*int\(os\.getenv\("TOPK_K",\s*"2"\)\)\s*$'
def repl_k(m):
    ind = m.group("ind")
    return (
        ind + 'k_env = os.getenv("TOPK_K", "").strip()\n'
        + ind + 'try:\n'
        + ind + '    k = int(k_env) if k_env else int(best.get("k", 2))\n'
        + ind + 'except Exception:\n'
        + ind + '    k = 2'
    )
txt, n1 = re.subn(pat_k, repl_k, txt, count=1)

# 2) THRESH_ON: env tem prioridade; se vazio -> best.thresh_on; fallback score
pat_th = r'(?m)^(?P<ind>[ \t]*)thresh_on\s*=\s*os\.getenv\("THRESH_ON",\s*"score"\)\.strip\(\)\.lower\(\)\s*$'
def repl_th(m):
    ind = m.group("ind")
    return (
        ind + 'thresh_on_env = os.getenv("THRESH_ON", "").strip()\n'
        + ind + 'thresh_on = (thresh_on_env or str(best.get("thresh_on", "score"))).strip().lower()'
    )
txt, n2 = re.subn(pat_th, repl_th, txt, count=1)

# 3) garante validação se não existir
if 'thresh_on = (thresh_on_env' in txt and 'if thresh_on not in ("score", "conf")' not in txt:
    lines = txt.splitlines()
    out=[]
    inserted=False
    for line in lines:
        out.append(line)
        if (not inserted) and ('thresh_on = (thresh_on_env' in line):
            ind = re.match(r'^\s*', line).group(0)
            out.append(ind + 'if thresh_on not in ("score", "conf"):')
            out.append(ind + '    thresh_on = "score"')
            inserted=True
    txt = "\n".join(out) + "\n"

path.write_text(txt, encoding="utf-8")

print("OK: patch aplicado.")
print("k_line_patched:", n1)
print("thresh_on_line_patched:", n2)
"@

if ($LASTEXITCODE -ne 0) { throw "Falhou ao patchar observe via Python." }

# Compila APENAS o arquivo alvo (pra erro ficar claro)
& $py -c "import py_compile; py_compile.compile(r'src/natbin/observe_signal_topk_perday.py', doraise=True); print('OK: py_compile observe_signal_topk_perday.py')"
if ($LASTEXITCODE -ne 0) { throw "py_compile falhou no observe_signal_topk_perday.py" }

# Compila tudo (sem esconder output)
& $py -m compileall .\src\natbin
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: observe agora usa best.k/best.thresh_on por default (env tem prioridade)." -ForegroundColor Green
Write-Host "Teste:" -ForegroundColor Yellow
Write-Host "  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once" -ForegroundColor Yellow