param(
  [switch]$Once,
  # TopK=0 => NAO faz override; usa best.k do config.yaml
  [int]$TopK = 3,
  [int]$LookbackCandles = 2000,
  # Fallback se sleep_align estiver desligado (SLEEP_ALIGN=0)
  [int]$SleepSeconds = 310,
  [int]$MaxFailures = 5
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --- defaults: meta_iso + TOPK rolling/min-gap ---
$env:META_ISO_ENABLE = "1"
$env:META_ISO_BLEND  = "0.75"

# Evita "not_in_topk_today" quando reinicia no meio do dia + evita cluster de trades.
if (-not $env:TOPK_ROLLING_MINUTES) { $env:TOPK_ROLLING_MINUTES = "360" }   # janela 6h
if (-not $env:TOPK_MIN_GAP_MINUTES) { $env:TOPK_MIN_GAP_MINUTES = "30" }    # cooldown 30min
if (-not $env:TOPK_PACING_ENABLE)   { $env:TOPK_PACING_ENABLE   = "1" }     # distribui o budget ao longo do dia

# Alinha alvo de volume com o K (se nao for definido externamente)
if (-not $env:VOL_TARGET_TRADES_PER_DAY) {
  if ($TopK -gt 0) { $env:VOL_TARGET_TRADES_PER_DAY = "$TopK" } else { $env:VOL_TARGET_TRADES_PER_DAY = "1.0" }
}

# Guardrails (derivados do sweep P14 com meta_iso, payout=0.8)
$env:VOL_SAFE_THR_MIN = "0.02"
$env:VOL_THR_MIN      = "0.02"
$env:VOL_THR_MAX      = "0.12"

# CP defaults (se nao vierem do auto_volume)
if (-not $env:CP_ALPHA) { $env:CP_ALPHA = "0.07" }
if (-not $env:CPREG_ALPHA_START) { $env:CPREG_ALPHA_START = "0.07" }
if (-not $env:CPREG_ALPHA_END)   { $env:CPREG_ALPHA_END   = "0.12" }

# Sleep alignment (para rodar colado nos boundaries do candle)
if (-not $env:SLEEP_ALIGN) { $env:SLEEP_ALIGN = "1" }
if (-not $env:SLEEP_ALIGN_OFFSET_SEC) { $env:SLEEP_ALIGN_OFFSET_SEC = "3" }

if (-not $env:MARKET_CONTEXT_USE_CACHE) { $env:MARKET_CONTEXT_USE_CACHE = "1" }
if (-not $env:MARKET_CONTEXT_MAX_AGE_SEC) { $env:MARKET_CONTEXT_MAX_AGE_SEC = "180" }
if (-not $env:MARKET_CONTEXT_FRESH) { $env:MARKET_CONTEXT_FRESH = "0" }
if (-not $env:MARKET_CONTEXT_AGE_SEC) { $env:MARKET_CONTEXT_AGE_SEC = "" }
if (-not $env:MARKET_CONTEXT_STALE) { $env:MARKET_CONTEXT_STALE = "0" }
if (-not $env:MARKET_CONTEXT_SOURCE) { $env:MARKET_CONTEXT_SOURCE = "" }
if (-not $env:QUOTA_SKIP_SETTLE_ENABLE) { $env:QUOTA_SKIP_SETTLE_ENABLE = "1" }
if (-not $env:RUNTIME_PRUNE_ENABLE) { $env:RUNTIME_PRUNE_ENABLE = "1" }
if (-not $env:RUNTIME_RETENTION_DAYS) { $env:RUNTIME_RETENTION_DAYS = "30" }
if (-not $env:STATE_RECONCILE_ENABLE) { $env:STATE_RECONCILE_ENABLE = "1" }
if (-not $env:STATE_RECONCILE_DAYS) { $env:STATE_RECONCILE_DAYS = "7" }

# Logging (transcript) defaults
if (-not $env:LOOP_LOG_ENABLE) { $env:LOOP_LOG_ENABLE = "1" }
if (-not $env:LOOP_LOG_DIR) { $env:LOOP_LOG_DIR = "runs\logs" }
if (-not $env:LOOP_LOG_RETENTION_DAYS) { $env:LOOP_LOG_RETENTION_DAYS = "14" }
if (-not $env:LOOP_STATUS_ENABLE) { $env:LOOP_STATUS_ENABLE = "1" }
if (-not $env:LEGACY_RUNTIME_CLEANUP_ENABLE) { $env:LEGACY_RUNTIME_CLEANUP_ENABLE = "1" }
if (-not $env:REGIME_MODE_DEFAULT) { $env:REGIME_MODE_DEFAULT = "hard" }
if (-not $env:GATE_FAIL_CLOSED) { $env:GATE_FAIL_CLOSED = "1" }
if (-not $env:MARKET_CONTEXT_FAIL_CLOSED) { $env:MARKET_CONTEXT_FAIL_CLOSED = "1" }

# --- /defaults ---

# [P25] garante execução a partir da raiz do repo (evita falhas no Task Scheduler)
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

function Get-ConfigAssetQuick {
  if (Test-Path "config.yaml") {
    $m = Select-String -Path "config.yaml" -Pattern 'asset\s*:\s*[''"]?([^''"#]+)' | Select-Object -First 1
    if ($m -and $m.Matches.Count -gt 0) {
      try { return ([string]$m.Matches[0].Groups[1].Value).Trim() } catch {}
    }
  }
  return "UNKNOWN"
}

function Get-IntervalSecQuick {
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

function Get-SanitizedAssetTagQuick {
  param([string]$Asset)
  $s = [string]$Asset
  if (-not $s) { return "UNKNOWN" }
  $s = [regex]::Replace($s, '[^A-Za-z0-9_-]+', '_')
  $s = $s.Trim('_')
  if (-not $s) { return "UNKNOWN" }
  return $s
}


function Invoke-RepoNowPy {
  param([string]$Fmt = "")
  $pyPreview = Join-Path $repoRoot ".venv\Scripts\python.exe"
  if (!(Test-Path $pyPreview) -or !(Test-Path (Join-Path $repoRoot "config.yaml"))) { return $null }
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
    $raw = & $pyPreview -c $code
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

function Get-RepoDayStamp {
  $v = Invoke-RepoNowPy -Fmt "%Y-%m-%d"
  if ($v) { return $v }
  return (Get-Date).ToString("yyyy-MM-dd")
}

function Get-RepoTimestamp {
  $v = Invoke-RepoNowPy -Fmt "%Y-%m-%d %H:%M:%S"
  if ($v) { return $v }
  return (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
}

function Get-RepoIsoStamp {
  $v = Invoke-RepoNowPy
  if ($v) { return $v }
  return (Get-Date).ToString("s")
}

function Remove-OldLoopTranscripts {
  if (-not $script:TranscriptLogDir) { return }
  try {
    $cut = (Get-Date).AddDays(-[int]$script:TranscriptRetentionDays)
    $baseName = if ($script:TranscriptBaseName) { $script:TranscriptBaseName } else { "observe_loop_auto" }
    Get-ChildItem -Path $script:TranscriptLogDir -Filter ("{0}_*.log" -f $baseName) -File -ErrorAction SilentlyContinue |
      Where-Object { $_.LastWriteTime -lt $cut } |
      Remove-Item -Force -ErrorAction SilentlyContinue
  } catch {}
}

function Ensure-LoopTranscriptCurrentDay {
  if (-not $logEnabled) { return }
  if (-not $script:TranscriptLogDir) { return }

  $dayTagNow = Get-RepoDateTag
  if (-not $dayTagNow) { $dayTagNow = (Get-Date).ToString("yyyyMMdd") }

  if ($script:TranscriptDayTag -eq $dayTagNow -and $transcriptStarted -and $transcriptPath) { return }

  if ($transcriptStarted) {
    try { Stop-Transcript | Out-Null } catch {}
    $transcriptStarted = $false
  }

  Remove-OldLoopTranscripts

  $baseName = if ($script:TranscriptBaseName) { $script:TranscriptBaseName } else { "observe_loop_auto" }
  $newPath = Join-Path $script:TranscriptLogDir ("{0}_{1}.log" -f $baseName, $dayTagNow)
  try {
    Start-Transcript -Path $newPath -Append | Out-Null
    $transcriptStarted = $true
    $transcriptPath = $newPath
    $script:TranscriptDayTag = $dayTagNow
  } catch {
    $transcriptStarted = $false
    $transcriptPath = $newPath
    try { Write-Host "[LOG] Start-Transcript failed: $($_.Exception.Message)" -ForegroundColor Yellow } catch {}
  }
}

function Resolve-TranscriptPathForStatus {
  if ($transcriptPath) { return ($transcriptPath | AsStr) }
  if (-not $script:TranscriptLogDir) { return "" }
  $baseName = if ($script:TranscriptBaseName) { $script:TranscriptBaseName } else { "observe_loop_auto" }
  try {
    $dayTagNow = Get-RepoDateTag
    if (-not $dayTagNow) { $dayTagNow = (Get-Date).ToString("yyyyMMdd") }
    $candidate = Join-Path $script:TranscriptLogDir ("{0}_{1}.log" -f $baseName, $dayTagNow)
    if (Test-Path $candidate) { return $candidate }
  } catch {}
  try {
    $latest = Get-ChildItem -Path $script:TranscriptLogDir -Filter ("{0}_*.log" -f $baseName) -File -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) { return ($latest.FullName | AsStr) }
  } catch {}
  return ""
}

# Lock de processo (evita duas instancias do auto-loop rodando em paralelo)
$runsDir = Join-Path $repoRoot "runs"
New-Item -ItemType Directory -Force $runsDir | Out-Null
$scopeAsset = Get-ConfigAssetQuick
$scopeInterval = Get-IntervalSecQuick
$scopeTag = ("{0}_{1}s" -f (Get-SanitizedAssetTagQuick -Asset $scopeAsset), $scopeInterval)
$script:RuntimeScopeTag = $scopeTag
$script:TranscriptBaseName = ("observe_loop_auto_{0}" -f $scopeTag)
if (-not $env:MARKET_CONTEXT_PATH) { $env:MARKET_CONTEXT_PATH = Join-Path $runsDir ("market_context_{0}.json" -f $scopeTag) }
if (-not $env:EFFECTIVE_ENV_PATH) { $env:EFFECTIVE_ENV_PATH = Join-Path $runsDir ("effective_env_{0}.json" -f $scopeTag) }
if (-not $env:LOOP_STATUS_PATH) { $env:LOOP_STATUS_PATH = Join-Path $runsDir ("observe_loop_auto_status_{0}.json" -f $scopeTag) }
if (-not $env:AUTO_PARAMS_LEGACY_FALLBACK) { $env:AUTO_PARAMS_LEGACY_FALLBACK = "0" }
$lockPath = Join-Path $runsDir ("observe_loop_auto_{0}.lock" -f $scopeTag)
$lockStream = $null
$transcriptStarted = $false
$transcriptPath = $null
$script:TranscriptDayTag = ""
$script:TranscriptLogDir = ""
$script:TranscriptRetentionDays = 14
$loopStartedAtUtc = [DateTimeOffset]::UtcNow.ToString("o")
$logEnabled = $false
$script:StatusSeq = 0
try {
  $lockStream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
  Write-Host "[LOCK] observe_loop_auto ja esta rodando (lock: $lockPath). Saindo." -ForegroundColor Yellow
  exit 2
}

try {

  # Optional transcript logging (writes console output to a scoped runs/logs/observe_loop_auto_<asset>_<interval>_YYYYMMDD.log)
  $logEnabled = $true
  try {
    $v = [string]$env:LOOP_LOG_ENABLE
    $s = $v.Trim().ToLowerInvariant()
    if ($s -in @("0","false","f","no","n","off","")) { $logEnabled = $false }
  } catch {}

  if ($logEnabled) {
    $logDir = [string]$env:LOOP_LOG_DIR
    if (-not $logDir) { $logDir = "runs\logs" }
    if (-not [System.IO.Path]::IsPathRooted($logDir)) { $logDir = Join-Path $repoRoot $logDir }
    New-Item -ItemType Directory -Force $logDir | Out-Null

    $retDays = 14
    try { if ($env:LOOP_LOG_RETENTION_DAYS) { $retDays = [int]$env:LOOP_LOG_RETENTION_DAYS } } catch {}
    $script:TranscriptLogDir = $logDir
    $script:TranscriptRetentionDays = $retDays
    Ensure-LoopTranscriptCurrentDay
  }

  # [P35-rootfix] Startup hydration is intentionally deferred until AFTER helper/restore functions are defined.
  # Calling Restore-EffectiveEnv / Restore-MarketContextEnv up here looked harmless, but in PowerShell
  # those functions do not exist yet at runtime. The old broad catch{} hid that failure, which is why
  # [P26] showed defaults while later phases (already below the helper definitions) showed the real values.


  # helpers: safe env parsing for PowerShell (pipeline-friendly; accepts "0,07")
  function AsStr {
    param([Parameter(ValueFromPipeline=$true)] $v)
    process { if ($null -eq $v) { "" } else { [string]$v } }
  }

  function AsInt {
    param([Parameter(ValueFromPipeline=$true)] $v)
    process {
      if ($null -eq $v -or "$v" -eq "") { return 0 }
      $s = [string]$v
      $s = $s.Replace(",", ".")
      try { return [int][double]::Parse($s, [System.Globalization.CultureInfo]::InvariantCulture) } catch { return 0 }
    }
  }

  function AsFloat {
    param([Parameter(ValueFromPipeline=$true)] $v)
    process {
      if ($null -eq $v -or "$v" -eq "") { return 0.0 }
      $s = [string]$v
      $s = $s.Replace(",", ".")
      try { return [double]::Parse($s, [System.Globalization.CultureInfo]::InvariantCulture) } catch { return 0.0 }
    }
  }

  function Resolve-RegimeMode {
    param([string]$Value = "")
    $s = ($Value | AsStr).Trim().ToLowerInvariant()
    if ($s -in @("hard","soft","off")) { return $s }
    $d = ($env:REGIME_MODE_DEFAULT | AsStr).Trim().ToLowerInvariant()
    if ($d -notin @("hard","soft","off")) { $d = "hard" }
    return $d
  }

  function Get-ObjProp {
    param(
      [AllowNull()] $Obj,
      [Parameter(Mandatory=$true)][string]$Name,
      $Default = $null
    )
    if ($null -eq $Obj) { return $Default }
    try {
      $prop = $Obj.PSObject.Properties[$Name]
      if ($null -ne $prop) { return $prop.Value }
    } catch {}
    return $Default
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
    if ($script:RuntimeScopeTag -and $scopeAsset) { return $scopeAsset }
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
    $s = ($Asset | AsStr)
    if (-not $s) { return "UNKNOWN" }
    $s = [regex]::Replace($s, '[^A-Za-z0-9_-]+', '_')
    $s = $s.Trim('_')
    if (-not $s) { return "UNKNOWN" }
    return $s
  }

  function Get-AutoParamsStatePath {
    $asset = Get-ConfigAsset
    $iv = Get-IntervalSec
    $tag = Get-SanitizedAssetTag -Asset $asset
    return (Join-Path $repoRoot (Join-Path 'runs' ("auto_params_{0}_{1}s.json" -f $tag, $iv)))
  }

  function Resolve-LegacyCompatPath {
    param(
      [Parameter(Mandatory=$true)][string]$ScopedPath,
      [Parameter(Mandatory=$true)][string]$LegacyFileName
    )
    try {
      if (Test-Path $ScopedPath) { return $ScopedPath }
      $legacyPath = Join-Path $runsDir $LegacyFileName
      if (-not (Test-Path $legacyPath)) { return $ScopedPath }

      $scopedDir = Split-Path -Parent $ScopedPath
      if ($scopedDir) { New-Item -ItemType Directory -Force $scopedDir | Out-Null }
      try {
        Copy-Item -Force $legacyPath $ScopedPath
        return $ScopedPath
      } catch {
        return $legacyPath
      }
    } catch {
      return $ScopedPath
    }
  }

  function Resolve-EffectiveEnvPath {
    $p = [string]$env:EFFECTIVE_ENV_PATH
    if (-not $p) {
      $tag = if ($script:RuntimeScopeTag) { $script:RuntimeScopeTag } else { ("{0}_{1}s" -f (Get-SanitizedAssetTag -Asset (Get-ConfigAsset)), (Get-IntervalSec)) }
      $p = Join-Path $runsDir ("effective_env_{0}.json" -f $tag)
    }
    if (-not [System.IO.Path]::IsPathRooted($p)) { $p = Join-Path $repoRoot $p }
    return (Resolve-LegacyCompatPath -ScopedPath $p -LegacyFileName 'effective_env.json')
  }

  function Resolve-MarketContextPath {
    $p = [string]$env:MARKET_CONTEXT_PATH
    if (-not $p) {
      $tag = if ($script:RuntimeScopeTag) { $script:RuntimeScopeTag } else { ("{0}_{1}s" -f (Get-SanitizedAssetTag -Asset (Get-ConfigAsset)), (Get-IntervalSec)) }
      $p = Join-Path $runsDir ("market_context_{0}.json" -f $tag)
    }
    if (-not [System.IO.Path]::IsPathRooted($p)) { $p = Join-Path $repoRoot $p }
    return (Resolve-LegacyCompatPath -ScopedPath $p -LegacyFileName 'market_context.json')
  }

  function Resolve-LoopStatusPath {
    $p = [string]$env:LOOP_STATUS_PATH
    if (-not $p) { $p = Join-Path $runsDir ("observe_loop_auto_status_{0}.json" -f $script:RuntimeScopeTag) }
    if (-not [System.IO.Path]::IsPathRooted($p)) { $p = Join-Path $repoRoot $p }
    return (Resolve-LegacyCompatPath -ScopedPath $p -LegacyFileName 'observe_loop_auto_status.json')
  }

  function Coalesce-Str {
    param($Primary, $Fallback)
    $s = ($Primary | AsStr)
    if ($s) { return $s }
    return ($Fallback | AsStr)
  }

  function Get-NextWakeUtcString {
    param([int]$SleepSec = 0)
    if ($SleepSec -le 0) { return "" }
    try {
      return ([DateTimeOffset]::UtcNow.AddSeconds([double]$SleepSec).ToString("o"))
    } catch {
      return ""
    }
  }

  function Try-ParseDateTimeOffsetLoose {
    param([string]$Value)
    if (-not $Value) { return $null }

    $dto = [DateTimeOffset]::MinValue
    try {
      if ([DateTimeOffset]::TryParse($Value, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AllowWhiteSpaces, [ref]$dto)) {
        return [DateTimeOffset]$dto
      }
    } catch {}

    $dto = [DateTimeOffset]::MinValue
    try {
      if ([DateTimeOffset]::TryParse($Value, [System.Globalization.CultureInfo]::CurrentCulture, [System.Globalization.DateTimeStyles]::AllowWhiteSpaces, [ref]$dto)) {
        return [DateTimeOffset]$dto
      }
    } catch {}

    return $null
  }

  function Get-MarketContextSnapshot {
    param(
      [string]$Path = "",
      [int]$MaxAgeSec = 180
    )
    if (-not $Path) { $Path = Resolve-MarketContextPath }
    if (!(Test-Path $Path)) { return $null }
    try {
      $o = Get-Content $Path -Raw | ConvertFrom-Json
      $mcOpen = (Get-ObjProp -Obj $o -Name "market_open" | AsStr).Trim().ToLowerInvariant()
      if ($mcOpen -in @("1","true","t","yes","y","on")) { $mcOpen = "1" }
      elseif ($mcOpen -in @("0","false","f","no","n","off")) { $mcOpen = "0" }

      $mcAtRaw = (Get-ObjProp -Obj $o -Name "at_utc" | AsStr)
      $mcAt = $mcAtRaw
      $ageSec = $null
      $ageSource = ""
      $lastTs = (Get-ObjProp -Obj $o -Name "last_candle_ts" | AsStr)
      $lastCandleDtUtc = ""

      if ($lastTs) {
        try {
          $lastTsInt = [int64]$lastTs
          $lastCandleDtUtc = [DateTimeOffset]::FromUnixTimeSeconds($lastTsInt).ToString("o")
          $ageSec = [int]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $lastTsInt)
          if ($ageSec -lt 0) { $ageSec = 0 }
          $ageSource = "last_candle_ts"
        } catch {
          $ageSec = $null
        }
      }

      if ($mcAtRaw) {
        $dto = Try-ParseDateTimeOffsetLoose -Value $mcAtRaw
        if ($null -ne $dto) {
          $mcAt = $dto.ToUniversalTime().ToString("o")
          if ($null -eq $ageSec) {
            try {
              $ageSec = [int]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $dto.ToUnixTimeSeconds())
              if ($ageSec -lt 0) { $ageSec = 0 }
              $ageSource = "at_utc"
            } catch {
              $ageSec = $null
            }
          }
        }
      }

      $stale = $false
      $isFresh = $false
      if ($null -ne $ageSec) {
        try {
          if ([int]$ageSec -le [int]$MaxAgeSec) { $isFresh = $true }
          if ([int]$ageSec -gt [int]$MaxAgeSec) { $stale = $true }
        } catch {}
      }

      $mcSource = (Get-ObjProp -Obj $o -Name "source" | AsStr)
      if (-not $mcSource) { $mcSource = "cache" }

      $ageStr = ""
      if ($null -ne $ageSec) { $ageStr = [string]$ageSec }
      $freshStr = if ($isFresh) { "1" } else { "0" }
      $staleStr = if ($stale) { "1" } else { "0" }

      return [pscustomobject]([ordered]@{
        asset = (Get-ObjProp -Obj $o -Name "asset" | AsStr)
        market_open = $mcOpen
        payout = (Get-ObjProp -Obj $o -Name "payout" | AsStr)
        payout_source = (Get-ObjProp -Obj $o -Name "payout_source" | AsStr)
        open_source = (Get-ObjProp -Obj $o -Name "open_source" | AsStr)
        source = $mcSource
        at_utc = $mcAt
        last_candle_ts = $lastTs
        last_candle_dt_utc = $lastCandleDtUtc
        age_sec = $ageStr
        age_source = $ageSource
        max_age_sec = [string]$MaxAgeSec
        fresh = $freshStr
        stale = $staleStr
      })
    } catch {
      return $null
    }
  }

  function Get-StatusMarketContextSnapshot {
    $maxAge = 180
    try { if ($env:MARKET_CONTEXT_MAX_AGE_SEC) { $maxAge = [int]$env:MARKET_CONTEXT_MAX_AGE_SEC } } catch {}
    return (Get-MarketContextSnapshot -Path (Resolve-MarketContextPath) -MaxAgeSec $maxAge)
  }

  function Save-LoopStatus {
    param(
      [string]$Phase,
      [string]$State = "ok",
      [string]$Message = "",
      $Quota = $null,
      $EvalGap = $null,
      [int]$SleepSec = -1,
      [string]$NextAt = "",
      [hashtable]$Extra = $null
    )

    $enabled = (($env:LOOP_STATUS_ENABLE | AsStr).ToLowerInvariant()) -notin @("0","false","f","no","n","off","")
    if (-not $enabled) { return }

    try {
      $statusPath = Resolve-LoopStatusPath
      $statusDir = Split-Path -Parent $statusPath
      if ($statusDir) { New-Item -ItemType Directory -Force $statusDir | Out-Null }

      $prevStatus = $null
      if ($Phase -eq "stopped" -and (Test-Path $statusPath)) {
        try { $prevStatus = Get-Content $statusPath -Raw | ConvertFrom-Json } catch {}
      }

      $script:StatusSeq = [int]$script:StatusSeq + 1

      $qKind = (Get-ObjProp -Obj $Quota -Name "Kind" | AsStr)
      $qDay = (Get-ObjProp -Obj $Quota -Name "Day" | AsStr)
      $qCount = (Get-ObjProp -Obj $Quota -Name "Count" | AsInt)
      $qAllowed = (Get-ObjProp -Obj $Quota -Name "Allowed" | AsInt)
      $qTopK = (Get-ObjProp -Obj $Quota -Name "TopK" | AsInt)
      if ($SleepSec -lt 0) { $SleepSec = (Get-ObjProp -Obj $Quota -Name "SleepSec" | AsInt) }
      if (-not $NextAt) { $NextAt = (Get-ObjProp -Obj $Quota -Name "NextAt" | AsStr) }
      $nextWakeUtc = Get-NextWakeUtcString -SleepSec ([int]$SleepSec)

      $gDay = (Get-ObjProp -Obj $EvalGap -Name "Day" | AsStr)
      $gCount = (Get-ObjProp -Obj $EvalGap -Name "Count" | AsInt)
      $gEval = (Get-ObjProp -Obj $EvalGap -Name "Eval" | AsInt)
      $gPending = (Get-ObjProp -Obj $EvalGap -Name "Pending" | AsInt)

      if ($prevStatus) {
        $prevQuota = Get-ObjProp -Obj $prevStatus -Name "quota"
        if (-not $qKind) { $qKind = (Get-ObjProp -Obj $prevQuota -Name "kind" | AsStr) }
        if (-not $qDay) { $qDay = (Get-ObjProp -Obj $prevQuota -Name "day" | AsStr) }
        if ($qCount -eq 0) { $qCount = (Get-ObjProp -Obj $prevQuota -Name "executed" | AsInt) }
        if ($qAllowed -eq 0) { $qAllowed = (Get-ObjProp -Obj $prevQuota -Name "allowed" | AsInt) }
        if ($qTopK -eq 0) { $qTopK = (Get-ObjProp -Obj $prevQuota -Name "topk" | AsInt) }
        if ($SleepSec -lt 0 -or $SleepSec -eq 0) { $SleepSec = (Get-ObjProp -Obj $prevQuota -Name "sleep_sec" | AsInt) }
        if (-not $NextAt) { $NextAt = (Get-ObjProp -Obj $prevQuota -Name "next_at" | AsStr) }

        $prevSettle = Get-ObjProp -Obj $prevStatus -Name "settle"
        if (-not $gDay) { $gDay = (Get-ObjProp -Obj $prevSettle -Name "day" | AsStr) }
        if ($gCount -eq 0) { $gCount = (Get-ObjProp -Obj $prevSettle -Name "executed" | AsInt) }
        if ($gEval -eq 0) { $gEval = (Get-ObjProp -Obj $prevSettle -Name "eval" | AsInt) }
        if ($gPending -eq 0) { $gPending = (Get-ObjProp -Obj $prevSettle -Name "pending" | AsInt) }
      }

      $statusEnvThreshold = ($env:THRESHOLD | AsStr)
      $statusEnvAlphaStart = ($env:CPREG_ALPHA_START | AsStr)
      $statusEnvAlphaEnd = ($env:CPREG_ALPHA_END | AsStr)
      $statusEnvCpAlpha = ($env:CP_ALPHA | AsStr)
      $statusEnvBlend = ($env:META_ISO_BLEND | AsStr)
      $statusEnvRegime = (Resolve-RegimeMode ($env:REGIME_MODE | AsStr) | AsStr)
      $statusEnvGateFailClosed = ($env:GATE_FAIL_CLOSED | AsStr)
      $statusEnvMarketContextFailClosed = ($env:MARKET_CONTEXT_FAIL_CLOSED | AsStr)
      $statusEnvPayout = ($env:PAYOUT | AsStr)
      $statusEnvMarketOpen = ($env:MARKET_OPEN | AsStr)
      $statusEnvCtxFresh = ($env:MARKET_CONTEXT_FRESH | AsStr)
      $statusEnvCtxAge = ($env:MARKET_CONTEXT_AGE_SEC | AsStr)
      $statusEnvCtxSource = ($env:MARKET_CONTEXT_SOURCE | AsStr)
      $statusEnvCtxStale = ($env:MARKET_CONTEXT_STALE | AsStr)
      $ctxSnap = Get-StatusMarketContextSnapshot
      if ($ctxSnap) {
        $statusEnvPayout = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "payout") $statusEnvPayout
        $statusEnvMarketOpen = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "market_open") $statusEnvMarketOpen
        $statusEnvCtxFresh = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "fresh") $statusEnvCtxFresh
        $statusEnvCtxAge = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "age_sec") $statusEnvCtxAge
        $statusEnvCtxSource = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "source") $statusEnvCtxSource
        $statusEnvCtxStale = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "stale") $statusEnvCtxStale
      }

      $budgetLeftNow = 0
      $budgetLeftTotal = 0
      $quotaAllowedDisplay = $qAllowed
      if ($qKind -eq "max_k_reached_today" -and $qTopK -gt 0) { $quotaAllowedDisplay = $qTopK }
      try { $budgetLeftNow = [Math]::Max(0, [int]$qAllowed - [int]$qCount) } catch { $budgetLeftNow = 0 }
      try { $budgetLeftTotal = [Math]::Max(0, [int]$qTopK - [int]$qCount) } catch { $budgetLeftTotal = 0 }

      $sleepReason = ""
      if ($qKind) { $sleepReason = $qKind }
      elseif ($Phase -eq "sleep") { $sleepReason = ($Message | AsStr) }

      $payload = [ordered]@{
        at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        loop_started_utc = $loopStartedAtUtc
        pid = $PID
        once = [bool]$Once
        phase = $Phase
        state = $State
        message = $Message
        sleep_reason = $sleepReason
        next_wake_utc = ($nextWakeUtc | AsStr)
        seq = [int]$script:StatusSeq
        asset = (Get-ConfigAsset)
        interval_sec = (Get-IntervalSec)
        topk_k = [int]$TopK
        lookback_candles = [int]$LookbackCandles
        env = [ordered]@{
          THRESHOLD = $statusEnvThreshold
          CPREG_ALPHA_START = $statusEnvAlphaStart
          CPREG_ALPHA_END = $statusEnvAlphaEnd
          CP_ALPHA = $statusEnvCpAlpha
          META_ISO_BLEND = $statusEnvBlend
          REGIME_MODE = $statusEnvRegime
          GATE_FAIL_CLOSED = $statusEnvGateFailClosed
          MARKET_CONTEXT_FAIL_CLOSED = $statusEnvMarketContextFailClosed
          PAYOUT = $statusEnvPayout
          MARKET_OPEN = $statusEnvMarketOpen
          MARKET_CONTEXT_FRESH = $statusEnvCtxFresh
          MARKET_CONTEXT_AGE_SEC = $statusEnvCtxAge
          MARKET_CONTEXT_SOURCE = $statusEnvCtxSource
          MARKET_CONTEXT_STALE = $statusEnvCtxStale
          TOPK_ROLLING_MINUTES = ($env:TOPK_ROLLING_MINUTES | AsStr)
          TOPK_MIN_GAP_MINUTES = ($env:TOPK_MIN_GAP_MINUTES | AsStr)
          TOPK_PACING_ENABLE = ($env:TOPK_PACING_ENABLE | AsStr)
          VOL_TARGET_TRADES_PER_DAY = ($env:VOL_TARGET_TRADES_PER_DAY | AsStr)
        }
        quota = [ordered]@{
          kind = $qKind
          day = $qDay
          executed = $qCount
          allowed = $quotaAllowedDisplay
          allowed_now = $qAllowed
          allowed_total = $qTopK
          topk = $qTopK
          budget_left_now = $budgetLeftNow
          budget_left_total = $budgetLeftTotal
          next_at = ($NextAt | AsStr)
          sleep_sec = ([int]$SleepSec)
          next_wake_utc = ($nextWakeUtc | AsStr)
        }
        settle = [ordered]@{
          day = $gDay
          executed = $gCount
          eval = $gEval
          pending = $gPending
        }
        market_context = $ctxSnap
        transcript_path = (Resolve-TranscriptPathForStatus)
        log_enabled = [bool]$logEnabled
      }

      if ($prevStatus) {
        $payload['last_phase'] = (Get-ObjProp -Obj $prevStatus -Name 'phase' | AsStr)
        $payload['last_state'] = (Get-ObjProp -Obj $prevStatus -Name 'state' | AsStr)
        $payload['last_message'] = (Get-ObjProp -Obj $prevStatus -Name 'message' | AsStr)
      }

      if ($Extra) {
        foreach ($k in $Extra.Keys) {
          $payload[$k] = $Extra[$k]
        }
      }

      $json = $payload | ConvertTo-Json -Depth 8
      $tmp = "$statusPath.tmp"
      [System.IO.File]::WriteAllText($tmp, $json, [System.Text.UTF8Encoding]::new($false))
      Move-Item -Force $tmp $statusPath
    } catch {
      # status write nunca deve derrubar o loop
    }
  }

  function Finalize-LoopStatusStopped {
    $enabled = (($env:LOOP_STATUS_ENABLE | AsStr).ToLowerInvariant()) -notin @("0","false","f","no","n","off","")
    if (-not $enabled) { return }
    try {
      $statusPath = Resolve-LoopStatusPath
      $statusDir = Split-Path -Parent $statusPath
      if ($statusDir) { New-Item -ItemType Directory -Force $statusDir | Out-Null }

      $prevStatus = $null
      if (Test-Path $statusPath) {
        try { $prevStatus = Get-Content $statusPath -Raw | ConvertFrom-Json } catch {}
      }
      $prevEnv = $null
      $prevQuota = $null
      $prevSettle = $null
      if ($prevStatus) {
        $prevEnv = Get-ObjProp -Obj $prevStatus -Name "env"
        $prevQuota = Get-ObjProp -Obj $prevStatus -Name "quota"
        $prevSettle = Get-ObjProp -Obj $prevStatus -Name "settle"
      }
      $ctxSnap = Get-StatusMarketContextSnapshot

      $prevSleepReason = ""
      $prevNextWakeUtc = ""
      if ($prevStatus) {
        $prevSleepReason = (Get-ObjProp -Obj $prevStatus -Name "sleep_reason" | AsStr)
        $prevNextWakeUtc = (Get-ObjProp -Obj $prevStatus -Name "next_wake_utc" | AsStr)
      }

      $payload = [ordered]@{
        at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        loop_started_utc = $loopStartedAtUtc
        pid = $PID
        once = [bool]$Once
        phase = "stopped"
        state = "stopped"
        message = "loop_exit"
        sleep_reason = $prevSleepReason
        next_wake_utc = $prevNextWakeUtc
        seq = [int]$script:StatusSeq + 1
        asset = (Get-ConfigAsset)
        interval_sec = (Get-IntervalSec)
        topk_k = [int]$TopK
        lookback_candles = [int]$LookbackCandles
        env = [ordered]@{
          THRESHOLD = Coalesce-Str ($env:THRESHOLD | AsStr) (Get-ObjProp -Obj $prevEnv -Name "THRESHOLD")
          CPREG_ALPHA_START = Coalesce-Str ($env:CPREG_ALPHA_START | AsStr) (Get-ObjProp -Obj $prevEnv -Name "CPREG_ALPHA_START")
          CPREG_ALPHA_END = Coalesce-Str ($env:CPREG_ALPHA_END | AsStr) (Get-ObjProp -Obj $prevEnv -Name "CPREG_ALPHA_END")
          CP_ALPHA = Coalesce-Str ($env:CP_ALPHA | AsStr) (Get-ObjProp -Obj $prevEnv -Name "CP_ALPHA")
          META_ISO_BLEND = Coalesce-Str ($env:META_ISO_BLEND | AsStr) (Get-ObjProp -Obj $prevEnv -Name "META_ISO_BLEND")
          REGIME_MODE = Coalesce-Str ($env:REGIME_MODE | AsStr) (Get-ObjProp -Obj $prevEnv -Name "REGIME_MODE")
          PAYOUT = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "payout") (Coalesce-Str ($env:PAYOUT | AsStr) (Get-ObjProp -Obj $prevEnv -Name "PAYOUT"))
          MARKET_OPEN = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "market_open") (Coalesce-Str ($env:MARKET_OPEN | AsStr) (Get-ObjProp -Obj $prevEnv -Name "MARKET_OPEN"))
          MARKET_CONTEXT_FRESH = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "fresh") (Coalesce-Str ($env:MARKET_CONTEXT_FRESH | AsStr) (Get-ObjProp -Obj $prevEnv -Name "MARKET_CONTEXT_FRESH"))
          MARKET_CONTEXT_AGE_SEC = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "age_sec") (Coalesce-Str ($env:MARKET_CONTEXT_AGE_SEC | AsStr) (Get-ObjProp -Obj $prevEnv -Name "MARKET_CONTEXT_AGE_SEC"))
          MARKET_CONTEXT_SOURCE = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "source") (Coalesce-Str ($env:MARKET_CONTEXT_SOURCE | AsStr) (Get-ObjProp -Obj $prevEnv -Name "MARKET_CONTEXT_SOURCE"))
          MARKET_CONTEXT_STALE = Coalesce-Str (Get-ObjProp -Obj $ctxSnap -Name "stale") (Coalesce-Str ($env:MARKET_CONTEXT_STALE | AsStr) (Get-ObjProp -Obj $prevEnv -Name "MARKET_CONTEXT_STALE"))
          TOPK_ROLLING_MINUTES = Coalesce-Str ($env:TOPK_ROLLING_MINUTES | AsStr) (Get-ObjProp -Obj $prevEnv -Name "TOPK_ROLLING_MINUTES")
          TOPK_MIN_GAP_MINUTES = Coalesce-Str ($env:TOPK_MIN_GAP_MINUTES | AsStr) (Get-ObjProp -Obj $prevEnv -Name "TOPK_MIN_GAP_MINUTES")
          TOPK_PACING_ENABLE = Coalesce-Str ($env:TOPK_PACING_ENABLE | AsStr) (Get-ObjProp -Obj $prevEnv -Name "TOPK_PACING_ENABLE")
          VOL_TARGET_TRADES_PER_DAY = Coalesce-Str ($env:VOL_TARGET_TRADES_PER_DAY | AsStr) (Get-ObjProp -Obj $prevEnv -Name "VOL_TARGET_TRADES_PER_DAY")
        }
        quota = $prevQuota
        settle = $prevSettle
        market_context = $ctxSnap
        transcript_path = (Resolve-TranscriptPathForStatus)
        log_enabled = [bool]$logEnabled
      }
      if ($prevStatus) {
        $payload['last_phase'] = (Get-ObjProp -Obj $prevStatus -Name 'phase' | AsStr)
        $payload['last_state'] = (Get-ObjProp -Obj $prevStatus -Name 'state' | AsStr)
        $payload['last_message'] = (Get-ObjProp -Obj $prevStatus -Name 'message' | AsStr)
      }

      $json = $payload | ConvertTo-Json -Depth 10
      $tmp = "$statusPath.tmp"
      [System.IO.File]::WriteAllText($tmp, $json, [System.Text.UTF8Encoding]::new($false))
      Move-Item -Force $tmp $statusPath
      $script:StatusSeq = [int]$payload.seq
    } catch {
      try { Write-Host "[P35] WARN: Finalize-LoopStatusStopped falhou: $($_.Exception.Message)" -ForegroundColor DarkYellow } catch {}
    }
  }

  function Apply-MarketContextObject {
    param(
      [Parameter(Mandatory=$true)] $Obj,
      [string]$SourceTag = ""
    )

    $v = Get-ObjProp -Obj $Obj -Name "payout"
    if ($null -ne $v -and (AsStr $v) -ne "") { $env:PAYOUT = [string]$v }

    $mv = (Get-ObjProp -Obj $Obj -Name "market_open" | AsStr).ToLowerInvariant()
    if ($mv -in @("0","false","f","no","n","off")) {
      $env:MARKET_OPEN = "0"
    } elseif ($mv -ne "") {
      $env:MARKET_OPEN = "1"
    } elseif (-not $env:MARKET_OPEN) {
      $env:MARKET_OPEN = "1"
    }

    $payoutSrc = (Get-ObjProp -Obj $Obj -Name "payout_source" | AsStr)
    $openSrc = (Get-ObjProp -Obj $Obj -Name "open_source" | AsStr)
    $assetCtx = (Get-ObjProp -Obj $Obj -Name "asset" | AsStr)
    $freshValue = (Get-ObjProp -Obj $Obj -Name "fresh" | AsStr)
    if (-not $freshValue) { $freshValue = "1" }
    $ageValue = (Get-ObjProp -Obj $Obj -Name "age_sec" | AsStr)
    if (-not $ageValue) { $ageValue = "0" }
    $staleValue = (Get-ObjProp -Obj $Obj -Name "stale" | AsStr)
    if (-not $staleValue) { $staleValue = "0" }
    $env:MARKET_CONTEXT_FRESH = $freshValue
    $env:MARKET_CONTEXT_AGE_SEC = $ageValue
    $env:MARKET_CONTEXT_STALE = $staleValue
    $srcTagValue = (Get-ObjProp -Obj $Obj -Name "source" | AsStr)
    if ($SourceTag) { $srcTagValue = [string]$SourceTag }
    $env:MARKET_CONTEXT_SOURCE = $srcTagValue
    $extra = ""
    if ($SourceTag) { $extra = " source=$SourceTag" }
    Write-Host "[P30] asset=$assetCtx market_open=$($env:MARKET_OPEN) payout=$($env:PAYOUT) payout_src=$payoutSrc open_src=$openSrc fresh=$($env:MARKET_CONTEXT_FRESH) age=$($env:MARKET_CONTEXT_AGE_SEC)s stale=$($env:MARKET_CONTEXT_STALE)$extra" -ForegroundColor DarkGray
  }

  function Try-LoadFreshMarketContext {
    param(
      [Parameter(Mandatory=$true)][string]$Path,
      [int]$MaxAgeSec = 180
    )

    $snap = Get-MarketContextSnapshot -Path $Path -MaxAgeSec $MaxAgeSec
    if ($null -eq $snap) { return $false }

    $env:MARKET_CONTEXT_FRESH = (Get-ObjProp -Obj $snap -Name "fresh" | AsStr)
    $env:MARKET_CONTEXT_AGE_SEC = (Get-ObjProp -Obj $snap -Name "age_sec" | AsStr)
    $env:MARKET_CONTEXT_SOURCE = (Get-ObjProp -Obj $snap -Name "source" | AsStr)
    $env:MARKET_CONTEXT_STALE = (Get-ObjProp -Obj $snap -Name "stale" | AsStr)

    if ((Get-ObjProp -Obj $snap -Name "fresh" | AsStr) -ne "1") { return $false }

    Apply-MarketContextObject -Obj $snap -SourceTag ("cache age={0}s" -f (Get-ObjProp -Obj $snap -Name "age_sec" | AsStr))
    return $true
  }

  function Invoke-MarketContext {
    $ctxPath = Resolve-MarketContextPath
    $useCache = (($env:MARKET_CONTEXT_USE_CACHE | AsStr).ToLowerInvariant()) -notin @("0","false","f","no","n","off","")
    $maxAge = 180
    if ($env:MARKET_CONTEXT_MAX_AGE_SEC) {
      try { $maxAge = [int]$env:MARKET_CONTEXT_MAX_AGE_SEC } catch {}
    }

    try {
      if ($useCache -and (Try-LoadFreshMarketContext -Path $ctxPath -MaxAgeSec $maxAge)) {
        Save-EffectiveEnv
        return
      }

      $raw = & $py -m natbin.refresh_market_context
      if ($LASTEXITCODE -ne 0) { throw "refresh_market_context exit=$LASTEXITCODE" }

      $line = $null
      if ($raw -is [System.Array]) {
        $line = ($raw | Where-Object { $_ -and $_.Trim().StartsWith("{") -and $_.Trim().EndsWith("}") } | Select-Object -Last 1)
      } else {
        $s = [string]$raw
        if ($s.Trim().StartsWith("{") -and $s.Trim().EndsWith("}")) { $line = $s.Trim() }
      }
      if (-not $line -and (Test-Path $ctxPath)) {
        $line = Get-Content $ctxPath -Raw
      }
      if (-not $line) { throw "refresh_market_context nao retornou JSON" }

      $o = $line | ConvertFrom-Json
      Apply-MarketContextObject -Obj $o -SourceTag "refresh"
      Save-EffectiveEnv
    } catch {
      if (-not $env:PAYOUT) { $env:PAYOUT = "0.8" }
      if (-not $env:MARKET_OPEN) { $env:MARKET_OPEN = "1" }
      if (-not $env:MARKET_CONTEXT_FRESH) { $env:MARKET_CONTEXT_FRESH = "0" }
      if (-not $env:MARKET_CONTEXT_AGE_SEC) { $env:MARKET_CONTEXT_AGE_SEC = "" }
      if (-not $env:MARKET_CONTEXT_STALE) { $env:MARKET_CONTEXT_STALE = "1" }
      if (-not $env:MARKET_CONTEXT_SOURCE) { $env:MARKET_CONTEXT_SOURCE = "fallback" }
      Save-EffectiveEnv
      Write-Host "[P30] WARN: refresh_market_context falhou: $($_.Exception.Message); fallback PAYOUT=$($env:PAYOUT) MARKET_OPEN=$($env:MARKET_OPEN) fresh=$($env:MARKET_CONTEXT_FRESH) stale=$($env:MARKET_CONTEXT_STALE)" -ForegroundColor DarkYellow
    }
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

  function Sleep-QuotaAware {
    param(
      [Parameter(Mandatory=$true)]$QuotaDecision,
      [int]$FallbackSeconds = 310
    )

    $sleepSec = 0
    try { $sleepSec = [int]$QuotaDecision.SleepSec } catch { $sleepSec = 0 }
    if ($sleepSec -le 0) {
      Sleep-ToNextCandle -FallbackSeconds $FallbackSeconds
      return
    }

    $kind = (Get-ObjProp -Obj $QuotaDecision -Name "Kind" | AsStr)
    $nextAt = (Get-ObjProp -Obj $QuotaDecision -Name "NextAt" | AsStr)
    $extra = ""
    if ($nextAt) { $extra = " next_at=$nextAt" }
    Write-Host "[P31b] quota_sleep kind=$kind sleep_s=$sleepSec$extra" -ForegroundColor DarkGray
    Start-Sleep -Seconds $sleepSec
  }


  $py = ".\.venv\Scripts\python.exe"
  if (!(Test-Path $py)) { throw "Python venv nao encontrado em $py" }

  $loop = ".\scripts\scheduler\observe_loop.ps1"
  if (!(Test-Path $loop)) { throw "observe_loop.ps1 nao encontrado em $loop" }

  $refresh = ".\src\natbin\refresh_daily_summary.py"
  if (!(Test-Path $refresh)) { throw "refresh_daily_summary.py nao encontrado em $refresh" }

  function Get-QuotaStatus {
    param([int]$K)
    try {
      $raw = & $py -c @"
from pathlib import Path
import math
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import yaml

cfg = yaml.safe_load(Path('config.yaml').read_text(encoding='utf-8'))
data = cfg.get('data') or {}
asset = str(data.get('asset', 'UNKNOWN'))
interval_sec = int(data.get('interval_sec') or 300)
tz = ZoneInfo(str(data.get('timezone', 'UTC')))
now = datetime.now(tz)
day = now.strftime('%Y-%m-%d')

def count_from_state(asset: str, interval_sec: int, day: str) -> int:
    p = Path('runs') / 'live_topk_state.sqlite3'
    if not p.exists():
        return 0
    con = sqlite3.connect(str(p))
    try:
        info = con.execute('PRAGMA table_info(executed)').fetchall()
        cols = {r[1] for r in info}
        if {'asset', 'day'} - cols:
            return 0
        if 'interval_sec' in cols:
            cur = con.execute('SELECT COUNT(*) FROM executed WHERE asset=? AND interval_sec=? AND day=?', (asset, interval_sec, day))
        else:
            cur = con.execute('SELECT COUNT(*) FROM executed WHERE asset=? AND day=?', (asset, day))
        return int(cur.fetchone()[0] or 0)
    except Exception:
        return 0
    finally:
        con.close()

def count_from_signals(asset: str, interval_sec: int, day: str) -> int:
    p = Path('runs') / 'live_signals.sqlite3'
    if not p.exists():
        return 0
    con = sqlite3.connect(str(p))
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'signals_v2' not in tables:
            return 0
        info = con.execute('PRAGMA table_info(signals_v2)').fetchall()
        cols = {r[1] for r in info}
        if {'asset', 'day', 'action'} - cols:
            return 0
        where = ["day=?", "asset=?", "action IN ('CALL','PUT')"]
        params = [day, asset]
        if 'interval_sec' in cols:
            where.append('interval_sec=?')
            params.append(interval_sec)
        cur = con.execute(f"SELECT COUNT(*) FROM signals_v2 WHERE {' AND '.join(where)}", tuple(params))
        return int(cur.fetchone()[0] or 0)
    except Exception:
        return 0
    finally:
        con.close()

count_state = count_from_state(asset, interval_sec, day)
count_signals = count_from_signals(asset, interval_sec, day)
count = max(int(count_state), int(count_signals))

topk = int($K)
pacing_enabled = str("$($env:TOPK_PACING_ENABLE)").strip().lower() not in ('0','false','f','no','n','off','')
pacing_allowed = int(topk)
next_hhmm = ''
if pacing_enabled and topk > 1:
    sec_of_day = int(now.hour) * 3600 + int(now.minute) * 60 + int(now.second)
    frac_day = min(1.0, max(0.0, float(sec_of_day) / 86400.0))
    pacing_allowed = min(int(topk), max(1, int(math.floor(float(topk) * frac_day)) + 1))
    if count >= pacing_allowed and count < topk:
        need_frac = min(1.0, max(0.0, float(count) / float(topk)))
        next_sec = int(math.ceil(86400.0 * need_frac))
        day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_dt = day0 + timedelta(seconds=next_sec)
        next_hhmm = next_dt.strftime('%H:%M')

sleep_sec = 0
if count >= topk:
    next_dt = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    sleep_sec = max(1, int((next_dt - now).total_seconds()) + 5)
elif pacing_enabled and count >= pacing_allowed and count < topk:
    if next_hhmm:
        sleep_sec = max(1, int((next_dt - now).total_seconds()) + 5)

print(f"{asset}|{day}|{count}|{pacing_allowed}|{topk}|{next_hhmm}|{sleep_sec}")
"@
      if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
      if ($raw -is [System.Array]) { return ($raw | Select-Object -Last 1) }
      return [string]$raw
    } catch {
      return $null
    }
  }

  function Get-QuotaSkipDecision {
    param([int]$K)

    $out = [ordered]@{
      Skip = $false
      Kind = ""
      Message = ""
      Asset = ""
      Day = ""
      Count = 0
      Allowed = 0
      TopK = $K
      NextAt = ""
      SleepSec = 0
    }

    if ($K -le 0) { return [pscustomobject]$out }
    if (($env:FORCE_OBSERVE_AFTER_MAXK | AsStr) -eq "1") { return [pscustomobject]$out }

    $quotaLine = Get-QuotaStatus -K $K
    if (-not $quotaLine) { return [pscustomobject]$out }

    $parts = ([string]$quotaLine).Trim() -split "\|", 7
    if ($parts.Length -lt 5) { return [pscustomobject]$out }

    $assetNow = $parts[0]
    $dayNow = $parts[1]
    $countNow = ($parts[2] | AsInt)
    $pacingAllowedNow = ($parts[3] | AsInt)
    $topkNow = ($parts[4] | AsInt)
    $nextAt = ""
    if ($parts.Length -ge 6) { $nextAt = ($parts[5] | AsStr) }
    $sleepSec = 0
    if ($parts.Length -ge 7) { $sleepSec = ($parts[6] | AsInt) }

    $out.Asset = $assetNow
    $out.Day = $dayNow
    $out.Count = $countNow
    $out.Allowed = $pacingAllowedNow
    $out.TopK = $topkNow
    $out.NextAt = $nextAt
    $out.SleepSec = $sleepSec

    if ($countNow -ge $K) {
      $out.Skip = $true
      $out.Kind = "max_k_reached_today"
      $out.Message = "[P31] max_k_reached_today asset=$assetNow day=$dayNow executed=$countNow/$K; skip"
      return [pscustomobject]$out
    }

    $pacingEnabled = (($env:TOPK_PACING_ENABLE | AsStr).ToLowerInvariant()) -notin @("0","false","f","no","n","off","")
    if ($pacingEnabled -and $pacingAllowedNow -gt 0 -and $countNow -ge $pacingAllowedNow) {
      $msg = "[P31] pacing_quota_reached asset=$assetNow day=$dayNow executed=$countNow/$topkNow allowed_now=$pacingAllowedNow/$topkNow"
      if ($nextAt) { $msg += " next_at=$nextAt" }
      $msg += "; skip"
      $out.Skip = $true
      $out.Kind = "pacing_quota_reached"
      $out.Message = $msg
      return [pscustomobject]$out
    }

    return [pscustomobject]$out
  }

  function Get-EvalGapStatus {
    try {
      $raw = & $py -c @"
from pathlib import Path
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import yaml

cfg = yaml.safe_load(Path('config.yaml').read_text(encoding='utf-8'))
data = cfg.get('data') or {}
asset = str(data.get('asset', 'UNKNOWN'))
interval_sec = int(data.get('interval_sec') or 300)
tz = ZoneInfo(str(data.get('timezone', 'UTC')))
now = datetime.now(tz)
day = now.strftime('%Y-%m-%d')

def count_from_state(asset: str, interval_sec: int, day: str) -> int:
    p = Path('runs') / 'live_topk_state.sqlite3'
    if not p.exists():
        return 0
    con = sqlite3.connect(str(p))
    try:
        info = con.execute('PRAGMA table_info(executed)').fetchall()
        cols = {r[1] for r in info}
        if {'asset', 'day'} - cols:
            return 0
        if 'interval_sec' in cols:
            cur = con.execute('SELECT COUNT(*) FROM executed WHERE asset=? AND interval_sec=? AND day=?', (asset, interval_sec, day))
        else:
            cur = con.execute('SELECT COUNT(*) FROM executed WHERE asset=? AND day=?', (asset, day))
        return int(cur.fetchone()[0] or 0)
    except Exception:
        return 0
    finally:
        con.close()

def count_from_signals(asset: str, interval_sec: int, day: str) -> int:
    p = Path('runs') / 'live_signals.sqlite3'
    if not p.exists():
        return 0
    con = sqlite3.connect(str(p))
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'signals_v2' not in tables:
            return 0
        info = con.execute('PRAGMA table_info(signals_v2)').fetchall()
        cols = {r[1] for r in info}
        if {'asset', 'day', 'action'} - cols:
            return 0
        where = ["day=?", "asset=?", "action IN ('CALL','PUT')"]
        params = [day, asset]
        if 'interval_sec' in cols:
            where.append('interval_sec=?')
            params.append(interval_sec)
        cur = con.execute(f"SELECT COUNT(*) FROM signals_v2 WHERE {' AND '.join(where)}", tuple(params))
        return int(cur.fetchone()[0] or 0)
    except Exception:
        return 0
    finally:
        con.close()

count_state = count_from_state(asset, interval_sec, day)
count_signals = count_from_signals(asset, interval_sec, day)
count = max(int(count_state), int(count_signals))

eval_count = 0
try:
    from natbin.summary_paths import find_daily_summary_path
    sp = find_daily_summary_path(day=day, asset=asset, interval_sec=interval_sec, out_dir='runs')
except Exception:
    sp = Path('runs') / f"daily_summary_{day.replace('-', '')}.json"
if sp is not None and sp.exists():
    try:
        s = json.loads(sp.read_text(encoding='utf-8'))
        if str(s.get('day') or day) == day:
            eval_count = int(s.get('trades_eval_total') or 0)
    except Exception:
        eval_count = 0

pending = max(0, int(count) - int(eval_count))
print(f"{asset}|{day}|{count}|{eval_count}|{pending}")
"@
      if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
      if ($raw -is [System.Array]) { $raw = ($raw | Select-Object -Last 1) }
      $parts = ([string]$raw).Trim() -split "\|", 5
      if ($parts.Length -lt 5) { return $null }
      return [pscustomobject]@{
        Asset = $parts[0]
        Day = $parts[1]
        Count = ($parts[2] | AsInt)
        Eval = ($parts[3] | AsInt)
        Pending = ($parts[4] | AsInt)
      }
    } catch {
      return $null
    }
  }

  function Refresh-DailySummary {
    try {
      $sum = & $py -m natbin.refresh_daily_summary --days 2
      if ($LASTEXITCODE -eq 0 -and $sum) {
        Write-Host "[P29] summary_refresh_ok: $sum" -ForegroundColor DarkGray
      } elseif ($LASTEXITCODE -ne 0) {
        Write-Host "[P29] WARN: refresh_daily_summary exit=$LASTEXITCODE" -ForegroundColor DarkYellow
      }
    } catch {
      Write-Host "[P29] WARN: refresh_daily_summary falhou: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }
  }

  function Invoke-RuntimePruneIfNeeded {
    $enabled = (($env:RUNTIME_PRUNE_ENABLE | AsStr).ToLowerInvariant()) -notin @("0","false","f","no","n","off","")
    if (-not $enabled) { return }

    $days = 30
    if ($env:RUNTIME_RETENTION_DAYS) {
      try { $days = [int]$env:RUNTIME_RETENTION_DAYS } catch {}
    }
    if ($days -lt 1) { $days = 1 }

    $stampPath = ".\runs\runtime_prune.last.txt"
    $today = Get-RepoDayStamp
    try {
      if (Test-Path $stampPath) {
        $prev = ((Get-Content $stampPath -Raw) | AsStr).Trim()
        if ($prev -eq $today) { return }
      }
    } catch {}

    try {
      $raw = & $py -m natbin.runtime_prune --days $days
      if ($LASTEXITCODE -ne 0) { throw "runtime_prune exit=$LASTEXITCODE" }
      if ($raw) {
        if ($raw -is [System.Array]) { $raw = ($raw | Select-Object -Last 1) }
        $line = (($raw | AsStr)).Trim()
        if ($line) {
          Write-Host "[P33] runtime_prune_ok: $line" -ForegroundColor DarkGray
        } else {
          Write-Host "[P33] runtime_prune_ok: {}" -ForegroundColor DarkGray
        }
      } else {
        Write-Host "[P33] runtime_prune_ok: {}" -ForegroundColor DarkGray
      }
      Set-Content -Path $stampPath -Value $today -Encoding UTF8
    } catch {
      Write-Host "[P33] WARN: runtime_prune falhou: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }
  }


  function Invoke-StateReconcileIfNeeded {
    $enabled = (($env:STATE_RECONCILE_ENABLE | AsStr).ToLowerInvariant()) -notin @("0","false","f","no","n","off","")
    if (-not $enabled) { return }

    $days = 7
    if ($env:STATE_RECONCILE_DAYS) {
      try { $days = [int]$env:STATE_RECONCILE_DAYS } catch {}
    }
    if ($days -lt 1) { $days = 1 }

    $stampPath = ".\runs\state_reconcile.last.txt"
    $today = Get-RepoDayStamp
    $stateDb = ".\runs\live_topk_state.sqlite3"
    $force = -not (Test-Path $stateDb)

    try {
      if ((-not $force) -and (Test-Path $stampPath)) {
        $prev = ((Get-Content $stampPath -Raw) | AsStr).Trim()
        if ($prev -eq $today) { return }
      }
    } catch {}

    try {
      $raw = & $py -m natbin.reconcile_topk_state --days $days
      if ($LASTEXITCODE -ne 0) { throw "reconcile_topk_state exit=$LASTEXITCODE" }
      if ($raw) {
        if ($raw -is [System.Array]) { $raw = ($raw | Select-Object -Last 1) }
        $line = (($raw | AsStr)).Trim()
        if ($line) {
          Write-Host "[P34] state_reconcile_ok: $line" -ForegroundColor DarkGray
        } else {
          Write-Host "[P34] state_reconcile_ok: {}" -ForegroundColor DarkGray
        }
      } else {
        Write-Host "[P34] state_reconcile_ok: {}" -ForegroundColor DarkGray
      }
      Set-Content -Path $stampPath -Value $today -Encoding UTF8
    } catch {
      Write-Host "[P34] WARN: state_reconcile falhou: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }
  }

  function Invoke-LegacyRuntimeCleanupIfNeeded {
    $enabled = (($env:LEGACY_RUNTIME_CLEANUP_ENABLE | AsStr).ToLowerInvariant()) -notin @("0","false","f","no","n","off","")
    if (-not $enabled) { return }

    $stampPath = ".\runs\legacy_runtime_cleanup.last.txt"
    $today = Get-RepoDayStamp
    try {
      if (Test-Path $stampPath) {
        $prev = ((Get-Content $stampPath -Raw) | AsStr).Trim()
        if ($prev -eq $today) { return }
      }
    } catch {}

    try {
      $raw = & $py -m natbin.legacy_runtime_cleanup
      if ($LASTEXITCODE -ne 0) { throw "legacy_runtime_cleanup exit=$LASTEXITCODE" }
      if ($raw) {
        if ($raw -is [System.Array]) { $raw = ($raw | Select-Object -Last 1) }
        $line = (($raw | AsStr)).Trim()
        if ($line) {
          Write-Host "[P37] legacy_runtime_cleanup_ok: $line" -ForegroundColor DarkGray
        } else {
          Write-Host "[P37] legacy_runtime_cleanup_ok: {}" -ForegroundColor DarkGray
        }
      } else {
        Write-Host "[P37] legacy_runtime_cleanup_ok: {}" -ForegroundColor DarkGray
      }
      Set-Content -Path $stampPath -Value $today -Encoding UTF8
    } catch {
      Write-Host "[P37] WARN: legacy_runtime_cleanup falhou: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }
  }

  function Invoke-QuotaSettleOnly {
    param(
      [Parameter(Mandatory=$true)]$QuotaDecision,
      [int]$Lookback = 2000
    )

    $ts = Get-RepoTimestamp
    Write-Host "`n[$ts] AUTO PREPARE (settle-only)" -ForegroundColor Cyan

    $env:LOOKBACK_CANDLES = "$Lookback"

    $collectOut = & $py -m natbin.collect_recent
    if ($collectOut) {
      foreach ($ln in @($collectOut)) {
        $s = ($ln | AsStr)
        if ($s) { Write-Host $s }
      }
    }
    if ($LASTEXITCODE -ne 0) { throw "collect_recent falhou" }

    $datasetOut = & $py -m natbin.make_dataset
    if ($datasetOut) {
      foreach ($ln in @($datasetOut)) {
        $s = ($ln | AsStr)
        if ($s) { Write-Host $s }
      }
    }
    if ($LASTEXITCODE -ne 0) { throw "make_dataset falhou" }

    Refresh-DailySummary

    $gap2 = Get-EvalGapStatus
    if ($gap2) {
      Write-Host "[P29b] quota_settle_status asset=$($gap2.Asset) day=$($gap2.Day) executed=$($gap2.Count) eval=$($gap2.Eval) pending=$($gap2.Pending)" -ForegroundColor DarkGray
    }

    $msg = $QuotaDecision.Message
    if ($QuotaDecision.Kind -eq "max_k_reached_today") {
      $msg = ($msg -replace '; skip$','; settle-only (collect + dataset + summary), skip auto params + observe')
    } elseif ($QuotaDecision.Kind -eq "pacing_quota_reached") {
      $msg = ($msg -replace '; skip$','; settle-only (collect + dataset + summary), skip auto params + observe')
    }
    Write-Host $msg -ForegroundColor DarkGray
    Write-Host "[P32] quota_frozen_today keep effective THRESHOLD=$($env:THRESHOLD) CPREG_ALPHA_END=$($env:CPREG_ALPHA_END) META_ISO_BLEND=$($env:META_ISO_BLEND) REGIME_MODE=$($env:REGIME_MODE) GATE_FAIL_CLOSED=$($env:GATE_FAIL_CLOSED) MARKET_CONTEXT_FAIL_CLOSED=$($env:MARKET_CONTEXT_FAIL_CLOSED) PAYOUT=$($env:PAYOUT) MARKET_OPEN=$($env:MARKET_OPEN) MARKET_CONTEXT_FRESH=$($env:MARKET_CONTEXT_FRESH) MARKET_CONTEXT_AGE_SEC=$($env:MARKET_CONTEXT_AGE_SEC) MARKET_CONTEXT_STALE=$($env:MARKET_CONTEXT_STALE) MARKET_CONTEXT_SOURCE=$($env:MARKET_CONTEXT_SOURCE) LEGACY_RUNTIME_CLEANUP_ENABLE=$($env:LEGACY_RUNTIME_CLEANUP_ENABLE)" -ForegroundColor DarkGray
    return ,$gap2
  }

  function Restore-MarketContextEnv {
    $restored = $false
    try {
      $ctxPath = Resolve-MarketContextPath
      $ctxMaxAge = 180
      try { if ($env:MARKET_CONTEXT_MAX_AGE_SEC) { $ctxMaxAge = [int]$env:MARKET_CONTEXT_MAX_AGE_SEC } } catch {}
      $ctx = Get-MarketContextSnapshot -Path $ctxPath -MaxAgeSec $ctxMaxAge
      if ($null -ne $ctx) {
        $v = Get-ObjProp -Obj $ctx -Name "fresh"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_CONTEXT_FRESH = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $ctx -Name "age_sec"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_CONTEXT_AGE_SEC = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $ctx -Name "stale"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_CONTEXT_STALE = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $ctx -Name "source"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_CONTEXT_SOURCE = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $ctx -Name "payout"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:PAYOUT = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $ctx -Name "market_open"
        if ($null -ne $v -and ((AsStr $v) -in @("1","0"))) { $env:MARKET_OPEN = [string]$v; $restored = $true }
      }
    } catch {}
    return $restored
  }

  function Restore-EffectiveEnv {
    $statePath = Resolve-EffectiveEnvPath
    $restored = $false
    if (Test-Path $statePath) {
      try {
        $prev = Get-Content $statePath -Raw | ConvertFrom-Json
        $v = Get-ObjProp -Obj $prev -Name "THRESHOLD"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:THRESHOLD = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "CPREG_ALPHA_START"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:CPREG_ALPHA_START = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "CPREG_ALPHA_END"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:CPREG_ALPHA_END = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "CPREG_SLOT2_MULT"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:CPREG_SLOT2_MULT = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "REGIME_MODE"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:REGIME_MODE = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "META_ISO_BLEND"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:META_ISO_BLEND = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "GATE_FAIL_CLOSED"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:GATE_FAIL_CLOSED = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "MARKET_CONTEXT_FAIL_CLOSED"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_CONTEXT_FAIL_CLOSED = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "PAYOUT"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:PAYOUT = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "MARKET_OPEN"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_OPEN = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "MARKET_CONTEXT_FRESH"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_CONTEXT_FRESH = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "MARKET_CONTEXT_AGE_SEC"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_CONTEXT_AGE_SEC = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "MARKET_CONTEXT_STALE"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_CONTEXT_STALE = [string]$v; $restored = $true }
        $v = Get-ObjProp -Obj $prev -Name "MARKET_CONTEXT_SOURCE"
        if ($null -ne $v -and (AsStr $v) -ne "") { $env:MARKET_CONTEXT_SOURCE = [string]$v; $restored = $true }
      } catch {
        # fallback below
      }
    }

    if (-not $restored) {
      $stateCandidates = @()
      $stateCandidates += (Get-AutoParamsStatePath)
      $legacyAutoParams = (($env:AUTO_PARAMS_LEGACY_FALLBACK | AsStr).ToLowerInvariant()) -in @('1','true','t','yes','y','on')
      if ($legacyAutoParams) { $stateCandidates += (Join-Path $repoRoot 'runs\auto_params.json') }
      $stateCandidates = $stateCandidates | Where-Object { $_ } | Select-Object -Unique
      foreach ($statePath in $stateCandidates) {
        if (!(Test-Path $statePath)) { continue }
        try {
          $prev = Get-Content $statePath -Raw | ConvertFrom-Json
          $pr = Get-ObjProp -Obj $prev -Name "recommended"
          if ($pr -ne $null) {
            $v = Get-ObjProp -Obj $pr -Name "threshold"
            if ($null -ne $v -and (AsStr $v) -ne "") { $env:THRESHOLD = [string]$v; $restored = $true }
            $v = Get-ObjProp -Obj $pr -Name "cpreg_alpha_start"
            if ($null -ne $v -and (AsStr $v) -ne "") { $env:CPREG_ALPHA_START = [string]$v; $restored = $true }
            $v = Get-ObjProp -Obj $pr -Name "cpreg_alpha_end"
            if ($null -ne $v -and (AsStr $v) -ne "") { $env:CPREG_ALPHA_END = [string]$v; $restored = $true }
            $v = Get-ObjProp -Obj $pr -Name "cpreg_slot2_mult"
            if ($null -ne $v -and (AsStr $v) -ne "") { $env:CPREG_SLOT2_MULT = [string]$v; $restored = $true }
            $v = Get-ObjProp -Obj $pr -Name "regime_mode"
            if ($null -ne $v -and (AsStr $v) -ne "") { $env:REGIME_MODE = [string]$v; $restored = $true }
            if ($restored) { break }
          }
        } catch {
          # tenta o proximo candidato
        }
      }
    }

    if ($env:CPREG_ALPHA_END) { $env:CP_ALPHA = $env:CPREG_ALPHA_END }
    if (-not $env:THRESHOLD) { $env:THRESHOLD = "0.02" }
    if (-not $env:META_ISO_BLEND) { $env:META_ISO_BLEND = "0.75" }
    if (-not $env:MARKET_CONTEXT_FRESH) { $env:MARKET_CONTEXT_FRESH = "0" }
    if (-not $env:MARKET_CONTEXT_AGE_SEC) { $env:MARKET_CONTEXT_AGE_SEC = "" }
    if (-not $env:MARKET_CONTEXT_STALE) { $env:MARKET_CONTEXT_STALE = "0" }
    if (-not $env:MARKET_CONTEXT_SOURCE) { $env:MARKET_CONTEXT_SOURCE = "" }
    if (-not $env:GATE_FAIL_CLOSED) { $env:GATE_FAIL_CLOSED = "1" }
    if (-not $env:MARKET_CONTEXT_FAIL_CLOSED) { $env:MARKET_CONTEXT_FAIL_CLOSED = "1" }
    $env:REGIME_MODE = Resolve-RegimeMode ($env:REGIME_MODE | AsStr)
    return $restored
  }

  function Save-EffectiveEnv {
    $statePath = Resolve-EffectiveEnvPath
    try {
      $payload = [ordered]@{
        saved_at = (Get-RepoIsoStamp)
        THRESHOLD = ($env:THRESHOLD | AsStr)
        CPREG_ALPHA_START = ($env:CPREG_ALPHA_START | AsStr)
        CPREG_ALPHA_END = ($env:CPREG_ALPHA_END | AsStr)
        CP_ALPHA = ($env:CP_ALPHA | AsStr)
        CPREG_SLOT2_MULT = ($env:CPREG_SLOT2_MULT | AsStr)
        REGIME_MODE = (Resolve-RegimeMode ($env:REGIME_MODE | AsStr) | AsStr)
        META_ISO_BLEND = ($env:META_ISO_BLEND | AsStr)
        GATE_FAIL_CLOSED = ($env:GATE_FAIL_CLOSED | AsStr)
        MARKET_CONTEXT_FAIL_CLOSED = ($env:MARKET_CONTEXT_FAIL_CLOSED | AsStr)
        PAYOUT = ($env:PAYOUT | AsStr)
        MARKET_OPEN = ($env:MARKET_OPEN | AsStr)
        MARKET_CONTEXT_FRESH = ($env:MARKET_CONTEXT_FRESH | AsStr)
        MARKET_CONTEXT_AGE_SEC = ($env:MARKET_CONTEXT_AGE_SEC | AsStr)
        MARKET_CONTEXT_STALE = ($env:MARKET_CONTEXT_STALE | AsStr)
        MARKET_CONTEXT_SOURCE = ($env:MARKET_CONTEXT_SOURCE | AsStr)
      }
      ($payload | ConvertTo-Json -Depth 4) | Set-Content -Path $statePath -Encoding UTF8
    } catch {
      Write-Host "[P32] WARN: Save-EffectiveEnv falhou: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }
  }

  function Invoke-AutoParams {
    # Seed previous effective params (persiste entre execucoes -Once / skips por quota)
    [void](Restore-EffectiveEnv)

    Write-Host "[P12] auto volume: computing params..." -ForegroundColor Cyan

    # VOL floors/ceilings for K>=3 + meta_iso
    $env:VOL_ENFORCE_P14 = "1"
    $env:VOL_THR_MIN = "0.02"
    $env:VOL_BOOTSTRAP_THR_FLOOR = "0.02"
    $env:VOL_BOOTSTRAP_STUCK_THR_FLOOR = "0.02"
    $env:VOL_SAFE_THR_MIN = "0.02"

    $env:VOL_ALPHA_MAX = "0.12"
    $env:VOL_BOOTSTRAP_ALPHA_END_CEIL = "0.12"
    $env:VOL_SAFE_ALPHA_MAX = "0.12"

    # defaults for meta_iso
    if (-not $env:META_ISO_ENABLE) { $env:META_ISO_ENABLE = "1" }
    if (-not $env:META_ISO_BLEND)  { $env:META_ISO_BLEND  = "0.75" }

    # auto-volume guardrails tuned from sweeps
    if (-not $env:VOL_THR_MIN)              { $env:VOL_THR_MIN = "0.02" }
    if (-not $env:VOL_THR_MAX)              { $env:VOL_THR_MAX = "0.14" }
    if (-not $env:VOL_ALPHA_MIN)            { $env:VOL_ALPHA_MIN = "0.05" }
    if (-not $env:VOL_ALPHA_MAX)            { $env:VOL_ALPHA_MAX = "0.12" }
    if (-not $env:VOL_BOOT_THR_FLOOR)       { $env:VOL_BOOT_THR_FLOOR = "0.02" }
    if (-not $env:VOL_BOOT_ALPHA_END_CEIL)  { $env:VOL_BOOT_ALPHA_END_CEIL = "0.12" }
    if (-not $env:VOL_STUCK_THR_FLOOR)      { $env:VOL_STUCK_THR_FLOOR = "0.02" }
    if (-not $env:VOL_STUCK_ALPHA_END_CEIL) { $env:VOL_STUCK_ALPHA_END_CEIL = "0.12" }

    if (-not $env:VOL_ENFORCE_P14)          { $env:VOL_ENFORCE_P14 = "1" }
    if (-not $env:VOL_SAFE_THR_MIN)         { $env:VOL_SAFE_THR_MIN = "0.02" }
    if (-not $env:VOL_SAFE_ALPHA_MAX)       { $env:VOL_SAFE_ALPHA_MAX = "0.12" }

    $json = & $py -m natbin.auto_volume
    if (!$json) { throw "auto_volume nao retornou JSON" }

    $obj = $json | ConvertFrom-Json
    $rec = Get-ObjProp -Obj $obj -Name "recommended"
    $windowObj = Get-ObjProp -Obj $obj -Name "window"
    $guardObj = Get-ObjProp -Obj $obj -Name "guardrails"

    $scanObj = Get-ObjProp -Obj $obj -Name "summary_scan"
    $p12SummaryFailClosedRaw = (Get-ObjProp -Obj $obj -Name "summary_fail_closed" | AsStr)
    $p12SummaryFailClosed = ($p12SummaryFailClosedRaw.ToLowerInvariant()) -notin @("", "0", "false", "f", "no", "n", "off")
    $p12ScanUsed = (Get-ObjProp -Obj $scanObj -Name "used_count" | AsStr)
    $p12ScanMissing = (Get-ObjProp -Obj $scanObj -Name "missing_count" | AsStr)
    $p12ScanInvalid = (Get-ObjProp -Obj $scanObj -Name "invalid_count" | AsStr)
    $p12ScanLegacy = (Get-ObjProp -Obj $scanObj -Name "legacy_fallback_count" | AsStr)
    $p12ScanStrict = (Get-ObjProp -Obj $scanObj -Name "strict" | AsStr)
    Write-Host ("[P12s] summary used={0} missing={1} invalid={2} legacy={3} strict={4} fail_closed={5}" -f `
      $p12ScanUsed, $p12ScanMissing, $p12ScanInvalid, $p12ScanLegacy, $p12ScanStrict, $p12SummaryFailClosedRaw) -ForegroundColor DarkGray

    # auto REGIME_MODE during bootstrap / no-eval (avoid starvation)
    # Se quiser forcar manualmente, set: $env:REGIME_MODE_LOCK="1" e $env:REGIME_MODE="hard|soft|off"
    # Mantem SOFT ate ter evidencia minimamente madura; 1 trade emitido no dia nao deve flipar
    # para HARD e bloquear o resto do bootstrap com regime_block.
    $forceSoftBootstrap = $false
    if (-not $p12SummaryFailClosed) {
      if ((($env:REGIME_MODE_LOCK | AsStr) -ne "1")) {
        $dec = (Get-ObjProp -Obj $obj -Name "decision" | AsStr)
        $tpd = 0.0
        try { $tpd = [double](Get-ObjProp -Obj $rec -Name "observed_trades_per_day" | AsStr) } catch { $tpd = 0.0 }
        $tt = 0
        try { $tt = [int](Get-ObjProp -Obj $rec -Name "observed_trades_today" | AsStr) } catch { $tt = 0 }
        $te = 0
        try { $te = [int](Get-ObjProp -Obj $rec -Name "observed_trades_eval_sum" | AsStr) } catch { $te = 0 }
        $daysUsed = 0
        try { $daysUsed = [int](Get-ObjProp -Obj $windowObj -Name "days_used" | AsStr) } catch { $daysUsed = 0 }
        $minDays = 1
        try { $minDays = [int](Get-ObjProp -Obj $guardObj -Name "min_days_used" | AsStr) } catch { $minDays = 1 }
        if ($minDays -lt 1) { $minDays = 1 }
        $minEval = 1
        try { $minEval = [int](Get-ObjProp -Obj $guardObj -Name "min_trades_eval" | AsStr) } catch { $minEval = 1 }
        if ($minEval -lt 1) { $minEval = 1 }

        $bootstrapLike = (($dec -like "*bootstrap*") -or ($te -lt $minEval) -or ($daysUsed -lt $minDays))
        if ($bootstrapLike) {
          $forceSoftBootstrap = $true
          $env:REGIME_MODE = "soft"
          Write-Host ("[P15e] REGIME_MODE=soft (bootstrap te={0}/{1} days={2}/{3} tpd={4} today={5})" -f `
            $te, $minEval, $daysUsed, $minDays, $tpd, $tt) -ForegroundColor Yellow
        } else {
          Write-Host ("[P15e] REGIME_MODE=hard (mature te={0}/{1} days={2}/{3} tpd={4} today={5})" -f `
            $te, $minEval, $daysUsed, $minDays, $tpd, $tt) -ForegroundColor DarkYellow
        }
      }

      $v = Get-ObjProp -Obj $rec -Name "threshold"
      if ($null -ne $v -and (AsStr $v) -ne "") { $env:THRESHOLD = [string]$v }
      $v = Get-ObjProp -Obj $rec -Name "cpreg_alpha_start"
      if ($null -ne $v -and (AsStr $v) -ne "") { $env:CPREG_ALPHA_START = [string]$v }
      $v = Get-ObjProp -Obj $rec -Name "cpreg_alpha_end"
      if ($null -ne $v -and (AsStr $v) -ne "") { $env:CPREG_ALPHA_END = [string]$v }
      $env:CP_ALPHA = $env:CPREG_ALPHA_END
      $v = Get-ObjProp -Obj $rec -Name "cpreg_slot2_mult"
      if ($null -ne $v -and (AsStr $v) -ne "") { $env:CPREG_SLOT2_MULT = [string]$v }
      $v = Get-ObjProp -Obj $rec -Name "regime_mode"
      if (-not $forceSoftBootstrap) {
        if ($null -ne $v -and (AsStr $v) -ne "") {
          $env:REGIME_MODE = [string]$v
          Write-Host "[P15f] REGIME_MODE override from auto_volume: $($env:REGIME_MODE)" -ForegroundColor DarkYellow
        } elseif ((($env:REGIME_MODE_LOCK | AsStr) -ne "1") -and -not $env:REGIME_MODE) {
          $env:REGIME_MODE = "hard"
        }
      }

      $env:CPREG_ENABLE = "1"
      if (!$env:CP_ALPHA) { $env:CP_ALPHA = $env:CPREG_ALPHA_START }
    } else {
      Write-Host "[P12] summary_fail_closed=1 keep current params" -ForegroundColor DarkYellow
    }

    $p12Decision = (Get-ObjProp -Obj $obj -Name "decision" | AsStr)
    $p12Lookback = (Get-ObjProp -Obj $obj -Name "lookback_days" | AsStr)
    $p12DaysUsed = (Get-ObjProp -Obj $windowObj -Name "days_used" | AsStr)
    $p12TPD = (Get-ObjProp -Obj $rec -Name "observed_trades_per_day" | AsStr)
    $p12Today = (Get-ObjProp -Obj $rec -Name "observed_trades_today" | AsStr)
    $p12Frac = (Get-ObjProp -Obj $rec -Name "observed_frac_day" | AsStr)
    $p12TE = (Get-ObjProp -Obj $rec -Name "observed_trades_eval_sum" | AsStr)
    $p12WR = (Get-ObjProp -Obj $rec -Name "observed_win_rate_eval" | AsStr)
    $p12EVW = (Get-ObjProp -Obj $rec -Name "observed_ev_avg_trades_w" | AsStr)
    Write-Host ("[P12] decision={0} lookback_days={1} days_used={2} trades/day={3} trades_today={4} frac={5} te={6} wr={7} evw={8}" -f `
      $p12Decision, $p12Lookback, $p12DaysUsed, $p12TPD, $p12Today, $p12Frac, $p12TE, $p12WR, $p12EVW) -ForegroundColor DarkCyan

    Write-Host ("[P12] applied: THRESHOLD={0} CPREG_ALPHA_START={1} CPREG_ALPHA_END={2} SLOT2_MULT={3}" -f `
      $env:THRESHOLD, $env:CPREG_ALPHA_START, $env:CPREG_ALPHA_END, $env:CPREG_SLOT2_MULT) -ForegroundColor Green

    # P16: auto META_ISO_BLEND from daily_summary
    try {
      if ($env:META_ISO_ENABLE -eq "1") {
        $p16 = & $py -m natbin.auto_isoblend
        if ($LASTEXITCODE -eq 0 -and $p16) {
          $o = $p16 | ConvertFrom-Json
          $scan16 = Get-ObjProp -Obj $o -Name "summary_scan"
          $p16Fail = (Get-ObjProp -Obj $o -Name "summary_fail_closed" | AsStr)
          $p16Used = (Get-ObjProp -Obj $scan16 -Name "used_count" | AsStr)
          $p16Miss = (Get-ObjProp -Obj $scan16 -Name "missing_count" | AsStr)
          $p16Inv = (Get-ObjProp -Obj $scan16 -Name "invalid_count" | AsStr)
          $p16Legacy = (Get-ObjProp -Obj $scan16 -Name "legacy_fallback_count" | AsStr)
          Write-Host "[P16s] summary used=$p16Used missing=$p16Miss invalid=$p16Inv legacy=$p16Legacy fail_closed=$p16Fail" -ForegroundColor DarkGray
          $v = Get-ObjProp -Obj $o -Name "meta_iso_blend"
          if ($null -ne $v -and (AsStr $v) -ne "") {
            $env:META_ISO_BLEND = [string]$v
            $p16Decision = (Get-ObjProp -Obj $o -Name "decision" | AsStr)
            $p16TPD = (Get-ObjProp -Obj $o -Name "tpd" | AsStr)
            $p16WR = (Get-ObjProp -Obj $o -Name "wr" | AsStr)
            $p16Days = (Get-ObjProp -Obj $o -Name "days_used" | AsStr)
            $p16Today = (Get-ObjProp -Obj $o -Name "trades_today" | AsStr)
            Write-Host "[P16] decision=$p16Decision META_ISO_BLEND=$($env:META_ISO_BLEND) (tpd=$p16TPD wr=$p16WR days=$p16Days today=$p16Today)"
          }
        }
      }
    } catch {
      Write-Host "[P16] WARN: auto_isoblend falhou: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }

    # P17: hour-aware threshold multiplier
    try {
      if ($env:P17_ENABLE -ne "0") {
        $p17 = & $py -m natbin.auto_hourthr
        if ($LASTEXITCODE -eq 0 -and $p17) {
          $o = $p17 | ConvertFrom-Json
          $scan17 = Get-ObjProp -Obj $o -Name "summary_scan"
          $p17Fail = (Get-ObjProp -Obj $o -Name "summary_fail_closed" | AsStr)
          $p17Used = (Get-ObjProp -Obj $scan17 -Name "used_count" | AsStr)
          $p17Miss = (Get-ObjProp -Obj $scan17 -Name "missing_count" | AsStr)
          $p17Inv = (Get-ObjProp -Obj $scan17 -Name "invalid_count" | AsStr)
          $p17Legacy = (Get-ObjProp -Obj $scan17 -Name "legacy_fallback_count" | AsStr)
          Write-Host "[P17s] summary used=$p17Used missing=$p17Miss invalid=$p17Inv legacy=$p17Legacy fail_closed=$p17Fail" -ForegroundColor DarkGray
          $v = Get-ObjProp -Obj $o -Name "threshold_out"
          if ($null -ne $v -and (AsStr $v) -ne "") {
            $thr_before = $env:THRESHOLD
            $env:THRESHOLD = [string]$v
            $p17Decision = (Get-ObjProp -Obj $o -Name "decision" | AsStr)
            $p17Hour = (Get-ObjProp -Obj $o -Name "hour" | AsStr)
            $p17Mult = (Get-ObjProp -Obj $o -Name "hour_mult" | AsStr)
            $p17Trades = (Get-ObjProp -Obj $o -Name "hour_trades" | AsStr)
            $p17WR = (Get-ObjProp -Obj $o -Name "hour_wr" | AsStr)
            $p17EV = (Get-ObjProp -Obj $o -Name "hour_ev_mean" | AsStr)
            Write-Host "[P17] decision=$p17Decision hour=$p17Hour mult=$p17Mult THRESHOLD=$thr_before->$($env:THRESHOLD) (h_trades=$p17Trades h_wr=$p17WR h_ev=$p17EV)"
          }
        }
      }
    } catch {
      Write-Host "[P17] WARN: auto_hourthr falhou: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }

    Save-EffectiveEnv
  }

  # [P35-rootfix] Real startup hydration: now that all helpers exist, restore the same effective/runtime
  # context the main loop will use. This makes [P26] and quota-skip logs deterministic and debuggable.
  $startupEff = $false
  $startupCtx = $false
  $startupWarns = @()
  try { $startupEff = [bool](Restore-EffectiveEnv) } catch { $startupWarns += ("effective_env: " + $_.Exception.Message) }
  try { $startupCtx = [bool](Restore-MarketContextEnv) } catch { $startupWarns += ("market_context: " + $_.Exception.Message) }
  try {
    $env:REGIME_MODE = Resolve-RegimeMode ($env:REGIME_MODE | AsStr)
  } catch {
    $env:REGIME_MODE = "hard"
    $startupWarns += ("regime_mode: " + $_.Exception.Message)
  }
  $startupWarnCount = @($startupWarns).Count
  Write-Host "[P26r] startup_hydration effective=$startupEff market_context=$startupCtx source=$($env:MARKET_CONTEXT_SOURCE) age=$($env:MARKET_CONTEXT_AGE_SEC) stale=$($env:MARKET_CONTEXT_STALE) warnings=$startupWarnCount" -ForegroundColor DarkGray
  if ($startupWarnCount -gt 0) {
    Write-Host ("[P26w] startup_hydration warnings: " + (($startupWarns -join ' | ') | AsStr)) -ForegroundColor DarkYellow
  }
  Write-Host "[P26] TOPK_ROLLING_MINUTES=$($env:TOPK_ROLLING_MINUTES) TOPK_MIN_GAP_MINUTES=$($env:TOPK_MIN_GAP_MINUTES) TOPK_PACING_ENABLE=$($env:TOPK_PACING_ENABLE) VOL_TARGET_TRADES_PER_DAY=$($env:VOL_TARGET_TRADES_PER_DAY) REGIME_MODE=$($env:REGIME_MODE) GATE_FAIL_CLOSED=$($env:GATE_FAIL_CLOSED) MARKET_CONTEXT_FAIL_CLOSED=$($env:MARKET_CONTEXT_FAIL_CLOSED) PAYOUT=$($env:PAYOUT) MARKET_OPEN=$($env:MARKET_OPEN) MARKET_CONTEXT_FRESH=$($env:MARKET_CONTEXT_FRESH) MARKET_CONTEXT_AGE_SEC=$($env:MARKET_CONTEXT_AGE_SEC) MARKET_CONTEXT_STALE=$($env:MARKET_CONTEXT_STALE) MARKET_CONTEXT_SOURCE=$($env:MARKET_CONTEXT_SOURCE) RUNTIME_RETENTION_DAYS=$($env:RUNTIME_RETENTION_DAYS) STATE_RECONCILE_DAYS=$($env:STATE_RECONCILE_DAYS) LOOP_LOG_RETENTION_DAYS=$($env:LOOP_LOG_RETENTION_DAYS) LOOP_STATUS_ENABLE=$($env:LOOP_STATUS_ENABLE) LEGACY_RUNTIME_CLEANUP_ENABLE=$($env:LEGACY_RUNTIME_CLEANUP_ENABLE)" -ForegroundColor DarkGray


  Save-LoopStatus -Phase "startup" -State "running" -Message "loop_initialized"

  $fail = 0
  $backoff = 30

  while ($true) {
    try {
      Ensure-LoopTranscriptCurrentDay
      [void](Restore-EffectiveEnv)
      [void](Restore-MarketContextEnv)
      Invoke-RuntimePruneIfNeeded
      Invoke-StateReconcileIfNeeded
      Invoke-LegacyRuntimeCleanupIfNeeded

      $preQuota = Get-QuotaSkipDecision -K $TopK
      if ($preQuota.Skip) {
        $settleOnSkip = (($env:QUOTA_SKIP_SETTLE_ENABLE | AsStr).ToLowerInvariant()) -notin @("0","false","f","no","n","off","")
        $gap = Get-EvalGapStatus
        $shouldSettle = $false
        $gapPending = (Get-ObjProp -Obj $gap -Name "Pending" | AsInt)
        if ($gapPending -eq 0) { $gapPending = (Get-ObjProp -Obj $gap -Name "pending" | AsInt) }
        if ($settleOnSkip -and $gap -and ($gapPending -gt 0)) {
          $shouldSettle = $true
        }

        $sleepDecision = $preQuota
        if ($shouldSettle) {
          $gapAfter = Invoke-QuotaSettleOnly -QuotaDecision $preQuota -Lookback $LookbackCandles
          $gapAfterPending = (Get-ObjProp -Obj $gapAfter -Name "Pending" | AsInt)
          if ($gapAfterPending -eq 0) { $gapAfterPending = (Get-ObjProp -Obj $gapAfter -Name "pending" | AsInt) }
          if ($gapAfter -and ($gapAfterPending -gt 0)) {
            $gapAfterAsset = (Get-ObjProp -Obj $gapAfter -Name "Asset" | AsStr)
            if (-not $gapAfterAsset) { $gapAfterAsset = (Get-ObjProp -Obj $gapAfter -Name "asset" | AsStr) }
            $gapAfterDay = (Get-ObjProp -Obj $gapAfter -Name "Day" | AsStr)
            if (-not $gapAfterDay) { $gapAfterDay = (Get-ObjProp -Obj $gapAfter -Name "day" | AsStr) }
            $gapAfterCount = (Get-ObjProp -Obj $gapAfter -Name "Count" | AsInt)
            if ($gapAfterCount -eq 0) { $gapAfterCount = (Get-ObjProp -Obj $gapAfter -Name "count" | AsInt) }
            Write-Host "[P31c] quota_pending_settle asset=$gapAfterAsset day=$gapAfterDay pending=$gapAfterPending; sleep next candle" -ForegroundColor DarkGray
            $sleepDecision = [pscustomobject]@{
              Skip = $true
              Kind = "quota_pending_settle"
              Message = ""
              Asset = $gapAfterAsset
              Day = $gapAfterDay
              Count = $gapAfterCount
              Allowed = 0
              TopK = $TopK
              NextAt = ""
              SleepSec = 0
            }
          }
        } else {
          if ($preQuota.Kind -eq "max_k_reached_today") {
            Write-Host ($preQuota.Message -replace '; skip$','; skip collect + dataset + summary + auto params + observe') -ForegroundColor DarkGray
          } elseif ($preQuota.Kind -eq "pacing_quota_reached") {
            Write-Host ($preQuota.Message -replace '; skip$','; skip collect + dataset + summary + auto params + observe') -ForegroundColor DarkGray
          } else {
            Write-Host $preQuota.Message -ForegroundColor DarkGray
          }
          if ($gap) {
            Write-Host "[P29b] quota_settle_status asset=$($gap.Asset) day=$($gap.Day) executed=$($gap.Count) eval=$($gap.Eval) pending=$($gap.Pending)" -ForegroundColor DarkGray
          }
          Write-Host "[P32] quota_frozen_today keep effective THRESHOLD=$($env:THRESHOLD) CPREG_ALPHA_END=$($env:CPREG_ALPHA_END) META_ISO_BLEND=$($env:META_ISO_BLEND) REGIME_MODE=$($env:REGIME_MODE) GATE_FAIL_CLOSED=$($env:GATE_FAIL_CLOSED) MARKET_CONTEXT_FAIL_CLOSED=$($env:MARKET_CONTEXT_FAIL_CLOSED) PAYOUT=$($env:PAYOUT) MARKET_OPEN=$($env:MARKET_OPEN) MARKET_CONTEXT_FRESH=$($env:MARKET_CONTEXT_FRESH) MARKET_CONTEXT_AGE_SEC=$($env:MARKET_CONTEXT_AGE_SEC) MARKET_CONTEXT_STALE=$($env:MARKET_CONTEXT_STALE) MARKET_CONTEXT_SOURCE=$($env:MARKET_CONTEXT_SOURCE) LEGACY_RUNTIME_CLEANUP_ENABLE=$($env:LEGACY_RUNTIME_CLEANUP_ENABLE)" -ForegroundColor DarkGray
          Save-LoopStatus -Phase "quota_skip" -State "blocked" -Message (($preQuota.Message | AsStr) -replace '; skip$','') -Quota $preQuota -EvalGap $gap
        }
        if ($shouldSettle -and $gapAfter) {
          $stMsg = ($preQuota.Message | AsStr)
          if ($gapAfterPending -gt 0) {
            Save-LoopStatus -Phase "quota_pending_settle" -State "blocked" -Message (($stMsg -replace '; skip$','') + "; pending settlement") -Quota $preQuota -EvalGap $gapAfter
          } else {
            Save-LoopStatus -Phase "quota_settled" -State "blocked" -Message (($stMsg -replace '; skip$','') + "; settle-only complete") -Quota $preQuota -EvalGap $gapAfter
          }
        }
        if ($Once) { break }
        Sleep-QuotaAware -QuotaDecision $sleepDecision -FallbackSeconds $SleepSeconds
        continue
      }

      $ts = Get-RepoTimestamp
      Write-Host "`n[$ts] AUTO PREPARE" -ForegroundColor Cyan
      Save-LoopStatus -Phase "auto_prepare" -State "running" -Message "collect + dataset + summary + auto params + observe"

      $env:LOOKBACK_CANDLES = "$LookbackCandles"

      & $py -m natbin.collect_recent
      if ($LASTEXITCODE -ne 0) { throw "collect_recent falhou" }

      & $py -m natbin.make_dataset
      if ($LASTEXITCODE -ne 0) { throw "make_dataset falhou" }

      Refresh-DailySummary

      Invoke-MarketContext
      [void](Restore-EffectiveEnv)

      $skipObserve = $false
      $skipAutoParams = $false
      $quotaSleepDecision = $null
      $skipPhase = ""
      $skipState = ""
      $skipMessage = ""

      $mcFailClosed = (($env:MARKET_CONTEXT_FAIL_CLOSED | AsStr).ToLowerInvariant()) -notin @("0","false","f","no","n","off","")
      $mcStale = (($env:MARKET_CONTEXT_STALE | AsStr).ToLowerInvariant()) -in @("1","true","t","yes","y","on")
      if ($mcFailClosed -and $mcStale) {
        $assetNow = Get-ConfigAsset
        $ageNow = ($env:MARKET_CONTEXT_AGE_SEC | AsStr)
        $srcNow = ($env:MARKET_CONTEXT_SOURCE | AsStr)
        $staleMsg = "[P30c] market_context_stale_fail_closed asset=$assetNow source=$srcNow age=${ageNow}s; skip auto params + observe"
        Write-Host $staleMsg -ForegroundColor DarkYellow
        $skipObserve = $true
        $skipAutoParams = $true
        $skipPhase = "market_context_skip"
        $skipState = "blocked"
        $skipMessage = $staleMsg
      }

      $quotaNow = Get-QuotaSkipDecision -K $TopK
      if (-not $skipObserve -and $quotaNow.Skip) {
        if ($quotaNow.Kind -eq "max_k_reached_today") {
          Write-Host ($quotaNow.Message -replace '; skip$','; skip auto params + observe') -ForegroundColor DarkGray
        } elseif ($quotaNow.Kind -eq "pacing_quota_reached") {
          Write-Host ($quotaNow.Message -replace '; skip$','; skip auto params + observe') -ForegroundColor DarkGray
        } else {
          Write-Host $quotaNow.Message -ForegroundColor DarkGray
        }
        $skipObserve = $true
        $skipAutoParams = $true
        $quotaSleepDecision = $quotaNow
        $skipPhase = "quota_skip_post_prepare"
        $skipState = "blocked"
        $skipMessage = (($quotaNow.Message | AsStr) -replace '; skip$','')
      }

      if (-not $skipAutoParams) {
        Invoke-AutoParams
      } else {
        if ($skipPhase -eq "market_context_skip") {
          Write-Host "[P32] market_context_frozen_today keep effective THRESHOLD=$($env:THRESHOLD) CPREG_ALPHA_END=$($env:CPREG_ALPHA_END) META_ISO_BLEND=$($env:META_ISO_BLEND) REGIME_MODE=$($env:REGIME_MODE) GATE_FAIL_CLOSED=$($env:GATE_FAIL_CLOSED) MARKET_CONTEXT_FAIL_CLOSED=$($env:MARKET_CONTEXT_FAIL_CLOSED) PAYOUT=$($env:PAYOUT) MARKET_OPEN=$($env:MARKET_OPEN) MARKET_CONTEXT_FRESH=$($env:MARKET_CONTEXT_FRESH) MARKET_CONTEXT_AGE_SEC=$($env:MARKET_CONTEXT_AGE_SEC) MARKET_CONTEXT_STALE=$($env:MARKET_CONTEXT_STALE)" -ForegroundColor DarkGray
        } else {
          Write-Host "[P32] quota_frozen_today keep effective THRESHOLD=$($env:THRESHOLD) CPREG_ALPHA_END=$($env:CPREG_ALPHA_END) META_ISO_BLEND=$($env:META_ISO_BLEND) REGIME_MODE=$($env:REGIME_MODE) GATE_FAIL_CLOSED=$($env:GATE_FAIL_CLOSED) MARKET_CONTEXT_FAIL_CLOSED=$($env:MARKET_CONTEXT_FAIL_CLOSED) PAYOUT=$($env:PAYOUT) MARKET_OPEN=$($env:MARKET_OPEN) MARKET_CONTEXT_FRESH=$($env:MARKET_CONTEXT_FRESH) MARKET_CONTEXT_AGE_SEC=$($env:MARKET_CONTEXT_AGE_SEC) MARKET_CONTEXT_STALE=$($env:MARKET_CONTEXT_STALE) MARKET_CONTEXT_SOURCE=$($env:MARKET_CONTEXT_SOURCE) LEGACY_RUNTIME_CLEANUP_ENABLE=$($env:LEGACY_RUNTIME_CLEANUP_ENABLE)" -ForegroundColor DarkGray
        }
        if ($skipPhase) {
          Save-LoopStatus -Phase $skipPhase -State $skipState -Message $skipMessage -Quota $quotaNow
        }
      }

      # roda 1 ciclo do loop principal somente para OBSERVE (coleta/dataset ja feitos acima)
      if (-not $skipObserve) {
        if ($TopK -gt 0) {
          & pwsh -ExecutionPolicy Bypass -File $loop -Once -LookbackCandles $LookbackCandles -TopK $TopK -SkipCollect -SkipDataset
        } else {
          & pwsh -ExecutionPolicy Bypass -File $loop -Once -LookbackCandles $LookbackCandles -SkipCollect -SkipDataset
        }
        if ($LASTEXITCODE -ne 0) { throw "observe_loop.ps1 falhou (exit=$LASTEXITCODE)" }
        Save-LoopStatus -Phase "cycle_ok" -State "ok" -Message "cycle_completed" -Quota $quotaNow
      } elseif (-not $skipPhase) {
        Save-LoopStatus -Phase "cycle_skipped" -State "blocked" -Message "skip_observe" -Quota $quotaNow
      }

      $fail = 0
      $backoff = 30

    } catch {
      $fail += 1
      Write-Host "[AUTOLOOP][ERR] $($_.Exception.Message)" -ForegroundColor Red
      Save-LoopStatus -Phase "error" -State "error" -Message $_.Exception.Message -Extra @{ fail = $fail; max_failures = $MaxFailures; backoff_sec = $backoff }

      if ($Once) { throw }
      if ($fail -ge $MaxFailures) { throw }

      Write-Host "[AUTOLOOP] retry em $backoff s (fail=$fail/$MaxFailures)" -ForegroundColor DarkYellow
      Start-Sleep -Seconds $backoff
      $backoff = [Math]::Min($backoff * 2, 300)
      continue
    }

    if ($Once) { break }
    if ($quotaSleepDecision) {
      Save-LoopStatus -Phase "sleep" -State "idle" -Message "quota_sleep" -Quota $quotaSleepDecision
      Sleep-QuotaAware -QuotaDecision $quotaSleepDecision -FallbackSeconds $SleepSeconds
    } else {
      Save-LoopStatus -Phase "sleep" -State "idle" -Message "next_candle"
      Sleep-ToNextCandle -FallbackSeconds $SleepSeconds
    }
  }

}
finally {
  if ($transcriptStarted) {
    try { Stop-Transcript | Out-Null } catch {}
  }
  Finalize-LoopStatusStopped
  if ($lockStream) {
    try { $lockStream.Close() } catch {}
    try { $lockStream.Dispose() } catch {}
  }
}

exit 0
