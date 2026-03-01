param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Ative/crie a venv antes." }

$files = @(
  "src\natbin\observe_signal_topk_perday.py",
  "src\natbin\tune_multiwindow_topk.py",
  "src\natbin\paper_pnl_backtest.py"
)

foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "Nao achei $f" }
}

# Backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\p2_4_fix_escaped_quotes_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($f in $files) {
  Copy-Item $f (Join-Path $backupDir ([IO.Path]::GetFileName($f))) -Force
}
Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

# Fix: troca \" -> " nos 3 arquivos
& $py -c @"
from pathlib import Path

files = [
  r"src/natbin/observe_signal_topk_perday.py",
  r"src/natbin/tune_multiwindow_topk.py",
  r"src/natbin/paper_pnl_backtest.py",
]

for f in files:
    p = Path(f)
    txt = p.read_text(encoding="utf-8")
    n = txt.count('\\"')  # \" literal no arquivo
    txt2 = txt.replace('\\"', '"')
    if txt2 != txt:
        p.write_text(txt2, encoding="utf-8")
    print(f"{f}: replaced {n} occurrences of \\\"")
"@
if ($LASTEXITCODE -ne 0) { throw "Fix python falhou." }

# Compile: arquivos + compileall
& $py -c "import py_compile; py_compile.compile(r'src/natbin/observe_signal_topk_perday.py', doraise=True); py_compile.compile(r'src/natbin/tune_multiwindow_topk.py', doraise=True); py_compile.compile(r'src/natbin/paper_pnl_backtest.py', doraise=True); print('OK: py_compile 3 files')"
if ($LASTEXITCODE -ne 0) { throw "py_compile falhou" }

& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: quotes corrigidas e Python compilando." -ForegroundColor Green
Write-Host "Agora pode seguir com o tuner/paper usando --thresh-on ev." -ForegroundColor Yellow