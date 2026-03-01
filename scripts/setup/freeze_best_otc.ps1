$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei .venv\Scripts\python.exe" }
if (-not (Test-Path "config.yaml")) { throw "Nao achei config.yaml" }
if (-not (Test-Path "runs")) { throw "Nao achei runs/" }

$freeze = @'
import json, yaml
from pathlib import Path

runs = Path("runs")
tunes = sorted(runs.glob("tune_v2_*"), key=lambda p: p.name, reverse=True)
if not tunes:
    raise SystemExit("Nao achei runs/tune_v2_*.")

t = tunes[0]
best = json.loads((t/"tune_summary.json").read_text(encoding="utf-8"))["best"]

p = Path("config.yaml")
cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
cfg["best"] = {
  "tune_dir": str(t).replace("\\","/"),
  "threshold": float(best["threshold"]),
  "bounds": {"vol_lo": float(best["vol_lo"]), "vol_hi": float(best["vol_hi"]),
             "bb_lo": float(best["bb_lo"]), "bb_hi": float(best["bb_hi"]),
             "atr_lo": float(best["atr_lo"]), "atr_hi": float(best["atr_hi"])},
  "notes": "Frozen from latest tune_v2 (OTC)."
}
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("Frozen best into config.yaml:", cfg["best"])
'@

& $py -c $freeze
if ($LASTEXITCODE -ne 0) { throw "Freeze best falhou" }

Write-Host "OK. best congelado no config.yaml." -ForegroundColor Green