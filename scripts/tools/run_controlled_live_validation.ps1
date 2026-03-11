param(
  [ValidateSet('baseline','practice','real_preflight','real_submit')]
  [string]$Stage = 'baseline',
  [string]$RepoRoot = '.',
  [string]$PythonExe = '.\.venv\Scripts\python.exe',
  [string]$Config = '',
  [string]$Asset = '',
  [int]$IntervalSec = 0,
  [switch]$SkipBaselineTests,
  [switch]$ForceSendAlerts,
  [switch]$AllowLiveSubmit,
  [string]$AckLive = ''
)

$cmd = @(
  $PythonExe,
  'scripts/tools/controlled_live_validation.py',
  '--repo-root', $RepoRoot,
  '--stage', $Stage
)

if ($Config -ne '') {
  $cmd += @('--config', $Config)
}
if ($Asset -ne '') {
  $cmd += @('--asset', $Asset)
}
if ($IntervalSec -gt 0) {
  $cmd += @('--interval-sec', $IntervalSec)
}
if ($SkipBaselineTests) {
  $cmd += '--skip-baseline-tests'
}
if ($ForceSendAlerts) {
  $cmd += '--force-send-alerts'
}
if ($AllowLiveSubmit) {
  $cmd += '--allow-live-submit'
}
if ($AckLive -ne '') {
  $cmd += @('--ack-live', $AckLive)
}

Write-Host ('Running: ' + ($cmd -join ' ')) -ForegroundColor Cyan
& $cmd[0] $cmd[1..($cmd.Length - 1)]
exit $LASTEXITCODE
