$ErrorActionPreference="Stop"
Set-StrictMode -Version Latest

$path = ".\src\natbin\observe_signal_topk_perday.py"
if(-not (Test-Path $path)){ throw "Nao achei $path" }

$txt = Get-Content $path -Raw

# 1) Garantir que signals_v2 ganhe colunas de auditabilidade (ALTER TABLE)
if($txt -notmatch "model_version"){
  $injection = @"
    # P0: colunas extras de auditabilidade (migração automática)
    try:
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
    except Exception:
        pass
"@

  # coloca logo depois do CREATE TABLE IF NOT EXISTS signals_v2 (...)
  $txt = $txt -replace '(CREATE TABLE IF NOT EXISTS signals_v2[\s\S]*?\)\s*\n)', "`$1$injection`n"
}

# 2) Injetar campos no row (antes de write_sqlite_signal(row))
if($txt -notmatch '"model_version"'){
  $rowInject = @'
    # P0: metadados auditáveis
    try:
        import subprocess
        model_version = subprocess.check_output(["git","rev-parse","--short","HEAD"]).decode().strip()
    except Exception:
        model_version = "unknown"
    row["asset"] = str(asset) if "asset" in locals() else ""
    row["model_version"] = model_version
    row["train_rows"] = int(len(train)) if "train" in locals() else int(len(df))
    row["train_end_ts"] = int(train["ts"].iloc[-1]) if "train" in locals() else int(df["ts"].iloc[-1])
    row["best_source"] = str(best.get("tune_dir","")) if "best" in locals() else ""
'@

  # insere imediatamente antes da chamada write_sqlite_signal(row)
  $txt = $txt -replace '(\s*)write_sqlite_signal\(row\)', "`$1$rowInject`n`$1write_sqlite_signal(row)"
}

Set-Content -Encoding UTF8 $path $txt
Write-Host "OK: observe_signal_topk_perday.py recebeu metadados + migração (P0)" -ForegroundColor Green