# P8 - Coverage Regulator (time-of-day threshold schedule)
#
# Adds an optional "coverage regulator" that gradually relaxes the EV/score threshold
# as the day progresses (local timezone). This helps avoid the "early quota capture" issue
# in TOPK-per-day online mode: low thresholds early in the day can consume k too soon.
#
# Env flags (recommended):
#   COVREG_ENABLE=1
#   COVREG_THR_LO=0.07
#   COVREG_WARMUP_FRAC=0.50
#   COVREG_RAMP_END_FRAC=0.90
#
# Behaviour:
#   - Until warmup_frac of the day (e.g., 50%), threshold = thr_hi (the configured threshold)
#   - Then linearly ramps down to thr_lo by ramp_end_frac of the day (e.g., 90%)
#   - After that, threshold = thr_lo
#
# Files patched:
#   - src/natbin/paper_pnl_backtest.py
#   - src/natbin/observe_signal_topk_perday.py

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param([Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Content)
  $enc = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

function Read-FileRaw {
  param([Parameter(Mandatory=$true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Arquivo não encontrado: $Path"
  }
  return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8)
}

function Patch-InsertAfter {
  param(
    [Parameter(Mandatory=$true)][string]$Text,
    [Parameter(Mandatory=$true)][string]$Needle,
    [Parameter(Mandatory=$true)][string]$Insert
  )
  $idx = $Text.IndexOf($Needle)
  if ($idx -lt 0) {
    throw "Padrão não encontrado para inserção: $Needle"
  }
  $pos = $idx + $Needle.Length
  return $Text.Substring(0, $pos) + $Insert + $Text.Substring($pos)
}

function Patch-ReplaceOnceRegex {
  param(
    [Parameter(Mandatory=$true)][string]$Text,
    [Parameter(Mandatory=$true)][string]$Pattern,
    [Parameter(Mandatory=$true)][string]$Replacement
  )
  $re = New-Object System.Text.RegularExpressions.Regex($Pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)
  if (-not $re.IsMatch($Text)) {
    throw "Padrão não encontrado para replace: $Pattern"
  }
  return $re.Replace($Text, $Replacement, 1)
}

$covHelper = @'

# --- P8: coverage regulator (threshold schedule) ---

def covreg_thresholds(ts: np.ndarray, tz: ZoneInfo, *, thr_hi: float, thr_lo: float,
                      warmup_frac: float = 0.50, ramp_end_frac: float = 0.90) -> np.ndarray:
    """Return per-row thresholds based on local time-of-day.

    The goal is to keep the threshold strict early in the day (avoid consuming TOPK too early),
    and relax it later if we still haven't found a good opportunity.

    - Before warmup_frac: threshold = thr_hi
    - Between warmup_frac and ramp_end_frac: linear ramp to thr_lo
    - After ramp_end_frac: threshold = thr_lo

    If thr_lo >= thr_hi (or invalid params), returns a constant array at thr_hi.
    """
    n = int(len(ts))
    if n <= 0:
        return np.zeros(0, dtype=float)

    thr_hi = float(thr_hi)
    thr_lo = float(thr_lo)
    if thr_lo >= thr_hi:
        return np.full(n, thr_hi, dtype=float)

    warm = float(warmup_frac)
    endf = float(ramp_end_frac)
    # clamp
    if warm < 0.0: warm = 0.0
    if warm > 1.0: warm = 1.0
    if endf < 0.0: endf = 0.0
    if endf > 1.0: endf = 1.0
    if endf <= warm:
        return np.full(n, thr_hi, dtype=float)

    dt = pd.to_datetime(ts, unit="s", utc=True).tz_convert(tz)
    minutes = dt.hour.astype(float) * 60.0 + dt.minute.astype(float)
    frac = (minutes / 1440.0).to_numpy(dtype=float)

    out = np.full(n, thr_hi, dtype=float)
    m = frac > warm
    if np.any(m):
        t = (frac[m] - warm) / (endf - warm)
        t = np.clip(t, 0.0, 1.0)
        out[m] = thr_hi + (thr_lo - thr_hi) * t
    return out

# --- end P8 ---
'@

# -------- patch paper_pnl_backtest.py --------
$paperPath = Join-Path "src" "natbin" "paper_pnl_backtest.py"
$paper = Read-FileRaw $paperPath

if ($paper -notmatch "covreg_thresholds\(") {
  # insert helper after ZoneInfo import (best anchor)
  $anchor = "from zoneinfo import ZoneInfo"
  if ($paper -notlike "*${anchor}*") {
    throw "Anchor não encontrado em ${paperPath}: ${anchor}"
  }
  $paper = Patch-InsertAfter -Text $paper -Needle $anchor -Insert $covHelper
}

# replace cand computation (constant threshold) with covreg-aware version
$paperReplacePattern = "^\s*cand\s*=\s*mask\s*&\s*\(metric\s*>=\s*thr\)\s*$"
$paperReplacement = @'
# P8 covreg (optional)
cov_enable = str(os.getenv("COVREG_ENABLE", "0")).strip().lower() not in ("0", "", "false", "no", "off")
thr_lo = float(os.getenv("COVREG_THR_LO", str(thr)))
warm = float(os.getenv("COVREG_WARMUP_FRAC", "0.50"))
endf = float(os.getenv("COVREG_RAMP_END_FRAC", "0.90"))
if cov_enable:
    thr_arr = covreg_thresholds(test_df["ts"].to_numpy(dtype=int), tz, thr_hi=float(thr), thr_lo=float(thr_lo),
                                warmup_frac=warm, ramp_end_frac=endf)
    cand = mask & (metric >= thr_arr)
else:
    cand = mask & (metric >= thr)
'@

$paper = Patch-ReplaceOnceRegex -Text $paper -Pattern $paperReplacePattern -Replacement $paperReplacement

Write-Utf8NoBomFile -Path $paperPath -Content $paper
Write-Host "ok: $paperPath"

# -------- patch observe_signal_topk_perday.py --------
$obsPath = Join-Path "src" "natbin" "observe_signal_topk_perday.py"
$obs = Read-FileRaw $obsPath

if ($obs -notmatch "covreg_thresholds\(") {
  $anchor2 = "from zoneinfo import ZoneInfo"
  if ($obs -notlike "*${anchor2}*") {
    throw "Anchor não encontrado em ${obsPath}: ${anchor2}"
  }
  $obs = Patch-InsertAfter -Text $obs -Needle $anchor2 -Insert $covHelper
}

# replace cand computation
$obsReplacePattern = "^\s*cand\s*=\s*mask\s*&\s*\(metric\s*>=\s*thr\)\s*$"
$obsReplacement = @'
# P8 covreg (optional)
cov_enable = str(os.getenv("COVREG_ENABLE", "0")).strip().lower() not in ("0", "", "false", "no", "off")
thr_lo = float(os.getenv("COVREG_THR_LO", str(thr)))
warm = float(os.getenv("COVREG_WARMUP_FRAC", "0.50"))
endf = float(os.getenv("COVREG_RAMP_END_FRAC", "0.90"))
thr_eff = float(thr)
if cov_enable:
    thr_arr = covreg_thresholds(df_day["ts"].to_numpy(dtype=int), tz, thr_hi=float(thr), thr_lo=float(thr_lo),
                                warmup_frac=warm, ramp_end_frac=endf)
    cand = mask & (metric >= thr_arr)
    thr_eff = float(thr_arr[-1])
else:
    cand = mask & (metric >= thr)
'@

$obs = Patch-ReplaceOnceRegex -Text $obs -Pattern $obsReplacePattern -Replacement $obsReplacement

# patch comparisons & logging to use thr_eff
$obs = $obs -replace "float\(metric\[now_i\]\) < thr", "float(metric[now_i]) < thr_eff"
$obs = $obs -replace "\"threshold\": float\(thr\)", '"threshold": float(thr_eff)'

Write-Utf8NoBomFile -Path $obsPath -Content $obs
Write-Host "ok: $obsPath"

Write-Host "P8 coverage regulator patch applied."
Write-Host "Env examples:" 
Write-Host "  `$env:COVREG_ENABLE='1'" 
Write-Host "  `$env:COVREG_THR_LO='0.07'" 
Write-Host "  `$env:COVREG_WARMUP_FRAC='0.50'" 
Write-Host "  `$env:COVREG_RAMP_END_FRAC='0.90'" 
