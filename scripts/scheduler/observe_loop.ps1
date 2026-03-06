param(
  [switch]$Once,
  [int]$TopK = 3,
  [int]$LookbackCandles = 2000,
  [int]$MaxCycles = 0,
  [switch]$Json,
  [string]$RepoRoot = '',
  [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptPath = Join-Path $PSScriptRoot 'observe_loop_auto.ps1'
$args = @('-TopK', [string]$TopK, '-LookbackCandles', [string]$LookbackCandles)
if ($Once) { $args += '-Once' }
if ($MaxCycles -gt 0) { $args += @('-MaxCycles', [string]$MaxCycles) }
if ($Json) { $args += '-Json' }
if ($RepoRoot) { $args += @('-RepoRoot', $RepoRoot) }
if ($Python) { $args += @('-Python', $Python) }

& $scriptPath @args
exit $LASTEXITCODE
