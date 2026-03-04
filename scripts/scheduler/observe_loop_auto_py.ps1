param(
  [switch]$Once,
  [switch]$QuotaAwareSleep,
  [int]$TopK = 3,
  [int]$LookbackCandles = 2000,
  [int]$MaxCycles = 0,
  [int]$SleepAlignOffsetSec = 3
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$py = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

$args = @('-m', 'natbin.runtime_daemon', '--repo-root', $repo, '--topk', "$TopK", '--lookback-candles', "$LookbackCandles", '--sleep-align-offset-sec', "$SleepAlignOffsetSec")
if ($Once) { $args += '--once' }
if ($QuotaAwareSleep) { $args += '--quota-aware-sleep' }
if ($MaxCycles -gt 0) { $args += @('--max-cycles', "$MaxCycles") }

& $py @args
exit $LASTEXITCODE
