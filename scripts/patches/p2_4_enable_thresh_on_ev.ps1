param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Ative/crie a venv antes." }

$files = @(
  "src\natbin\observe_signal_topk_perday.py",
  "src\natbin\tune_multiwindow_topk.py",
  "src\natbin\paper_pnl_backtest.py",
  "config.yaml"
)

foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "Nao achei $f" }
}

# Backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\p2_4_thresh_on_ev_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($f in $files) {
  Copy-Item $f (Join-Path $backupDir ([IO.Path]::GetFileName($f))) -Force
}
Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

& $py -c @"
from pathlib import Path
import re
import yaml

def patch_observe(p: Path):
    txt = p.read_text(encoding="utf-8")

    # 1) Permitir 'ev' no thresh_on
    txt, n1 = re.subn(
        r'(?m)^(?P<ind>[ \t]*)if\s+thresh_on\s+not\s+in\s+\(\"score\",\s*\"conf\"\)\s*:\s*$',
        r'\g<ind>if thresh_on not in (\"score\", \"conf\", \"ev\"):',
        txt,
        count=1
    )
    if n1 == 0:
        # fallback (caso tenha aspas simples)
        txt, n1b = re.subn(
            r"(?m)^(?P<ind>[ \t]*)if\s+thresh_on\s+not\s+in\s+\('score',\s*'conf'\)\s*:\s*$",
            r"\g<ind>if thresh_on not in ('score', 'conf', 'ev'):",
            txt,
            count=1
        )
        n1 += n1b

    # 2) metric/cand: suportar score/conf/ev
    pat = (
        r'(?m)^(?P<ind>[ \t]*)metric\s*=\s*score\s*if\s*thresh_on\s*==\s*[\'"]score[\'"]\s*else\s*conf\s*$\n'
        r'(?P=ind)cand\s*=\s*mask\s*&\s*\(metric\s*>=\s*thr\)\s*$'
    )
    rep = (
        r'\g<ind>payout_gate = float(os.getenv(\"PAYOUT\", \"0.8\"))\n'
        r'\g<ind>ev_metric = score * payout_gate - (1.0 - score)\n'
        r'\g<ind>if thresh_on == \"score\":\n'
        r'\g<ind>    metric = score\n'
        r'\g<ind>elif thresh_on == \"conf\":\n'
        r'\g<ind>    metric = conf\n'
        r'\g<ind>else:\n'
        r'\g<ind>    metric = ev_metric\n'
        r'\g<ind>cand = mask & (metric >= thr)'
    )
    txt, n2 = re.subn(pat, rep, txt, count=1)
    if n2 == 0 and "ev_metric =" not in txt:
        raise RuntimeError("OBSERVE: nao achei o bloco metric/cand para patchar.")

    # 3) reason: incluir below_ev_threshold
    pat2 = (
        r'(?m)^(?P<ind>[ \t]*)elif\s+float\(metric\[now_i\]\)\s*<\s*thr\s*:\s*\n'
        r'(?P=ind)[ \t]*reason\s*=\s*[\'"]below_score_threshold[\'"]\s*if\s*thresh_on\s*==\s*[\'"]score[\'"]\s*else\s*[\'"]below_conf_threshold[\'"]\s*$'
    )
    rep2 = (
        r'\g<ind>elif float(metric[now_i]) < thr:\n'
        r'\g<ind>    if thresh_on == \"score\":\n'
        r'\g<ind>        reason = \"below_score_threshold\"\n'
        r'\g<ind>    elif thresh_on == \"conf\":\n'
        r'\g<ind>        reason = \"below_conf_threshold\"\n'
        r'\g<ind>    else:\n'
        r'\g<ind>        reason = \"below_ev_threshold\"'
    )
    txt, n3 = re.subn(pat2, rep2, txt, count=1)

    p.write_text(txt, encoding="utf-8")
    return (n1, n2, n3)

def patch_tuner(p: Path):
    txt = p.read_text(encoding="utf-8")

    # 1) choices thresh_on: add ev
    txt, n1 = re.subn(r'choices=\[\s*\"score\"\s*,\s*\"conf\"\s*\]', 'choices=["score","conf","ev"]', txt, count=1)
    if n1 == 0:
        txt, n1b = re.subn(r"choices=\[\s*'score'\s*,\s*'conf'\s*\]", "choices=['score','conf','ev']", txt, count=1)
        n1 += n1b

    # 2) threshold grid: usar keys diferentes quando thresh_on=ev
    pat = (
        r'(?ms)^(?P<ind>[ \t]*)tmin\s*=\s*float\(cfg\.get\(\"phase2\",\s*\{\}\)\.get\(\"threshold_min\".*?\)\)\s*\n'
        r'(?P=ind)tmax\s*=\s*float\(cfg\.get\(\"phase2\",\s*\{\}\)\.get\(\"threshold_max\".*?\)\)\s*\n'
        r'(?P=ind)tstep\s*=\s*float\(cfg\.get\(\"phase2\",\s*\{\}\)\.get\(\"threshold_step\".*?\)\)\s*\n'
        r'(?P=ind)thresholds\s*=\s*np\.round\(np\.arange\(tmin,\s*tmax\s*\+\s*1e-9,\s*tstep\),\s*2\)\s*$'
    )
    rep = (
        r'\g<ind>phase2 = cfg.get(\"phase2\", {}) or {}\n'
        r'\g<ind>if args.thresh_on == \"ev\":\n'
        r'\g<ind>    tmin = float(phase2.get(\"ev_threshold_min\", -0.05))\n'
        r'\g<ind>    tmax = float(phase2.get(\"ev_threshold_max\", 0.40))\n'
        r'\g<ind>    tstep = float(phase2.get(\"ev_threshold_step\", 0.01))\n'
        r'\g<ind>else:\n'
        r'\g<ind>    tmin = float(phase2.get(\"threshold_min\", 0.55))\n'
        r'\g<ind>    tmax = float(phase2.get(\"threshold_max\", 0.85))\n'
        r'\g<ind>    tstep = float(phase2.get(\"threshold_step\", 0.01))\n'
        r'\g<ind>thresholds = np.round(np.arange(tmin, tmax + 1e-9, tstep), 2)'
    )
    txt, n2 = re.subn(pat, rep, txt, count=1)
    if n2 == 0 and "ev_threshold_min" not in txt:
        raise RuntimeError("TUNER: nao achei bloco threshold grid para patchar.")

    # 3) metric selection: suportar ev
    pat2 = r'(?m)^(?P<ind>[ \t]*)metric\s*=\s*p\.score\s*if\s*args\.thresh_on\s*==\s*[\'"]score[\'"]\s*else\s*p\.conf\s*$'
    rep2 = (
        r'\g<ind>if args.thresh_on == \"score\":\n'
        r'\g<ind>    metric = p.score\n'
        r'\g<ind>elif args.thresh_on == \"conf\":\n'
        r'\g<ind>    metric = p.conf\n'
        r'\g<ind>else:\n'
        r'\g<ind>    metric = p.score * payout - (1.0 - p.score)'
    )
    txt, n3 = re.subn(pat2, rep2, txt, count=1)
    if n3 == 0 and "metric = p.score * payout" not in txt:
        raise RuntimeError("TUNER: nao achei linha metric = p.score if ... para patchar.")

    p.write_text(txt, encoding="utf-8")
    return (n1, n2, n3)

def patch_paper(p: Path):
    txt = p.read_text(encoding="utf-8")

    # 1) choices thresh_on: add ev
    txt, n1 = re.subn(r'choices=\[\s*\"score\"\s*,\s*\"conf\"\s*\]', 'choices=["score","conf","ev"]', txt, count=1)
    if n1 == 0:
        txt, n1b = re.subn(r"choices=\[\s*'score'\s*,\s*'conf'\s*\]", "choices=['score','conf','ev']", txt, count=1)
        n1 += n1b

    # 2) metric selection: suportar ev
    pat = r'(?m)^(?P<ind>[ \t]*)metric\s*=\s*score\s*if\s*args\.thresh_on\s*==\s*[\'"]score[\'"]\s*else\s*conf\s*$'
    rep = (
        r'\g<ind>if args.thresh_on == \"score\":\n'
        r'\g<ind>    metric = score\n'
        r'\g<ind>elif args.thresh_on == \"conf\":\n'
        r'\g<ind>    metric = conf\n'
        r'\g<ind>else:\n'
        r'\g<ind>    metric = score * args.payout - (1.0 - score)'
    )
    txt, n2 = re.subn(pat, rep, txt, count=1)
    if n2 == 0 and "metric = score * args.payout" not in txt:
        raise RuntimeError("PAPER: nao achei linha metric = score if ... para patchar.")

    p.write_text(txt, encoding="utf-8")
    return (n1, n2)

# aplica patches
obs = Path("src/natbin/observe_signal_topk_perday.py")
tun = Path("src/natbin/tune_multiwindow_topk.py")
pap = Path("src/natbin/paper_pnl_backtest.py")

o = patch_observe(obs)
t = patch_tuner(tun)
pa = patch_paper(pap)

# garante grid EV no config (non-destructive)
cfg_path = Path("config.yaml")
cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
phase2 = cfg.setdefault("phase2", {})
changed = False
if "ev_threshold_min" not in phase2:
    phase2["ev_threshold_min"] = -0.05
    changed = True
if "ev_threshold_max" not in phase2:
    phase2["ev_threshold_max"] = 0.40
    changed = True
if "ev_threshold_step" not in phase2:
    phase2["ev_threshold_step"] = 0.01
    changed = True
if changed:
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")

print("OK P2.4 THRESH_ON=ev enabled:", {"observe": o, "tuner": t, "paper": pa, "config_ev_grid_added": changed})
"@

if ($LASTEXITCODE -ne 0) { throw "Patch falhou." }

# compile
& $py -c "import py_compile; py_compile.compile(r'src/natbin/observe_signal_topk_perday.py', doraise=True); py_compile.compile(r'src/natbin/tune_multiwindow_topk.py', doraise=True); py_compile.compile(r'src/natbin/paper_pnl_backtest.py', doraise=True); print('OK: py_compile')"
if ($LASTEXITCODE -ne 0) { throw "py_compile falhou" }

& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: THRESH_ON=ev habilitado (live+tuner+paper)." -ForegroundColor Green
Write-Host "Dica: EV>0 equivale a break-even. Agora tune/paper/observe com --thresh-on ev." -ForegroundColor Yellow