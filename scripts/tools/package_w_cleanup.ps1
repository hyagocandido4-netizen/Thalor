param(
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Info($msg) { Write-Host "[Package W] $msg" }

$repoRoot = (Resolve-Path '.').Path

# 1) Remove legacy patches directory from main
$patchDir = Join-Path $repoRoot 'scripts\patches'
if (Test-Path $patchDir) {
  Info "Found legacy directory: scripts/patches"
  if ($DryRun) {
    Info "DryRun: would remove via git rm -r scripts/patches (or Remove-Item if git not available)."
  } else {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
      Info "Running: git rm -r scripts/patches"
      git rm -r scripts/patches
    } else {
      Info "git not found; removing with Remove-Item"
      Remove-Item -Recurse -Force $patchDir
    }
  }
} else {
  Info "scripts/patches not present (OK)"
}

Info "Cleanup done. Suggested next steps:"
Info "  - git status"
Info "  - pytest -q"
Info "  - python -m natbin.leak_check"
Info "  - python scripts/ci/smoke_execution_layer.py"
Info "  - python scripts/ci/smoke_runtime_app.py"
