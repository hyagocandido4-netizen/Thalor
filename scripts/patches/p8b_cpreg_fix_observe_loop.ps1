<# 
P8b (v1): Fix observe_loop crash + CP coverage regulator (alpha schedule + slot-aware)
- Fix NameError: thr referenced at import-time in observe_signal_topk_perday.py (from covreg injection)
- Add CPREG (alpha schedule) + slot-aware alpha for gate_mode=cp
Safe patch: creates .bak timestamp and validates via py_compile.
#>
$ErrorActionPreference = "Stop"

function Get-PyExe {
  if (Test-Path ".\.venv\Scripts\python.exe") { return ".\.venv\Scripts\python.exe" }
  return "python"
}

$path = Join-Path (Get-Location) "src\natbin\observe_signal_topk_perday.py"
if (!(Test-Path $path)) { throw "Arquivo não encontrado: $path (rode na raiz do repo)" }

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_$ts"
Copy-Item -Path $path -Destination $bak -Force
Write-Host "Backup:" $bak

$txt = Get-Content -Raw -Encoding UTF8 $path

# --- Hotfix: remove NameError 'thr' at import-time (covreg default)
$txt = $txt.Replace('os.getenv("COVREG_THR_LO", str(thr))', 'os.getenv("COVREG_THR_LO", os.getenv("COVREG_THR_HI", "0.10"))')
$txt = $txt.Replace("os.getenv('COVREG_THR_LO', str(thr))", "os.getenv('COVREG_THR_LO', os.getenv('COVREG_THR_HI', '0.10'))")

# --- Inject CPREG helpers (only if not present)
if ($txt -notmatch "CPREG_ENABLE" -and $txt -notmatch "def cpreg_alpha") {
  $needle = "def main()"
  $idx = $txt.IndexOf($needle)
  if ($idx -lt 0) { throw "Não achei 'def main()' para injetar CPREG helpers." }

  $helpers = @'
# --- P8b: CP coverage regulator (alpha schedule + slot-aware) ---
def _cpreg_is_on() -> bool:
    v = str(os.getenv("CPREG_ENABLE", "0")).strip().lower()
    return v in ("1", "true", "yes", "on")

def _day_frac(dt: datetime) -> float:
    return (dt.hour * 3600 + dt.minute * 60 + dt.second) / 86400.0

def cpreg_alpha(*, base_alpha: float, tz: ZoneInfo, slot: int) -> float:
    """Alpha efetivo do conformal (gate_mode=cp).

    Ideia:
      - Começa mais conservador (alpha_start) -> win rate mais alto e evita "early quota capture"
      - Relaxa mais tarde (alpha_end) -> melhora coverage se ainda não operou
      - Slot>=2 pode ser mais conservador (slot2_mult) -> protege o 2o trade do dia
    """
    base_alpha = float(base_alpha)

    if not _cpreg_is_on():
        a = base_alpha
    else:
        a0 = float(os.getenv("CPREG_ALPHA_START", str(base_alpha)))
        a1 = float(os.getenv("CPREG_ALPHA_END", str(base_alpha)))
        warm = float(os.getenv("CPREG_WARMUP_FRAC", "0.50"))
        end = float(os.getenv("CPREG_RAMP_END_FRAC", "0.90"))

        dt = datetime.now(tz)
        f = _day_frac(dt)

        if end <= warm:
            a = a0
        elif f <= warm:
            a = a0
        elif f >= end:
            a = a1
        else:
            a = a0 + (a1 - a0) * ((f - warm) / (end - warm))

    # slot-aware: segundo trade mais conservador
    try:
        mult2 = float(os.getenv("CPREG_SLOT2_MULT", "0.85"))
    except Exception:
        mult2 = 0.85
    if int(slot) >= 2:
        a = float(a) * float(mult2)

    # clamp
    if a < 0.001:
        a = 0.001
    if a > 0.49:
        a = 0.49
    return float(a)
# --- /P8b ---
'@

  $txt = $txt.Insert($idx, $helpers + "`r`n`r`n")
  Write-Host "Injected CPREG helpers."
}

# --- Inject "apply alpha_eff" before the FIRST compute_scores call (safe)
if ($txt -notmatch "P8b: apply cpreg alpha") {
  $pos = $txt.IndexOf("compute_scores(")
  if ($pos -lt 0) { throw "Não achei 'compute_scores(' no observe_signal_topk_perday.py" }

  # Find line start
  $lineStart = $txt.LastIndexOf("`n", $pos)
  if ($lineStart -lt 0) { $lineStart = 0 } else { $lineStart += 1 }

  # Capture indentation of that line
  $indent = ""
  $i = $lineStart
  while ($i -lt $txt.Length) {
    $ch = $txt[$i]
    if ($ch -eq " " -or $ch -eq "`t") { $indent += $ch; $i += 1 } else { break }
  }

  $applyLines = @(
    "${indent}# P8b: apply cpreg alpha (slot-aware) for cp gate",
    "${indent}try:",
    "${indent}    gate_mode_eff = str(best.get(\"gate_mode\") or \"\").strip().lower()",
    "${indent}    if gate_mode_eff == \"cp\":",
    "${indent}        base_alpha = float(os.getenv(\"CP_ALPHA\", \"0.07\"))",
    "${indent}        day_now = datetime.now(tz).strftime(\"%Y-%m-%d\")",
    "${indent}        slot = executed_today_count(asset, day_now) + 1",
    "${indent}        alpha_eff = cpreg_alpha(base_alpha=base_alpha, tz=tz, slot=slot)",
    "${indent}        os.environ[\"CP_ALPHA\"] = f\"{alpha_eff:.6f}\"",
    "${indent}except Exception:",
    "${indent}    pass",
    ""
  )
  $apply = ($applyLines -join "`n") + "`n"

  $txt = $txt.Insert($lineStart, $apply)
  Write-Host "Injected alpha_eff apply block."
}

Set-Content -Path $path -Value $txt -Encoding UTF8

# --- Validate python syntax
$py = Get-PyExe
& $py -m py_compile $path
Write-Host "OK: observe_signal_topk_perday.py compila."
Write-Host "Done."
