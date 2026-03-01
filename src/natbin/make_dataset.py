from __future__ import annotations

from natbin.config2 import load_config
from natbin.dataset2 import build_dataset


def main():
    cfg = load_config()
    res = build_dataset(
        db_path=cfg.data.db_path,
        asset=cfg.data.asset,
        interval_sec=cfg.data.interval_sec,
        out_csv=cfg.phase2.dataset_path,
    )
    print("Dataset pronto:")
    print(f"  path: {res.path}")
    print(f"  rows: {res.n_rows}")
    print(f"  features: {len(res.feature_cols)}")


if __name__ == "__main__":
    main()