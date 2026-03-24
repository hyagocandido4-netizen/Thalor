param(
  [string]$RepoRoot = '.',
  [string]$PythonExe = '.\.venv\Scripts\python.exe',
  [string]$Config = 'config/live_controlled_practice.yaml',
  [int]$SoakCycles = 3,
  [switch]$ForceSoak,
  [switch]$SkipSoak,
  [double]$MaxStakeAmount = 5.0,
  [int]$SoakStaleAfterSec = 0,
  [switch]$ForceSendAlerts,
  [int]$IncidentLimit = 20,
  [int]$WindowHours = 24
)

$cmd = @(
  $PythonExe,
  'scripts/tools/controlled_practice_round.py',
  '--repo-root', $RepoRoot,
  '--config', $Config,
  '--soak-cycles', $SoakCycles,
  '--max-stake-amount', $MaxStakeAmount,
  '--incident-limit', $IncidentLimit,
  '--window-hours', $WindowHours,
  '--json'
)

if ($ForceSoak) {
  $cmd += '--force-soak'
}
if ($SkipSoak) {
  $cmd += '--skip-soak'
}
if ($SoakStaleAfterSec -gt 0) {
  $cmd += @('--soak-stale-after-sec', $SoakStaleAfterSec)
}
if ($ForceSendAlerts) {
  $cmd += '--force-send-alerts'
}

Write-Host ('Running: ' + ($cmd -join ' ')) -ForegroundColor Cyan
& $cmd[0] $cmd[1..($cmd.Length - 1)]
exit $LASTEXITCODE
