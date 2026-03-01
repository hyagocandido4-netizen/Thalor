param(
  [double]$Min = 0.55,
  [double]$Max = 0.70,
  [double]$Step = 0.01
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Resolve python (prioriza venv)
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# Validações básicas
if ($Min -le 0 -or $Min -ge 1) { throw "Min invalido: $Min (use 0<Min<1)" }
if ($Max -le 0 -or $Max -ge 1) { throw "Max invalido: $Max (use 0<Max<1)" }
if ($Min -ge $Max) { throw "Min ($Min) precisa ser < Max ($Max)" }
if ($Step -le 0) { throw "Step invalido: $Step (use Step>0)" }
if ($Step -ge ($Max - $Min)) { throw "Step ($Step) muito grande para o range (Max-Min=$($Max-$Min))" }

# Backup do config.yaml
if (-not (Test-Path ".\config.yaml")) { throw "Nao achei .\config.yaml" }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = ".\config_backup_scoregrid_{0}.yaml" -f $stamp
Copy-Item ".\config.yaml" $backup -Force

# Evita problema de vírgula decimal (pt-BR) ao passar para Python
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$MinS = $Min.ToString($inv)
$MaxS = $Max.ToString($inv)
$StepS = $Step.ToString($inv)

$code = @"
import yaml
from pathlib import Path

p = Path("config.yaml")
cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
phase2 = cfg.setdefault("phase2", {})

old = {
  "threshold_min": phase2.get("threshold_min", None),
  "threshold_max": phase2.get("threshold_max", None),
  "threshold_step": phase2.get("threshold_step", None),
}

phase2["threshold_min"] = float("$MinS")
phase2["threshold_max"] = float("$MaxS")
phase2["threshold_step"] = float("$StepS")

p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")

print("OK: phase2 threshold grid atualizado (para THRESH_ON=score).")
print("Backup:", "$backup")
print("Antes:", old)
print("Agora:", {
  "threshold_min": phase2["threshold_min"],
  "threshold_max": phase2["threshold_max"],
  "threshold_step": phase2["threshold_step"],
})
"@

& $py -c $code
if ($LASTEXITCODE -ne 0) { throw "Falhou ao atualizar config.yaml via python" }

Write-Host ""
Write-Host "Pronto. Isso NAO muda o best.threshold; só muda o grid do tuner." -ForegroundColor Green
Write-Host "Agora rode o tuner (score):" -ForegroundColor Yellow
Write-Host "  .\.venv\Scripts\python.exe -m natbin.tune_multiwindow_topk --k 2 --windows 6 --window-days 20 --thresh-on score --min-total-trades 15 --min-trades-per-window 1" -ForegroundColor Yellow