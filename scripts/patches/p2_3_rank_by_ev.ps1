param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Ative/crie a venv antes." }

$files = @(
  "src\natbin\observe_signal_topk_perday.py",
  "src\natbin\tune_multiwindow_topk.py",
  "src\natbin\paper_pnl_backtest.py"
)

foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "Nao achei $f" }
}

# Backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\p2_3_rank_by_ev_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($f in $files) {
  Copy-Item $f (Join-Path $backupDir ([IO.Path]::GetFileName($f))) -Force
}
Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

# Patch via Python
& $py -c @"
from pathlib import Path
import re

def patch_observe(path: Path):
    txt = path.read_text(encoding="utf-8")

    # 1) Rank do dia: trocar score -> EV (score->EV usando payout)
    if "rank = score * payout_rank - (1.0 - score)" not in txt:
        pat = r'(?m)^(?P<ind>[ \t]*)order\s*=\s*idx\s*\[\s*np\.argsort\(\s*-\s*score\s*\)\s*\]\s*$'
        rep = (
            r'\g<ind>payout_rank = float(os.getenv("PAYOUT", "0.8"))\n'
            r'\g<ind>rank = score * payout_rank - (1.0 - score)\n'
            r'\g<ind>order = idx[np.argsort(-rank)]'
        )
        txt, n = re.subn(pat, rep, txt, count=1)
        if n == 0 and "order = idx[np.argsort(-rank)]" not in txt:
            raise RuntimeError("OBSERVE: nao achei a linha order = idx[np.argsort(-score)] para patchar.")
    else:
        n = 0

    # 2) EV no row: guardar EV mesmo quando HOLD (pra análise)
    #    antes: ev = <expr> if action != "HOLD" else 0.0
    pat_ev = r'(?m)^(?P<ind>[ \t]*)ev\s*=\s*(?P<expr>.+?)\s*if\s*action\s*!=\s*["\']HOLD["\']\s*else\s*0\.0\s*$'
    txt, n_ev = re.subn(pat_ev, r'\g<ind>ev = \g<expr>', txt, count=1)

    path.write_text(txt, encoding="utf-8")
    return n, n_ev

def patch_tune(path: Path):
    txt = path.read_text(encoding="utf-8")

    # 1) import os (precisa pra PAYOUT env)
    if re.search(r'(?m)^import os\s*$', txt) is None:
        txt, n_os = re.subn(
            r'(?m)^from __future__ import annotations\s*$',
            'from __future__ import annotations\n\nimport os',
            txt,
            count=1
        )
        if n_os == 0:
            raise RuntimeError("TUNER: nao consegui inserir import os.")
    else:
        n_os = 0

    # 2) define payout após parse_args (env PAYOUT, default 0.8)
    if 'payout = float(os.getenv("PAYOUT", "0.8"))' not in txt:
        pat = r'(?m)^(?P<ind>[ \t]*)args\s*=\s*ap\.parse_args\(\)\s*$'
        rep = r'\g<ind>args = ap.parse_args()\n\g<ind>payout = float(os.getenv("PAYOUT", "0.8"))'
        txt, n_pay = re.subn(pat, rep, txt, count=1)
        if n_pay == 0:
            raise RuntimeError("TUNER: nao achei args = ap.parse_args() para inserir payout.")
    else:
        n_pay = 0

    # 3) ranking: passar EV em vez de score no online topk
    if "p.score * payout - (1.0 - p.score)" not in txt:
        txt, n_call = re.subn(
            r'simulate_online_topk\(\s*p\.score\s*,',
            'simulate_online_topk(p.score * payout - (1.0 - p.score),',
            txt,
            count=1
        )
        if n_call == 0:
            raise RuntimeError("TUNER: nao achei chamada simulate_online_topk(p.score, ...).")
    else:
        n_call = 0

    path.write_text(txt, encoding="utf-8")
    return n_os, n_pay, n_call

def patch_paper(path: Path):
    txt = path.read_text(encoding="utf-8")

    # ranking: passar EV em vez de score no online day
    if "score * args.payout - (1.0 - score)" not in txt:
        txt, n_call = re.subn(
            r'simulate_online_day\(\s*score\s*,',
            'simulate_online_day(score * args.payout - (1.0 - score),',
            txt,
            count=1
        )
        if n_call == 0:
            raise RuntimeError("PAPER: nao achei chamada simulate_online_day(score, ...).")
    else:
        n_call = 0

    path.write_text(txt, encoding="utf-8")
    return n_call

obs = Path(r"src/natbin/observe_signal_topk_perday.py")
tun = Path(r"src/natbin/tune_multiwindow_topk.py")
pap = Path(r"src/natbin/paper_pnl_backtest.py")

n_order, n_ev = patch_observe(obs)
n_os, n_pay, n_call_t = patch_tune(tun)
n_call_p = patch_paper(pap)

print("OK P2.3 rank-by-EV applied:", {
  "observe_order_patch": n_order,
  "observe_ev_patch": n_ev,
  "tune_import_os": n_os,
  "tune_insert_payout": n_pay,
  "tune_call_patch": n_call_t,
  "paper_call_patch": n_call_p,
})
"@

if ($LASTEXITCODE -ne 0) { throw "Patch Python falhou." }

# Compile rápido
& $py -c "import py_compile; py_compile.compile(r'src/natbin/observe_signal_topk_perday.py', doraise=True); py_compile.compile(r'src/natbin/tune_multiwindow_topk.py', doraise=True); py_compile.compile(r'src/natbin/paper_pnl_backtest.py', doraise=True); print('OK: py_compile 3 files')"
if ($LASTEXITCODE -ne 0) { throw "py_compile falhou" }

& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: P2.3 aplicado (ranking por EV)." -ForegroundColor Green
Write-Host ""
Write-Host "Teste recomendado:" -ForegroundColor Yellow
Write-Host "  $py -m natbin.paper_pnl_backtest --k 1 --holdout-days 60 --payout 0.8 --gate-mode meta --meta-model hgb --thresh-on score" -ForegroundColor Yellow
Write-Host "  $py -m natbin.tune_multiwindow_topk --k 1 --windows 2 --window-days 60 --gate-mode meta --meta-model hgb --thresh-on score --min-total-trades 20 --min-trades-per-window 5 --update-config" -ForegroundColor Yellow
Write-Host "  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once" -ForegroundColor Yellow