$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$env:PYTHONPATH = Join-Path $RepoRoot "src"
python (Join-Path $ScriptDir "apply_rational_workspace_cleanup.py") --repo-root $RepoRoot @args
