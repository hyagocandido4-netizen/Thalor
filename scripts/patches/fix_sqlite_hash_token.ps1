$ErrorActionPreference="Stop"
Set-StrictMode -Version Latest

$path = ".\src\natbin\observe_signal_topk_perday.py"
if(-not (Test-Path $path)){ throw "Nao achei $path" }

$txt = Get-Content $path -Raw

# Substitui a função ensure_signals_v2 inteira por uma versão limpa
$pattern = '(?s)def ensure_signals_v2\(con: sqlite3\.Connection\) -> None:\s*.*?\n\s*def '
$replacement = @'
def ensure_signals_v2(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL;")
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

    # migração de colunas extras (auditabilidade)
    existing = {r[1] for r in con.execute("PRAGMA table_info(signals_v2)").fetchall()}
    add_cols = {
        "asset": "asset TEXT",
        "model_version": "model_version TEXT",
        "train_rows": "train_rows INTEGER",
        "train_end_ts": "train_end_ts INTEGER",
        "best_source": "best_source TEXT",
    }
    for name, ddl in add_cols.items():
        if name not in existing:
            con.execute(f"ALTER TABLE signals_v2 ADD COLUMN {ddl}")

def 
'@

if ($txt -notmatch $pattern) {
  throw "Nao consegui localizar a funcao ensure_signals_v2 para substituir."
}

$txt2 = [regex]::Replace($txt, $pattern, $replacement, 1)
Set-Content -Encoding UTF8 $path $txt2

# preflight
.\.venv\Scripts\python.exe -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK. ensure_signals_v2 corrigida (sem token # na SQL)." -ForegroundColor Green
Write-Host "Teste: pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once" -ForegroundColor Yellow