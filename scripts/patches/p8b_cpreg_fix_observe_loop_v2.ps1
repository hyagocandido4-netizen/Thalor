# P8b CPREG + hotfix NameError in observe_signal_topk_perday.py
# - Fixes: NameError: thr not defined when reading COVREG_THR_LO default
# - Adds: CPREG (Conformal coverage regulator) alpha schedule + slot-aware tightening
# Safe: creates timestamped .bak before modifying.

$ErrorActionPreference = "Stop"

function Write-Utf8NoBomFile([string]$Path, [string]$Content) {
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Backup-File([string]$Path) {
  if (Test-Path $Path) {
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $bak = "$Path.bak_$ts"
    Copy-Item -LiteralPath $Path -Destination $bak -Force
    Write-Host "Backup: $bak"
  } else {
    throw "Arquivo não encontrado: $Path"
  }
}

$repoRoot = Get-Location
$target = Join-Path $repoRoot "src\natbin\observe_signal_topk_perday.py"

Backup-File $target

$txt = Get-Content -Raw -Encoding UTF8 $target

# 1) Fix NameError: replace default str(thr) with a safe literal.
# We replace: os.getenv("COVREG_THR_LO", str(thr))  -> os.getenv("COVREG_THR_LO", "0.10")
$txt2 = $txt.Replace('os.getenv("COVREG_THR_LO", str(thr))', 'os.getenv("COVREG_THR_LO", "0.10")')

# Also handle single-quote variants, just in case.
$txt2 = $txt2.Replace("os.getenv('COVREG_THR_LO', str(thr))", "os.getenv('COVREG_THR_LO', '0.10')")

# 2) Inject CPREG helper functions if missing.
if ($txt2 -notmatch "def\s+cpreg_alpha") {
  $inject = @'
# --- CPREG (Conformal Coverage Regulator) -------------------------------------
# Goal: increase coverage without sacrificing EV/WR by scheduling CP_ALPHA over the day
# and tightening the 2nd slot (k=2) to reduce "early quota capture".
#
# Env vars:
#   CPREG_ENABLE=1
#   CPREG_ALPHA_START=0.06
#   CPREG_ALPHA_END=0.09
#   CPREG_WARMUP_FRAC=0.50
#   CPREG_RAMP_END_FRAC=0.90
#   CPREG_SLOT2_MULT=0.85
#
import os

def _day_progress_frac(ts: int, tz_name: str = "America/Sao_Paulo") -> float:
    """Return fraction [0,1] of local day elapsed for timestamp ts (seconds)."""
    try:
        from zoneinfo import ZoneInfo
        import datetime as _dt
        dt = _dt.datetime.fromtimestamp(int(ts), tz=ZoneInfo(tz_name))
        sod = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        frac = (dt - sod).total_seconds() / 86400.0
        if frac < 0: frac = 0.0
        if frac > 1: frac = 1.0
        return float(frac)
    except Exception:
        # fallback: UTC fraction
        import datetime as _dt
        dt = _dt.datetime.utcfromtimestamp(int(ts))
        sod = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        frac = (dt - sod).total_seconds() / 86400.0
        if frac < 0: frac = 0.0
        if frac > 1: frac = 1.0
        return float(frac)

def cpreg_alpha(ts: int, executed_today: int, tz_name: str = "America/Sao_Paulo") -> float:
    """
    Alpha schedule:
      - warmup: keep alpha_start (more conservative)
      - ramp: linearly increase towards alpha_end
      - late: alpha_end
    Slot-aware:
      - if executed_today >= 1 (2nd slot), multiply alpha by SLOT2_MULT (<1 => more conservative)
    """
    if os.getenv("CPREG_ENABLE", "0").strip() != "1":
        return float(os.getenv("CP_ALPHA", "0.07"))

    a0 = float(os.getenv("CPREG_ALPHA_START", os.getenv("CP_ALPHA", "0.07")))
    a1 = float(os.getenv("CPREG_ALPHA_END", "0.09"))
    warm = float(os.getenv("CPREG_WARMUP_FRAC", "0.50"))
    end = float(os.getenv("CPREG_RAMP_END_FRAC", "0.90"))
    if end <= warm: end = min(0.99, warm + 0.10)

    frac = _day_progress_frac(ts, tz_name=tz_name)
    if frac <= warm:
        a = a0
    elif frac >= end:
        a = a1
    else:
        t = (frac - warm) / (end - warm)
        a = a0 + (a1 - a0) * t

    slot2_mult = float(os.getenv("CPREG_SLOT2_MULT", "0.85"))
    if executed_today >= 1:
        a *= slot2_mult

    # clamp
    if a < 0.001: a = 0.001
    if a > 0.49: a = 0.49
    return float(a)
# ----------------------------------------------------------------------------- 
'@

  # Inject right after imports block (best-effort)
  $m = [regex]::Match($txt2, "(?s)\A(.*?\n)(\n|#)")
  if ($m.Success) {
    $pos = $m.Groups[1].Length
    $txt2 = $txt2.Insert($pos, "`n$inject`n")
  } else {
    $txt2 = "$inject`n`n$txt2"
  }
}

# 3) Patch call site: where CP gate is evaluated, set CP_ALPHA dynamically.
# We do a conservative textual injection: before calling compute_scores(... gate_mode == 'cp' ...)
# Insert: os.environ['CP_ALPHA']=str(cpreg_alpha(ts, executed_today))
if ($txt2 -notmatch "cpreg_alpha\(ts") {
  # Find a likely place where gate_mode_eff is used
  $pattern = "gate_mode_eff\s*==\s*['""]cp['""]"
  $idx = $txt2.IndexOf("gate_mode_eff == \"cp\"")
  if ($idx -lt 0) { $idx = $txt2.IndexOf("gate_mode_eff == 'cp'") }

  if ($idx -ge 0) {
    # inject a few lines just after the first occurrence line
    $lines = $txt2 -split "`n", -1
    $lineNo = 0
    for ($i=0; $i -lt $lines.Length; $i++) {
      if ($lines[$i] -match $pattern) { $lineNo = $i; break }
    }
    # Insert after this line (indent follow next line)
    $indent = ($lines[$lineNo] -replace "(\S.*)$","")  # leading whitespace
    $ins = @(
      "$indent# CPREG: schedule CP_ALPHA over day + slot-aware tightening",
      "$indent`$__alpha = cpreg_alpha(ts, executed_today, tz_name=tz_name)",
      "$indent`$os.environ['CP_ALPHA'] = str($__alpha)"
    )
    $newLines = @()
    for ($i=0; $i -lt $lines.Length; $i++) {
      $newLines += $lines[$i]
      if ($i -eq $lineNo) { $newLines += $ins }
    }
    $txt2 = ($newLines -join "`n")
  } else {
    Write-Host "[WARN] Não encontrei gate_mode_eff == 'cp' para injetar CPREG. Patch aplicado parcialmente (NameError fix + helpers)."
  }
}

Write-Utf8NoBomFile $target $txt2
Write-Host "OK: patched $target"

# quick syntax check
$py = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $py) {
  & $py -c "import py_compile; py_compile.compile(r'src/natbin/observe_signal_topk_perday.py', doraise=True); print('py_compile OK')"
} else {
  Write-Host "[WARN] .venv python.exe não encontrado, pulei py_compile"
}
