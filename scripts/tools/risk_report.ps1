param(
  [double]$Bankroll = 1000,
  [string]$Windows = "30,60,120",
  [int]$MinTrades = 10,
  [ValidateSet("loss","push")][string]$Tie = "loss",
  [ValidateSet("open_close","close_close")][string]$Outcome = "open_close",
  [double]$PayoutDefault = 0.8,
  [double]$KellyFrac = 0.25,
  [double]$CapFrac = 0.02,
  [string]$SignalsDb = "runs/live_signals.sqlite3",
  [string]$MarketDb = "",
  [string]$Asset = "",
  [int]$IntervalSec = 0,
  [switch]$Save
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

# Garante rodar no ROOT do repo (Start in do Agendador)
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $root
try {
  $py = Join-Path $root ".venv\Scripts\python.exe"
  Require-Path $py "Nao encontrei .venv.`nRode scripts/setup/init.ps1 (ou init.ps1) para criar a venv."
  Require-Path "config.yaml" "Nao encontrei config.yaml."
  Require-Path $SignalsDb "Nao encontrei signals db: $SignalsDb"

  $args = @(
    "-m","natbin.risk_report",
    "--signals-db", $SignalsDb,
    "--windows", $Windows,
    "--min-trades", "$MinTrades",
    "--tie", $Tie,
    "--outcome", $Outcome,
    "--payout-default", "$PayoutDefault",
    "--kelly-frac", "$KellyFrac",
    "--cap-frac", "$CapFrac",
    "--bankroll", "$Bankroll"
  )

  if ($MarketDb) { $args += @("--market-db", $MarketDb) }
  if ($Asset)    { $args += @("--asset", $Asset) }
  if ($IntervalSec -gt 0) { $args += @("--interval-sec", "$IntervalSec") }

  if ($Save) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outJson = Join-Path "runs" ("risk_report_{0}.json" -f $stamp)
    $outCsv  = Join-Path "runs" ("risk_trades_{0}.csv" -f $stamp)
    $args += @("--out-json", $outJson, "--out-trades-csv", $outCsv)
  }

  $pretty = (@($py) + $args | ForEach-Object { if ($_ -match "\s") { '"' + $_ + '"' } else { $_ } }) -join " "
  Write-Host ">> $pretty" -ForegroundColor DarkGray
  & $py @args
  if ($LASTEXITCODE -ne 0) { throw "risk_report falhou" }
} finally {
  Pop-Location
}