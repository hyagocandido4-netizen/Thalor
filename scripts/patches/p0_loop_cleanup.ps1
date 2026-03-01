$ErrorActionPreference="Stop"
Set-StrictMode -Version Latest

$path = ".\scripts\scheduler\observe_loop.ps1"
if(-not (Test-Path $path)){ throw "Nao achei $path" }

@'
param(
  [int]$LookbackCandles = 2000,
  [int]$TopK = 2,
  [switch]$Once
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

$root = (Get-Location).Path
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

  $env:TOPK_K = "$TopK"
  $env:LIVE_SIGNALS_PATH = Join-Path (Join-Path (Get-Location) "runs") ("live_signals_v2_{0}.csv" -f (Get-Date -Format "yyyyMMdd"))

  & $py -m natbin.observe_signal_topk_perday
  if ($LASTEXITCODE -ne 0) { throw "observe_signal_topk_perday falhou" }

  if ($Once) { break }
  Start-Sleep -Seconds 310
}
'@ | Set-Content -Encoding UTF8 $path

Write-Host "OK: observe_loop.ps1 limpo (sem autogerador, path único)" -ForegroundColor Green