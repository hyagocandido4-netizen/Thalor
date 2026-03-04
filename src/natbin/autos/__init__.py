"""Auto-controller policy layer.

Package F of the refactor extracts summary loading, common parsing helpers and
policy computations for the auto controllers into a dedicated namespace.  The
CLI modules (`auto_volume.py`, `auto_isoblend.py`, `auto_hourthr.py`) remain as
thin wrappers so behavior stays stable while architecture becomes testable.
"""

from .common import as_float, as_int, break_even_from_payout, repo_runs_dir, write_json_atomic
from .summary_loader import SummaryScanResult, collect_checked_summaries

__all__ = [
    "as_float",
    "as_int",
    "break_even_from_payout",
    "repo_runs_dir",
    "write_json_atomic",
    "SummaryScanResult",
    "collect_checked_summaries",
]
