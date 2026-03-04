# Auto hour-threshold CLI wrapper (Package F)
from __future__ import annotations

import json

from .autos.hour_policy import compute_hour_threshold


def main() -> None:
    out = compute_hour_threshold()
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
