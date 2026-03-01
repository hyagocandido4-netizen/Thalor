$ErrorActionPreference="Stop"
Set-StrictMode -Version Latest

$path = ".\src\natbin\observe_signal_topk_perday.py"
if(-not (Test-Path $path)){ throw "Nao achei $path" }

$txt = Get-Content $path -Raw

$pattern = '(?s)def ensure_state_db\([^)]*\)\s*->\s*None:\s*.*?(?=def state_paths\()'
$replacement = @'
def ensure_state_db(con: sqlite3.Connection) -> None:
    # State DB serve só para limitar/evitar duplicidade (pode ser recriado).
    con.execute("PRAGMA journal_mode=WAL;")

    # Detecta schema legado (sem coluna asset) e migra de forma segura.
    cols = {r[1] for r in con.execute("PRAGMA table_info(executed)").fetchall()}
    if cols and "asset" not in cols:
        con.execute("ALTER TABLE executed RENAME TO executed_legacy")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS executed (
            asset TEXT NOT NULL,
            day TEXT NOT NULL,
            ts INTEGER NOT NULL,
            action TEXT NOT NULL,
            conf REAL NOT NULL,
            score REAL,
            PRIMARY KEY(asset, day, ts)
        )
        """
    )

    legacy = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='executed_legacy'"
    ).fetchone()
    if legacy:
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO executed(asset, day, ts, action, conf, score)
                SELECT 'LEGACY', day, ts, action, conf, NULL
                FROM executed_legacy
                """
            )
        except Exception:
            pass
        con.execute("DROP TABLE executed_legacy")

    con.execute("CREATE INDEX IF NOT EXISTS idx_exe_asset_day ON executed(asset, day)")
    con.commit()

'@

if ($txt -notmatch $pattern) { throw "Nao consegui localizar ensure_state_db() no arquivo para patch." }

$txt2 = [regex]::Replace($txt, $pattern, $replacement, 1)
Set-Content -Encoding UTF8 $path $txt2

.\.venv\Scripts\python.exe -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: ensure_state_db agora migra schema legado automaticamente." -ForegroundColor Green
Write-Host "Teste: pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once" -ForegroundColor Yellow