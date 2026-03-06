param(
  [switch]$Once,
  [int]$TopK = 3,
  [int]$LookbackCandles = 2000,
  [int]$MaxCycles = 0,
  [switch]$QuotaAwareSleep,
  [switch]$PrecheckMarketContext,
  [switch]$Json,
  [string]$RepoRoot = '',
  [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = if ($RepoRoot) {
  (Resolve-Path $RepoRoot).Path
} else {
  (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

. (Join-Path $PSScriptRoot '_bootstrap_python.ps1')

$args = @(
  'portfolio', 'observe',
  '--repo-root', $repo,
  '--topk', [string]$TopK,
  '--lookback-candles', [string]$LookbackCandles
)
if ($Once) { $args += '--once' }
if ($MaxCycles -gt 0) { $args += @('--max-cycles', [string]$MaxCycles) }
if ($QuotaAwareSleep) { $args += '--quota-aware-sleep' }
if ($PrecheckMarketContext) { $args += '--precheck-market-context' }
if ($Json) { $args += '--json' }

Push-Location $repo
try {
  Invoke-RepoPythonModule -RepoRoot $repo -Module 'natbin.runtime_app' -ModuleArgs $args -Python $Python
  $code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
} finally {
  Pop-Location
}

exit $code
