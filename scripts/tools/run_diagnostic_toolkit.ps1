param(
    [string]$RepoRoot = '.',
    [string]$Config = 'config\live_controlled_practice.yaml',
    [switch]$DryRun,
    [switch]$ProbeBroker,
    [switch]$ProbeProvider,
    [switch]$StopOnFailure,
    [switch]$IncludeSupportBundle,
    [switch]$AllScopes
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path $RepoRoot).Path
. (Join-Path $RepoRoot 'scripts\scheduler\_bootstrap_python.ps1')

function Invoke-ToolkitStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Args
    )

    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    $moduleArgs = @('--repo-root', $RepoRoot)
    if (-not [string]::IsNullOrWhiteSpace($Config)) {
        $moduleArgs += @('--config', $Config)
    }
    $moduleArgs += $Args

    & {
        Invoke-RepoPythonModule -RepoRoot $RepoRoot -Module 'natbin.runtime_app' -ModuleArgs $moduleArgs
    }
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq $null) {
        $exitCode = 0
    }
    $status = if ($exitCode -eq 0) { 'OK' } else { 'FAIL' }
    Write-Host ("[{0}] {1} (exit={2})" -f $status, $Name, $exitCode) -ForegroundColor ($(if ($exitCode -eq 0) { 'Green' } else { 'Red' }))
    if ($StopOnFailure -and $exitCode -ne 0) {
        throw "Toolkit step failed: $Name"
    }
    return $exitCode
}

$shared = @('--json')
if ($DryRun) { $shared += '--dry-run' }
if ($AllScopes) { $shared += '--all-scopes' }

$summary = [ordered]@{}

$diagArgs = @('diag-suite') + $shared + @('--include-practice', '--include-provider-probe')
if ($ProbeProvider -and -not $DryRun) { $diagArgs += '--active-provider-probe' }
if ($IncludeSupportBundle) { $diagArgs += '--include-support-bundle' }
if ($ProbeBroker -and -not $DryRun) { $diagArgs += '--probe-broker' }
$summary['diag-suite'] = Invoke-ToolkitStep -Name 'diag-suite' -Args $diagArgs

$transportArgs = @('transport-smoke') + $shared + @('--operation', 'powershell_toolkit')
$summary['transport-smoke'] = Invoke-ToolkitStep -Name 'transport-smoke' -Args $transportArgs

$moduleArgs = @('module-smoke') + $shared
$summary['module-smoke'] = Invoke-ToolkitStep -Name 'module-smoke' -Args $moduleArgs

$redactionArgs = @('redaction-audit', '--json')
if ($DryRun) { $redactionArgs += '--dry-run' }
$summary['redaction-audit'] = Invoke-ToolkitStep -Name 'redaction-audit' -Args $redactionArgs

$preflightArgs = @('practice-preflight', '--json')
if ($DryRun) { $preflightArgs += '--dry-run' }
if ($ProbeBroker -and -not $DryRun) { $preflightArgs += '--probe-broker' }
if ($ProbeProvider -and -not $DryRun) { $preflightArgs += '--probe-provider' }
$summary['practice-preflight'] = Invoke-ToolkitStep -Name 'practice-preflight' -Args $preflightArgs

Write-Host "`nResumo do toolkit:" -ForegroundColor Yellow
$summary.GetEnumerator() | ForEach-Object {
    $name = $_.Key
    $code = [int]$_.Value
    $label = if ($code -eq 0) { 'OK' } else { 'FAIL' }
    Write-Host (" - {0}: {1}" -f $name, $label)
}

if (($summary.Values | Where-Object { [int]$_ -ne 0 }).Count -gt 0) {
    exit 2
}
exit 0
