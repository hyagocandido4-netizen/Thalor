param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [ValidateSet("quick", "full")]
    [string]$Preset = "full",
    [switch]$IncludeSoak
)

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$args = @(
    "scripts/tools/local_test_suite.py",
    "--repo-root", $repoRoot,
    "--python", $Python,
    "--preset", $Preset
)

if ($IncludeSoak) {
    $args += "--include-soak"
}

& $Python @args
exit $LASTEXITCODE
