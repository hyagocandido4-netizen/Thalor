from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
import py_compile


MARK = "# --- P11: incremental dataset build (skip/full/incremental) ---"


def find_repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    raise SystemExit("Não encontrei .git. Rode isso dentro do repo (ex: C:\\Users\\hyago\\Documents\\bot).")


def backup_file(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def main() -> None:
    root = find_repo_root()
    target = root / "src" / "natbin" / "dataset2.py"
    if not target.exists():
        raise SystemExit(f"Arquivo não encontrado: {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")
    if MARK in txt:
        print("[P11] Já aplicado (marker encontrado). Nada a fazer.")
        return

    bkp = backup_file(target)
    print(f"[P11] Backup: {bkp}")

    patch = r'''
# --- P11: incremental dataset build (skip/full/incremental) ---
import os as _os
import json as _json
from datetime import datetime as _dt


def _p11_truthy(v: str | None) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s not in ("0", "false", "no", "off", "")


def _p11_meta_path(out_csv: str) -> Path:
    # ex: data/dataset_phase2.csv.meta.json
    p = Path(out_csv)
    return p.with_suffix(p.suffix + ".meta.json")


def _p11_db_max_ts(db_path: str, asset: str, interval_sec: int) -> int:
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT MAX(ts) FROM candles WHERE asset=? AND interval_sec=?",
            (asset, int(interval_sec)),
        )
        r = cur.fetchone()
        return int(r[0] or 0)
    finally:
        con.close()


def _p11_read_meta(meta_p: Path) -> dict | None:
    try:
        if not meta_p.exists():
            return None
        return _json.loads(meta_p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _p11_write_meta(meta_p: Path, payload: dict) -> None:
    try:
        meta_p.parent.mkdir(parents=True, exist_ok=True)
        tmp = meta_p.with_suffix(meta_p.suffix + ".tmp")
        tmp.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(meta_p)
    except Exception:
        pass


def _p11_count_rows_fast(csv_path: str) -> int:
    # conta linhas (rápido o bastante para ~100k)
    try:
        with open(csv_path, "rb") as f:
            n = sum(1 for _ in f)
        return max(0, n - 1)  # desconta header
    except Exception:
        return 0


def _p11_read_dataset_state(out_csv: str) -> tuple[int, int | None, list[str] | None]:
    """
    retorna: (last_ts, n_rows, feature_cols)
    tenta meta first; fallback para CSV.
    """
    meta_p = _p11_meta_path(out_csv)
    meta = _p11_read_meta(meta_p)
    if meta:
        try:
            last_ts = int(meta.get("dataset_last_ts") or 0)
        except Exception:
            last_ts = 0
        try:
            n_rows = int(meta.get("n_rows")) if meta.get("n_rows") is not None else None
        except Exception:
            n_rows = None
        feat = meta.get("feature_cols")
        if isinstance(feat, list):
            feat_cols = [str(x) for x in feat if str(x).startswith("f_")]
        else:
            feat_cols = None
        return last_ts, n_rows, feat_cols

    # fallback: lê do CSV
    try:
        dfts = pd.read_csv(out_csv, usecols=["ts"])
        if dfts.empty:
            return 0, 0, None
        last_ts = int(dfts["ts"].max())
        # pega cols lendo só header
        head = pd.read_csv(out_csv, nrows=1)
        feat_cols = [c for c in head.columns if c.startswith("f_")]
        return last_ts, None, feat_cols
    except Exception:
        return 0, None, None


def _p11_load_candles_from(db_path: str, asset: str, interval_sec: int, ts_from: int) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT ts, open, high, low, close, volume
            FROM candles
            WHERE asset = ? AND interval_sec = ? AND ts >= ?
            ORDER BY ts ASC
            """,
            con,
            params=(asset, int(interval_sec), int(ts_from)),
        )
    finally:
        con.close()

    if df.empty:
        return df

    df["ts"] = df["ts"].astype("int64")
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype("float64")
    df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce")
    return df


# guarda a versão FULL original
_p11_full_build_dataset = build_dataset


def build_dataset(db_path: str, asset: str, interval_sec: int, out_csv: str) -> DatasetBuildResult:
    """
    P11:
      - DATASET_INCREMENTAL=1 (default) => incremental/skip
      - DATASET_WARMUP_CANDLES=300 (default) => janela para recomputar features com segurança
      - DATASET_MAX_GAP_CANDLES=5000 (default) => se dataset estiver muito atrasado, faz FULL rebuild
    """
    inc_enabled = _p11_truthy(_os.getenv("DATASET_INCREMENTAL", "1"))
    step = int(interval_sec)

    meta_p = _p11_meta_path(out_csv)
    out_p = Path(out_csv)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    db_max = _p11_db_max_ts(db_path, asset, step)
    expected_last = int(db_max - step) if db_max > 0 else 0

    # se não existe dataset ainda => FULL
    if not inc_enabled or (not out_p.exists()):
        res = _p11_full_build_dataset(db_path, asset, step, out_csv)
        # escreve meta
        try:
            dfts = pd.read_csv(out_csv, usecols=["ts"])
            last_ts = int(dfts["ts"].max()) if not dfts.empty else 0
        except Exception:
            last_ts = 0
        feat_cols = list(res.feature_cols)
        _p11_write_meta(meta_p, {
            "built_at": _dt.utcnow().isoformat(timespec="seconds") + "Z",
            "mode": "full",
            "db_path": db_path,
            "asset": asset,
            "interval_sec": step,
            "db_max_ts": int(db_max),
            "expected_last_ts": int(expected_last),
            "dataset_last_ts": int(last_ts),
            "n_rows": int(res.n_rows),
            "feature_cols": feat_cols,
        })
        return res

    # tenta state rápido
    last_ts, n_rows, feat_cols = _p11_read_dataset_state(out_csv)

    # SKIP se já está up-to-date
    tol = 2
    if expected_last > 0 and last_ts >= (expected_last - tol):
        if n_rows is None:
            n_rows = _p11_count_rows_fast(out_csv)
        if feat_cols is None:
            try:
                head = pd.read_csv(out_csv, nrows=1)
                feat_cols = [c for c in head.columns if c.startswith("f_")]
            except Exception:
                feat_cols = []
        _p11_write_meta(meta_p, {
            "built_at": _dt.utcnow().isoformat(timespec="seconds") + "Z",
            "mode": "skip",
            "db_path": db_path,
            "asset": asset,
            "interval_sec": step,
            "db_max_ts": int(db_max),
            "expected_last_ts": int(expected_last),
            "dataset_last_ts": int(last_ts),
            "n_rows": int(n_rows or 0),
            "feature_cols": list(feat_cols or []),
        })
        print(f"[P11] Dataset up-to-date (skip). last_ts={last_ts} expected_last={expected_last}")
        return DatasetBuildResult(path=out_csv, n_rows=int(n_rows or 0), feature_cols=list(feat_cols or []))

    # se está MUITO atrasado, FULL (segurança)
    warmup = int(_os.getenv("DATASET_WARMUP_CANDLES", "300"))
    max_gap = int(_os.getenv("DATASET_MAX_GAP_CANDLES", "5000"))
    if last_ts > 0 and expected_last > 0:
        gap_candles = int((expected_last - last_ts) // max(1, step))
        if gap_candles > max_gap:
            print(f"[P11] Dataset muito atrasado (gap {gap_candles} candles). Fazendo FULL rebuild...")
            res = _p11_full_build_dataset(db_path, asset, step, out_csv)
            try:
                dfts = pd.read_csv(out_csv, usecols=["ts"])
                last_ts2 = int(dfts["ts"].max()) if not dfts.empty else 0
            except Exception:
                last_ts2 = 0
            _p11_write_meta(meta_p, {
                "built_at": _dt.utcnow().isoformat(timespec="seconds") + "Z",
                "mode": "full_gap",
                "db_path": db_path,
                "asset": asset,
                "interval_sec": step,
                "db_max_ts": int(db_max),
                "expected_last_ts": int(expected_last),
                "dataset_last_ts": int(last_ts2),
                "n_rows": int(res.n_rows),
                "feature_cols": list(res.feature_cols),
                "gap_candles": gap_candles,
            })
            return res

    # INCREMENTAL: recomputa só o final com warmup
    ts_from = max(0, int(last_ts - warmup * step)) if last_ts > 0 else max(0, int(expected_last - warmup * step))
    df = _p11_load_candles_from(db_path, asset, step, ts_from)

    if df.empty:
        # fallback safe
        print("[P11] Sem candles no range incremental. Fazendo FULL rebuild...")
        res = _p11_full_build_dataset(db_path, asset, step, out_csv)
        return res

    df = _snap_ts(df, step)
    df = _add_sessions(df, step)

    entry_open = df["open"].shift(-1)
    expiry_close = df["close"].shift(-1)
    same_sess_next = df["session_id"].shift(-1) == df["session_id"]
    gap_next = df["ts"].shift(-1) - df["ts"]
    gap_next_ok = (gap_next >= (step - tol)) & (gap_next <= (step + tol))

    y = (expiry_close > entry_open).astype("float64")
    y[~same_sess_next] = np.nan
    y[~gap_next_ok] = np.nan
    df["y_open_close"] = y

    df = df.groupby("session_id", group_keys=False).apply(_build_features_one_session)
    feature_cols = [c for c in df.columns if c.startswith("f_")]
    feature_cols = _cleanup_features(df, feature_cols)

    keep_cols = ["ts", "open", "high", "low", "close", "volume", "session_id", "y_open_close"] + feature_cols
    out_new = df[keep_cols].copy()
    out_new = out_new.dropna(subset=["y_open_close"] + feature_cols).reset_index(drop=True)

    if out_new.empty:
        print("[P11] out_new vazio (provável warmup insuficiente ou gaps). Fazendo FULL rebuild...")
        res = _p11_full_build_dataset(db_path, asset, step, out_csv)
        return res

    # merge seguro: só substitui a partir do primeiro ts realmente presente no out_new
    replace_from_ts = int(out_new["ts"].min())

    try:
        old = pd.read_csv(out_csv)
    except Exception:
        old = pd.DataFrame()

    if (not old.empty) and ("ts" in old.columns):
        old_keep = old[old["ts"] < replace_from_ts].copy()
        merged = pd.concat([old_keep, out_new], ignore_index=True)
    else:
        merged = out_new

    merged = merged.drop_duplicates(subset=["ts"], keep="last").sort_values("ts").reset_index(drop=True)
    merged.to_csv(out_csv, index=False)

    last_ts3 = int(merged["ts"].max()) if not merged.empty else 0
    feat_cols_final = [c for c in merged.columns if c.startswith("f_")]

    _p11_write_meta(meta_p, {
        "built_at": _dt.utcnow().isoformat(timespec="seconds") + "Z",
        "mode": "incremental",
        "db_path": db_path,
        "asset": asset,
        "interval_sec": step,
        "db_max_ts": int(db_max),
        "expected_last_ts": int(expected_last),
        "dataset_last_ts": int(last_ts3),
        "n_rows": int(merged.shape[0]),
        "feature_cols": feat_cols_final,
        "warmup_candles": int(warmup),
        "ts_from": int(ts_from),
        "replace_from_ts": int(replace_from_ts),
    })

    print(f"[P11] Dataset incremental ok. replace_from_ts={replace_from_ts} rows={merged.shape[0]}")
    return DatasetBuildResult(path=out_csv, n_rows=int(merged.shape[0]), feature_cols=feat_cols_final)

# --- /P11 ---
'''
    # garante newline antes de anexar
    if not txt.endswith("\n"):
        txt += "\n"
    txt2 = txt + "\n" + patch

    target.write_text(txt2, encoding="utf-8")

    # sanity: compilar o arquivo
    py_compile.compile(str(target), doraise=True)
    print("[P11] OK: patch aplicado e py_compile passou.")


if __name__ == "__main__":
    main()