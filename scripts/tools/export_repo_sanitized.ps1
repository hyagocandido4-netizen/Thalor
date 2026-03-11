# Package M1 - Export sanitized repo snapshot (no secrets / no heavy artifacts)
# Usage:
#   pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1
#   pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1 -Out exports\thalor_clean.zip
#   pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1 -DryRun -Json
#
# Delegates to the canonical Python implementation in:
#   scripts/tools/release_bundle.py

param(
  [string]$Out = "",
  [switch]$DryRun,
  [switch]$Json,
  [string]$RootPrefix = ""
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\.." )).Path

function Resolve-Python([string]$RepoRoot) {
  $venvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPy) {
    return $venvPy
  }
  return "python"
}

$python = Resolve-Python $root
$script = Join-Path $root "scripts\tools\release_bundle.py"

$args = @($script, "--repo-root", $root)
if ($Out -ne "") {
  $args += @("--out", $Out)
}
if ($DryRun) {
  $args += "--dry-run"
}
if ($Json) {
  $args += "--json"
}
if ($RootPrefix -ne "") {
  $args += @("--root-prefix", $RootPrefix)
}

Push-Location $root
try {
  & $python @args
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
