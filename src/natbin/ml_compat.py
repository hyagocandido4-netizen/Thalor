from __future__ import annotations

"""Small ML compatibility helpers.

The project currently uses scikit-learn 1.5.x. With the pinned SciPy line,
``LogisticRegression`` configured with the default ``lbfgs`` solver emits a
SciPy deprecation warning about ``disp`` / ``iprint`` options inside the
optimizer bridge.

For Thalor we only use logistic regression as a compact *binary* baseline /
stacking model. ``liblinear`` is a stable solver for this use case, avoids the
SciPy warning path completely, and keeps the behaviour deterministic.
"""

from typing import Any

from sklearn.linear_model import LogisticRegression


DEFAULT_BINARY_LOGREG_SOLVER = "liblinear"


def build_binary_logreg(
    *,
    max_iter: int = 1000,
    class_weight: str | dict[int, float] | None = None,
    random_state: int | None = None,
    **extra: Any,
) -> LogisticRegression:
    params: dict[str, Any] = {
        "solver": DEFAULT_BINARY_LOGREG_SOLVER,
        "max_iter": int(max_iter),
    }
    if class_weight is not None:
        params["class_weight"] = class_weight
    if random_state is not None:
        params["random_state"] = int(random_state)
    params.update(extra)
    return LogisticRegression(**params)
