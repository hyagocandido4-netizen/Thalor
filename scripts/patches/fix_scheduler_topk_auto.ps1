param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param([string]$Path, [string]$Content)
  $enc = New-Object System.Text.UTF8Encoding($false)
  $norm = $Content.Replace("`r`n","`n")
  [System.IO.File]::WriteAllText($Path, $norm, $enc)
}

$target = ".\scripts\scheduler\observe_loop.ps1"
if (-not (Test-Path $target)) { throw "Nao achei $target" }

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\fix_scheduler_topk_auto_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $target (Join-Path $backupDir "observe_loop.ps1") -Force
Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

Write-Utf8NoBomFile -Path $target -Content @'
param(
  [int]$LookbackCandles = 2000,
  # TopK=0 => NAO faz override; usa best.k do config.yaml
  [int]$TopK = 0,
  [switch]$Once
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

# Garante rodar no ROOT do repo (funciona bem no Agendador)
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $root
try {
  $py = Join-Path $root ".venv\Scripts\python.exe"
  Require-Path $py "Nao encontrei .venv. Rode scripts/setup/phase2_bootstrap.ps1."
  Require-Path ".env" "Nao encontrei .env. Copie .env.example para .env."
  Require-Path "config.yaml" "Nao encontrei config.yaml."
  Require-Path "src\natbin\collect_recent.py" "Nao encontrei src\natbin\collect_recent.py"
  Require-Path "src\natbin\make_dataset.py" "Nao encontrei src\natbin\make_dataset.py"
  Require-Path "src\natbin\observe_signal_topk_perday.py" "Nao encontrei src\natbin\observe_signal_topk_perday.py"

  while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "`n[$ts] OBSERVE LOOP" -ForegroundColor Cyan

    $env:LOOKBACK_CANDLES = "$LookbackCandles"

    & $py -m natbin.collect_recent
    if ($LASTEXITCODE -ne 0) { throw "collect_recent falhou" }

    & $py -m natbin.make_dataset
    if ($LASTEXITCODE -ne 0) { throw "make_dataset falhou" }

    # Só faz override de TOPK_K se você passar -TopK > 0
    if ($TopK -gt 0) {
      $env:TOPK_K = "$TopK"
      Write-Host "TOPK_K override: $TopK" -ForegroundColor DarkGray
    } else {
      Remove-Item Env:TOPK_K -ErrorAction SilentlyContinue
      Write-Host "TOPK_K: usando best.k do config.yaml (sem override)" -ForegroundColor DarkGray
    }

    $env:LIVE_SIGNALS_PATH = Join-Path (Join-Path $root "runs") ("live_signals_v2_{0}.csv" -f (Get-Date -Format "yyyyMMdd"))

    & $py -m natbin.observe_signal_topk_perday
    if ($LASTEXITCODE -ne 0) { throw "observe_signal_topk_perday falhou" }

    if ($Once) { break }
    Start-Sleep -Seconds 310
  }
}
finally {
  Pop-Location
}
'@

Write-Host "OK: observe_loop.ps1 atualizado (TopK default=auto/best.k)." -ForegroundColor Green
Write-Host "Teste:" -ForegroundColor Yellow
Write-Host "  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once" -ForegroundColor Yellow
Write-Host "Forcar topk:" -ForegroundColor Yellow
Write-Host "  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -TopK 1 -Once" -ForegroundColor Yellow