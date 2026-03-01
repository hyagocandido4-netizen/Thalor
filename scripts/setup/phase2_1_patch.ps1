param(
  [switch]$NoRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Content
  )
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Read-FileRaw {
  param([Parameter(Mandatory=$true)][string]$Path)
  return [System.IO.File]::ReadAllText($Path)
}

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

$root = (Get-Location).Path
$py = Join-Path $root ".venv\Scripts\python.exe"

Require-Path $py "Nao encontrei $py. Rode primeiro o init.ps1 (Fase 1) para criar a venv."
Require-Path "config.yaml" "Nao encontrei config.yaml. Rode este script na raiz do projeto (onde esta config.yaml)."
Require-Path "src\natbin\dataset2.py" "Nao encontrei src\natbin\dataset2.py. Rode primeiro o phase2_bootstrap.ps1."
Require-Path "src\natbin\train_walkforward.py" "Nao encontrei src\natbin\train_walkforward.py. Rode primeiro o phase2_bootstrap.ps1."

Write-Host "== Phase 2.1 Patch ==" -ForegroundColor Cyan

# -------------------------
# (1) Patch config.yaml: thresholds mais baixos (mais trades) + mantém o resto
# -------------------------
$pyPatchConfig = @'
import yaml
from pathlib import Path

p = Path("config.yaml")
cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
cfg.setdefault("phase2", {})
cfg["phase2"].update({
    "threshold_min": 0.52,
    "threshold_max": 0.75,
    "threshold_step": 0.01,
})
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("config.yaml: phase2 thresholds atualizados (0.52–0.75).")
'@
& $py -c $pyPatchConfig
if ($LASTEXITCODE -ne 0) { throw "Falhou patch do config.yaml" }

# -------------------------
# (2) Patch dataset2.py: adiciona SMA/BB/MACD antes do return (idempotente)
# -------------------------
$datasetPath = "src\natbin\dataset2.py"
$ds = Read-FileRaw $datasetPath

if ($ds -match "f_bb_width20" -or $ds -match "f_macdhist") {
  Write-Host "dataset2.py: features extras ja presentes. (skip)" -ForegroundColor DarkGray
} else {
  $insertBlock = @'
    # medias / zscore / bandas
    m20 = g["close"].rolling(20, min_periods=20).mean()
    s20 = g["close"].rolling(20, min_periods=20).std()
    m50 = g["close"].rolling(50, min_periods=50).mean()

    g["f_sma20"] = (g["close"] / m20) - 1.0
    g["f_sma50"] = (g["close"] / m50) - 1.0
    g["f_z20"] = (g["close"] - m20) / s20
    g["f_bb_width20"] = (4.0 * s20) / g["close"]  # ~2 desvios pra cima + 2 pra baixo

    # MACD (12,26,9)
    ema12 = g["close"].ewm(span=12, adjust=False).mean()
    ema26 = g["close"].ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()

    g["f_macd"] = macd
    g["f_macdsig"] = signal
    g["f_macdhist"] = macd - signal
'@

  # insere imediatamente antes do "return g" com indent correto
  $pattern = "(?m)^\s*return g\s*$"
  if ($ds -notmatch $pattern) { throw "dataset2.py: nao achei 'return g' para inserir o bloco." }

  $ds2 = [regex]::Replace($ds, $pattern, ($insertBlock + "`r`n    return g"), 1)
  Write-Utf8NoBomFile $datasetPath $ds2
  Write-Host "dataset2.py: patch aplicado (SMA/BB/MACD)." -ForegroundColor Green
}

# -------------------------
# (3) Patch train_walkforward.py: regra de escolha por minimo de trades + coverage alvo (idempotente)
# -------------------------
$trainPath = "src\natbin\train_walkforward.py"
$tw = Read-FileRaw $trainPath

if ($tw -match "MIN_TRADES\s*=\s*300" -or $tw -match "best_accuracy_cov0\.1-2%") {
  Write-Host "train_walkforward.py: regra Phase 2.1 ja presente. (skip)" -ForegroundColor DarkGray
} else {
  $oldBlockPattern = [regex]::Escape('candidates = thr_df[(thr_df["coverage"] >= 0.002) & (thr_df["coverage"] <= 0.03)].copy()') +
    '.*?' +
    [regex]::Escape('rule = "best_accuracy_with_cov_0.2%-3%"')

  if ($tw -notmatch $oldBlockPattern) {
    throw "train_walkforward.py: nao encontrei o bloco antigo de candidates/best para substituir."
  }

  $newBlock = @'
# --- Phase 2.1 selection: poucos sinais, mas mensurável ---
COV_MIN = 0.001   # 0.10%
COV_MAX = 0.02    # 2.00%
MIN_TRADES = 300  # mínimo de trades totais (somando folds)

candidates = thr_df[
    (thr_df["coverage"] >= COV_MIN) &
    (thr_df["coverage"] <= COV_MAX) &
    (thr_df["taken"] >= MIN_TRADES)
].copy()

if candidates.empty:
    # fallback: melhor acurácia, mas com pelo menos algum número de trades
    candidates2 = thr_df[thr_df["taken"] >= 50].copy()
    if candidates2.empty:
        best = thr_df.iloc[0]
        rule = "fallback_best_overall"
    else:
        best = candidates2.iloc[0]
        rule = "fallback_best_accuracy_min50trades"
else:
    best = candidates.iloc[0]
    rule = "best_accuracy_cov0.1-2%_min300trades"
'@

  $tw2 = [regex]::Replace($tw, $oldBlockPattern, $newBlock, 1, [System.Text.RegularExpressions.RegexOptions]::Singleline)
  Write-Utf8NoBomFile $trainPath $tw2
  Write-Host "train_walkforward.py: patch aplicado (coverage alvo + minimo trades)." -ForegroundColor Green
}

# -------------------------
# (4) Rebuild + rerun (default)
# -------------------------
Write-Host "Preflight: compileall..." -ForegroundColor Cyan
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

if (-not $NoRun) {
  Write-Host "`nRodando: make_dataset..." -ForegroundColor Cyan
  & $py -m natbin.make_dataset
  if ($LASTEXITCODE -ne 0) { throw "make_dataset falhou" }

  Write-Host "`nRodando: train_walkforward..." -ForegroundColor Cyan
  & $py -m natbin.train_walkforward
  if ($LASTEXITCODE -ne 0) { throw "train_walkforward falhou" }

  Write-Host "`nPhase 2.1 concluida." -ForegroundColor Green
} else {
  Write-Host "`nPatches aplicados. Para rodar:" -ForegroundColor Yellow
  Write-Host "  .\.venv\Scripts\python.exe -m natbin.make_dataset"
  Write-Host "  .\.venv\Scripts\python.exe -m natbin.train_walkforward"
}