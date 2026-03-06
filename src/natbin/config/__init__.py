"""Typed configuration system (Package M foundation).

This package introduces a modern configuration layer built on Pydantic v2 and
``pydantic-settings``.

The goal in Package M v1 is *foundation*:

* Provide a strong, typed config model (with validation, defaults, and
  forbidden unknown keys inside the model).
* Load settings from multiple sources with a clear precedence.
* Support the legacy repo ``config.yaml`` without breaking existing installs.
* Emit an ``effective_config`` dump so every runtime cycle can be audited.

Nothing in this package should perform network access.
"""

from .models import (  # noqa: F401
    AssetSettings,
    AutosSettings,
    BrokerSettings,
    DecisionSettings,
    FailsafeSettings,
    MultiAssetSettings,
    ObservabilitySettings,
    QuotaSettings,
    ResolvedConfig,
    RuntimeOverrides,
    RuntimeSettings,
    ThalorConfig,
)
from .loader import load_resolved_config, load_thalor_config  # noqa: F401
from .effective_dump import (  # noqa: F401
    write_effective_config_latest,
    write_effective_config_snapshot,
)
