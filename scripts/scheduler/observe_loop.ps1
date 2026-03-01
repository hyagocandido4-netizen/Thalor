param(
  [int]$LookbackCandles = 2000,
  # TopK=0 => NAO faz override; usa best.k do config.yaml
  [int]$TopK = 0,
  [switch]$Once,
  [switch]$SkipCollect,
  [switch]$SkipDataset
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $env:GATE_FAIL_CLOSED) { $env:GATE_FAIL_CLOSED = "1" }

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

function Get-IntervalSec {
  # Priority: env override > config.yaml > fallback 300
  if ($env:INTERVAL_SEC) {
    try { return [int]$env:INTERVAL_SEC } catch {}
  }
  if (Test-Path "config.yaml") {
    $m = Select-String -Path "config.yaml" -Pattern "interval_sec\s*:\s*(\d+)" | Select-Object -First 1
    if ($m -and $m.Matches.Count -gt 0) {
      try { return [int]$m.Matches[0].Groups[1].Value } catch {}
    }
  }
  return 300
}

function Get-ConfigAsset {
  if (Test-Path "config.yaml") {
    $m = Select-String -Path "config.yaml" -Pattern 'asset\s*:\s*[''"]?([^''"#]+)' | Select-Object -First 1
    if ($m -and $m.Matches.Count -gt 0) {
      try { return ([string]$m.Matches[0].Groups[1].Value).Trim() } catch {}
    }
  }
  return "UNKNOWN"
}

function Get-SanitizedAssetTag {
  param([string]$Asset)
  $s = [string]$Asset
  if (-not $s) { return "UNKNOWN" }
  $s = [regex]::Replace($s, '[^A-Za-z0-9_-]+', '_')
  $s = $s.Trim('_')
  if (-not $s) { return "UNKNOWN" }
  return $s
}

function Sleep-ToNextCandle {
  param(
    [int]$FallbackSeconds = 310
  )

  $align = $env:SLEEP_ALIGN
  if ($null -eq $align -or "$align" -eq "") { $align = "1" }
  if ($align -eq "0") {
    Start-Sleep -Seconds $FallbackSeconds
    return
  }

  $step = Get-IntervalSec
  $offset = 3
  if ($env:SLEEP_ALIGN_OFFSET_SEC) {
    try { $offset = [int]$env:SLEEP_ALIGN_OFFSET_SEC } catch {}
  }

  # Align by epoch seconds (UTC). Dataset snaps ts by (ts // step) * step.
  $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  $next = ([math]::Floor($now / $step) + 1) * $step
  $sleep = [int](($next - $now) + $offset)
  if ($sleep -lt 1) { $sleep = 1 }
  if ($sleep -gt ($step + $offset + 10)) { $sleep = $FallbackSeconds }

  Start-Sleep -Seconds $sleep
}

# Garante rodar no ROOT do repo (funciona bem no Agendador)
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $root
try {
  # Lock para evitar duas instancias em paralelo (Task Scheduler costuma sobrepor execucoes)
  $runsDir = Join-Path $root "runs"
  New-Item -ItemType Directory -Force $runsDir | Out-Null
  $scopeTag = ("{0}_{1}s" -f (Get-SanitizedAssetTag -Asset (Get-ConfigAsset)), (Get-IntervalSec))
  if (-not $env:MARKET_CONTEXT_PATH) { $env:MARKET_CONTEXT_PATH = Join-Path $runsDir ("market_context_{0}.json" -f $scopeTag) }
  $lockPath = Join-Path $runsDir ("observe_loop_{0}.lock" -f $scopeTag)
  $lockStream = $null
  try {
    $lockStream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
  } catch {
    Write-Host "[LOCK] observe_loop ja esta rodando (lock: $lockPath). Saindo." -ForegroundColor Yellow
    exit 2
  }

  $py = Join-Path $root ".venv\Scripts\python.exe"
  function Invoke-RepoNowPy {
    param([string]$Fmt = "")
    if (!(Test-Path $py) -or !(Test-Path (Join-Path $root "config.yaml"))) { return $null }
    try {
      $code = @"
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import yaml
cfg = yaml.safe_load(Path('config.yaml').read_text(encoding='utf-8')) or {}
tz_name = str((cfg.get('data') or {}).get('timezone') or 'UTC')
now = datetime.now(ZoneInfo(tz_name))
fmt = r'''$Fmt'''.strip()
print(now.strftime(fmt) if fmt else now.isoformat(timespec='seconds'))
"@
      $raw = & $py -c $code
      if ($LASTEXITCODE -eq 0 -and $raw) {
        if ($raw -is [System.Array]) { $raw = ($raw | Select-Object -Last 1) }
        $s = ([string]$raw).Trim()
        if ($s) { return $s }
      }
    } catch {}
    return $null
  }

  function Get-RepoDateTag {
    $v = Invoke-RepoNowPy -Fmt "%Y%m%d"
    if ($v) { return $v }
    return (Get-Date).ToString("yyyyMMdd")
  }

  function Get-RepoTimestamp {
    $v = Invoke-RepoNowPy -Fmt "%Y-%m-%d %H:%M:%S"
    if ($v) { return $v }
    return (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
  }

  Require-Path $py "Nao encontrei .venv. Rode scripts/setup/phase2_bootstrap.ps1."
  Require-Path ".env" "Nao encontrei .env. Copie .env.example para .env."
  Require-Path "config.yaml" "Nao encontrei config.yaml."
  Require-Path "src\natbin\collect_recent.py" "Nao encontrei src\\natbin\\collect_recent.py"
  Require-Path "src\natbin\make_dataset.py" "Nao encontrei src\\natbin\\make_dataset.py"
  Require-Path "src\natbin\observe_signal_topk_perday.py" "Nao encontrei src\\natbin\\observe_signal_topk_perday.py"

  # Log de alinhamento (evita poluir quando chamado com -Once pelo auto-loop)
  if (-not $Once) {
    $step = Get-IntervalSec
    $align = $env:SLEEP_ALIGN
    if ($null -eq $align -or "$align" -eq "") { $align = "1" }
    $offset = 3
    if ($env:SLEEP_ALIGN_OFFSET_SEC) {
      try { $offset = [int]$env:SLEEP_ALIGN_OFFSET_SEC } catch {}
    }
    Write-Host "[P27] interval_sec=$step sleep_align=$align offset=$offset" -ForegroundColor DarkGray
  }

  while ($true) {
    $ts = Get-RepoTimestamp
    Write-Host "`n[$ts] OBSERVE LOOP" -ForegroundColor Cyan

    $env:LOOKBACK_CANDLES = "$LookbackCandles"

    if (-not $SkipCollect) {
      & $py -m natbin.collect_recent
      if ($LASTEXITCODE -ne 0) { throw "collect_recent falhou" }
    } else {
      Write-Host "collect_recent: skipped (-SkipCollect)" -ForegroundColor DarkGray
    }

    if (-not $SkipDataset) {
      & $py -m natbin.make_dataset
      if ($LASTEXITCODE -ne 0) { throw "make_dataset falhou" }
    } else {
      Write-Host "make_dataset: skipped (-SkipDataset)" -ForegroundColor DarkGray
    }

    # So faz override de TOPK_K se voce passar -TopK > 0
    if ($TopK -gt 0) {
      $env:TOPK_K = "$TopK"
      Write-Host "TOPK_K override: $TopK" -ForegroundColor DarkGray
    } else {
      Remove-Item Env:TOPK_K -ErrorAction SilentlyContinue
      Write-Host "TOPK_K: usando best.k do config.yaml (sem override)" -ForegroundColor DarkGray
    }

    $stepNow = Get-IntervalSec
    $assetTagNow = Get-SanitizedAssetTag -Asset (Get-ConfigAsset)
    $env:LIVE_SIGNALS_PATH = Join-Path (Join-Path $root "runs") ("live_signals_v2_{0}_{1}_{2}s.csv" -f (Get-RepoDateTag), $assetTagNow, $stepNow)

    & $py -m natbin.observe_signal_topk_perday
    if ($LASTEXITCODE -ne 0) { throw "observe_signal_topk_perday falhou" }

    if ($Once) { break }
    Sleep-ToNextCandle -FallbackSeconds 310
  }
}
finally {
  if ($lockStream) {
    try { $lockStream.Close() } catch {}
    try { $lockStream.Dispose() } catch {}
  }
  Pop-Location
}
