$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

$root = (Get-Location).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
Require-Path $py "Nao encontrei .venv. Rode init.ps1."

# (A) Migração do SQLite (preserva legado)
$script = @'
import sqlite3
from pathlib import Path

db_path = Path("runs") / "live_signals.sqlite3"
db_path.parent.mkdir(parents=True, exist_ok=True)

con = sqlite3.connect(db_path, timeout=30)
try:
    con.execute("PRAGMA journal_mode=WAL;")

    # existe tabela signals?
    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signals'")
    has_signals = cur.fetchone() is not None

    if has_signals:
        # checa colunas
        cols = [r[1] for r in con.execute("PRAGMA table_info(signals)").fetchall()]
        if "conf" in cols and "reason" in cols:
            print("SQLite: signals já tem schema novo. OK.")
        else:
            # renomeia para legado
            con.execute("ALTER TABLE signals RENAME TO signals_legacy")
            print("SQLite: signals renomeada para signals_legacy (preservado).")
    else:
        print("SQLite: tabela signals não existia. OK.")

    # cria signals_v2 (novo schema) se não existir
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS signals_v2 (
          dt_local TEXT NOT NULL,
          day TEXT NOT NULL,
          ts INTEGER NOT NULL,
          proba_up REAL NOT NULL,
          conf REAL NOT NULL,
          regime_ok INTEGER NOT NULL,
          threshold REAL NOT NULL,
          rank_in_day INTEGER NOT NULL,
          executed_today INTEGER NOT NULL,
          action TEXT NOT NULL,
          reason TEXT NOT NULL,
          close REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_signals_v2_day_ts ON signals_v2(day, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_signals_v2_ts ON signals_v2(ts)")
    con.commit()

    print("SQLite: signals_v2 pronto.")
finally:
    con.close()
'@

& $py -c $script
if ($LASTEXITCODE -ne 0) { throw "Falhou migração do SQLite." }

# (B) Patch observe_signal_topk_perday.py para gravar em signals_v2
Require-Path "src\natbin\observe_signal_topk_perday.py" "Nao achei src\natbin\observe_signal_topk_perday.py"

$obsPath = "src\natbin\observe_signal_topk_perday.py"
$txt = Get-Content $obsPath -Raw

# troca CREATE/INSERT para signals_v2
$txt = $txt -replace "CREATE TABLE IF NOT EXISTS signals \(", "CREATE TABLE IF NOT EXISTS signals_v2 ("
$txt = $txt -replace "INSERT INTO signals\(", "INSERT INTO signals_v2("

Set-Content -Encoding UTF8 $obsPath $txt

# (C) Patch observe_loop.ps1: mensagem e nome de erro
Require-Path "observe_loop.ps1" "Nao achei observe_loop.ps1"
$loopPath = "observe_loop.ps1"
$loop = Get-Content $loopPath -Raw

# ajusta throw para o nome novo (só pra não confundir)
$loop = $loop -replace 'throw "observe_signal_latest falhou"', 'throw "observe_signal_topk_perday falhou"'
Set-Content -Encoding UTF8 $loopPath $loop

# preflight
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK. SQLite migrado e observe atualizado para signals_v2." -ForegroundColor Green
Write-Host "Teste agora: pwsh -ExecutionPolicy Bypass -File .\observe_loop.ps1 -Once" -ForegroundColor Yellow