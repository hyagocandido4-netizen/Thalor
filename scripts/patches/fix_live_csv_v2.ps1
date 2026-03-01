$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$today = Get-Date -Format "yyyyMMdd"
$runs = Join-Path (Get-Location) "runs"

$old = Join-Path $runs ("live_signals_{0}.csv" -f $today)
$legacy = Join-Path $runs ("live_signals_{0}_legacy.csv" -f $today)
$new = Join-Path $runs ("live_signals_v2_{0}.csv" -f $today)

New-Item -ItemType Directory -Force -Path $runs | Out-Null

# 1) Se existe o arquivo antigo do dia e ainda não criamos o v2, renomeia para legacy
if ((Test-Path $old) -and (-not (Test-Path $new))) {
  Rename-Item -Force $old $legacy
  Write-Host "Renomeado para legacy: $legacy" -ForegroundColor Green
} else {
  Write-Host "Nada para renomear (ok)." -ForegroundColor DarkGray
}

# 2) Patch observe_loop.ps1 para sempre escrever no CSV v2 diário
$loopPath = "observe_loop.ps1"
if (-not (Test-Path $loopPath)) {
  throw "Nao achei observe_loop.ps1 na raiz."
}

$loop = Get-Content $loopPath -Raw

# Insere override idempotente antes da chamada do observer TopK
$needle = '& $py -m natbin.observe_signal_topk_perday'
if ($loop -notmatch [regex]::Escape($needle)) {
  throw "Nao achei a chamada do observer TopK no observe_loop.ps1 (natbin.observe_signal_topk_perday)."
}

if ($loop -notmatch "live_signals_v2_") {
  $insert = @'
  # CSV v2 diario (evita misturar schemas antigos/novos)
  $env:LIVE_SIGNALS_PATH = Join-Path (Join-Path (Get-Location) "runs") ("live_signals_v2_{0}.csv" -f (Get-Date -Format "yyyyMMdd"))
'@
  $loop = $loop -replace [regex]::Escape($needle), ($insert + "`r`n  " + $needle)
  Set-Content -Encoding UTF8 $loopPath $loop
  Write-Host "observe_loop.ps1 atualizado para gravar em live_signals_v2_YYYYMMDD.csv" -ForegroundColor Green
} else {
  Write-Host "observe_loop.ps1 ja esta usando live_signals_v2_*. (skip)" -ForegroundColor DarkGray
}

Write-Host "`nPronto." -ForegroundColor Green
Write-Host "Agora rode: pwsh -ExecutionPolicy Bypass -File .\observe_loop.ps1 -Once" -ForegroundColor Yellow
Write-Host "E abra: runs\live_signals_v2_$today.csv" -ForegroundColor Yellow