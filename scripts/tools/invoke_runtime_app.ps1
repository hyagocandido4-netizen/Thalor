param(
    [string]$RepoRoot = '.',
    [string]$Config = '',
    [string]$Python = '',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RuntimeArgs
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path $RepoRoot).Path
. (Join-Path $RepoRoot 'scripts\scheduler\_bootstrap_python.ps1')

$moduleArgs = @('--repo-root', $RepoRoot)
if (-not [string]::IsNullOrWhiteSpace($Config)) {
    $moduleArgs += @('--config', $Config)
}
$moduleArgs += @($RuntimeArgs | Where-Object { $_ -ne $null })

Invoke-RepoPythonModule -RepoRoot $RepoRoot -Module 'natbin.runtime_app' -ModuleArgs $moduleArgs -Python $Python
