# Auto META_ISO_BLEND CLI wrapper (Package F)
from __future__ import annotations

import json

from .autos.isoblend_policy import compute_meta_iso_blend


def main() -> None:
    out = compute_meta_iso_blend()
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
