param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Ative/crie a venv antes." }

$targets = @(
  "src\natbin\observe_signal_topk_perday.py",
  "src\natbin\tune_multiwindow_topk.py",
  "src\natbin\paper_pnl_backtest.py"
)

foreach ($t in $targets) {
  if (-not (Test-Path $t)) { throw "Nao achei $t" }
}

# Backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\p2_4_fix_backslash_replace_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($t in $targets) {
  Copy-Item $t (Join-Path $backupDir ([IO.Path]::GetFileName($t))) -Force
}
Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

# Patch via Python (regex: só corrige o caso de 1 backslash literal dentro da string)
& $py -c @'
from pathlib import Path
import re

files = [
  r"src/natbin/observe_signal_topk_perday.py",
  r"src/natbin/tune_multiwindow_topk.py",
  r"src/natbin/paper_pnl_backtest.py",
]

pat_dq = r'replace\("(?<!\\)\\",\s*"([^"]*)"\)'     # replace("\", "/")  -> inválido
rep_dq = r'replace("\\\\", "\1")'                   # replace("\\", "/") -> válido

pat_sq = r"replace\('(?<!\\)\\',\s*'([^']*)'\)"     # replace('\', '/')  -> inválido
rep_sq = r"replace('\\\\', '\1')"                   # replace('\\', '/') -> válido

total = 0
for f in files:
    p = Path(f)
    txt = p.read_text(encoding="utf-8")
    before = txt

    txt, n1 = re.subn(pat_dq, rep_dq, txt)
    txt, n2 = re.subn(pat_sq, rep_sq, txt)

    if txt != before:
        p.write_text(txt, encoding="utf-8")
    print(f"{f}: fixed_dq={n1} fixed_sq={n2}")
    total += (n1+n2)

print("Total fixes:", total)
'@
if ($LASTEXITCODE -ne 0) { throw "Patch Python falhou." }

# Compile dos 3 arquivos + compileall
& $py -c "import py_compile; py_compile.compile(r'src/natbin/observe_signal_topk_perday.py', doraise=True); py_compile.compile(r'src/natbin/tune_multiwindow_topk.py', doraise=True); py_compile.compile(r'src/natbin/paper_pnl_backtest.py', doraise=True); print('OK: py_compile 3 files')"
if ($LASTEXITCODE -ne 0) { throw "py_compile falhou" }

& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: backslash corrigido e compilando." -ForegroundColor Green