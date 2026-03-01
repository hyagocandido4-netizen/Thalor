$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

$root = (Get-Location).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
Require-Path $py "Nao encontrei .venv. Rode init.ps1."
Require-Path "src\natbin\observe_signal_topk_perday.py" "Nao achei src\natbin\observe_signal_topk_perday.py"

$path = "src\natbin\observe_signal_topk_perday.py"
$txt = Get-Content $path -Raw

# (A) Força cálculo robusto de day e dt_local (fallback seguro)
# Procura o bloco onde define last_day e substitui por uma versão robusta
$pattern = '(?s)# dia local do último ts.*?last_day\s*=\s*.*?\n'
if ($txt -match $pattern) {
  $replacement = @'
    # dia local do último ts (define o "por dia"); fallback seguro
    try:
        last_day = datetime.fromtimestamp(last_ts, tz=tz).strftime("%Y-%m-%d")
    except Exception:
        last_day = datetime.now(tz=tz).strftime("%Y-%m-%d")
'@
  $txt = [regex]::Replace($txt, $pattern, $replacement, 1)
}

# (B) Garante que row SEMPRE tenha 'day' e dt_local no tz (não naive)
# Substitui a construção do dict row por uma versão garantida
$rowPattern = '(?s)row\s*=\s*\{.*?\}\s*\n\s*# SQLite sempre'
if ($txt -match $rowPattern) {
  $rowReplacement = @'
    row = {
        "dt_local": datetime.now(tz=tz).isoformat(timespec="seconds"),
        "day": str(last_day),
        "ts": int(last_ts),
        "proba_up": float(proba_now),
        "conf": float(conf_now),
        "regime_ok": int(regime_now),
        "threshold": float(thr),
        "rank_in_day": int(rank_in_day),
        "executed_today": int(executed_n),
        "action": str(action),
        "reason": str(reason),
        "close": float(last["close"]),
    }

    # SQLite sempre
'@
  $txt = [regex]::Replace($txt, $rowPattern, $rowReplacement, 1)
} else {
  throw "Nao consegui localizar o bloco 'row = {...}' para patch. Me mande o conteúdo das linhas onde o row é montado."
}

Set-Content -Encoding UTF8 $path $txt

# (C) Preflight
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK. day/dt_local agora são garantidos (NOT NULL resolvido)." -ForegroundColor Green
Write-Host "Teste: pwsh -ExecutionPolicy Bypass -File .\observe_loop.ps1 -Once" -ForegroundColor Yellow