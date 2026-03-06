param(
  [switch]$Once,
  [switch]$Json,
  [switch]$QuotaJson,
  [string]$RepoRoot = '',
  [string]$Python = '',
  [int]$TopK = 3,
  [int]$LookbackCandles = 2000
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = if ($RepoRoot) {
  (Resolve-Path $RepoRoot).Path
} else {
  (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

. (Join-Path $PSScriptRoot '_bootstrap_python.ps1')

if ($QuotaJson) {
  $args = @('quota', '--repo-root', $repo, '--topk', [string]$TopK)
  if ($Json) { $args += '--json' }
  Push-Location $repo
  try {
    Invoke-RepoPythonModule -RepoRoot $repo -Module 'natbin.runtime_app' -ModuleArgs $args -Python $Python
    $code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
  } finally {
    Pop-Location
  }
  exit $code
}

$args = @('portfolio', 'observe', '--repo-root', $repo, '--topk', [string]$TopK, '--lookback-candles', [string]$LookbackCandles)
if ($Once) { $args += '--once' }
if ($Json) { $args += '--json' }

Push-Location $repo
try {
  Invoke-RepoPythonModule -RepoRoot $repo -Module 'natbin.runtime_app' -ModuleArgs $args -Python $Python
  $code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
} finally {
  Pop-Location
}

exit $code
