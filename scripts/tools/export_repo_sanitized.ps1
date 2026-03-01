# P26 - Export sanitized repo snapshot (no secrets / no heavy artifacts)
# Usage:
#   pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1
#   pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1 -Out exports\repo_sanitized.zip
#
# This creates a zip good for sharing/debugging. It EXCLUDES:
#   - .env / .env.* (secrets)
#   - .git/
#   - .venv/ (heavy)
#   - data/, runs/, backups/, exports/, temp_snapshot/, __pycache__/
#   - *.bak_* backups
#   - *.sqlite3 + sqlite wal/shm
#
# IMPORTANT: if you already shared a zip containing .env with credentials,
# rotate the credentials immediately.

param(
  [string]$Out = ""
)

$ErrorActionPreference = "Stop"

function New-DirIfMissing([string]$p) {
  if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\.." )).Path

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
if ($Out -eq "") {
  New-DirIfMissing (Join-Path $root "exports")
  $Out = Join-Path $root ("exports\\repo_sanitized_" + $ts + ".zip")
} else {
  # allow relative paths
  if (-not [System.IO.Path]::IsPathRooted($Out)) {
    $Out = Join-Path $root $Out
  }
  New-DirIfMissing ([System.IO.Path]::GetDirectoryName($Out))
}

# Build file list
$files = Get-ChildItem -Path $root -Recurse -File -Force | Where-Object {
  $full = $_.FullName
  $rel  = $full.Substring($root.Length).TrimStart("\\")

  # directories to exclude
  if ($rel -match "^\\.git\\\\") { return $false }
  if ($rel -match "^\\.venv\\\\") { return $false }
  if ($rel -match "^venv\\\\") { return $false }
  if ($rel -match "^data\\\\") { return $false }
  if ($rel -match "^runs\\\\") { return $false }
  if ($rel -match "^backups\\\\") { return $false }
  if ($rel -match "^exports\\\\") { return $false }
  if ($rel -match "^temp_") { return $false }
  if ($rel -match "__pycache__") { return $false }

  # sensitive files
  if ($rel -ieq ".env") { return $false }
  if ($rel -like ".env.*") { return $false }

  # generated backups
  if ($rel -match "\\.bak_") { return $false }
  if ($rel -match "\\.bak\\.") { return $false }

  # sqlite artifacts
  if ($rel -match "\\.sqlite3$") { return $false }
  if ($rel -match "\\.sqlite3-wal$") { return $false }
  if ($rel -match "\\.sqlite3-shm$") { return $false }

  return $true
}

if (Test-Path -LiteralPath $Out) {
  Remove-Item -LiteralPath $Out -Force
}

# Compress-Archive accepts an array of paths
$paths = $files | ForEach-Object { $_.FullName }
Compress-Archive -Path $paths -DestinationPath $Out -CompressionLevel Optimal
Write-Host "[export] OK: $Out"
