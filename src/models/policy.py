"""ModelInstallPolicy — disabled | manual | lazy | on_start (Step 03).

The policy decides *when* a missing local model may be downloaded. It never
overrides the network gate: MODEL_ALLOW_NETWORK=false blocks every download
regardless of policy.
"""
from __future__ import annotations

from enum import Enum

from errors import ConfigError

# Download triggers, in the order they can happen in a process lifetime.
TRIGGER_EXPLICIT = "explicit"    # `another-brain model pull`
TRIGGER_STARTUP = "startup"      # server startup check
TRIGGER_FIRST_USE = "first_use"  # first embedding call

_TRIGGERS = frozenset({TRIGGER_EXPLICIT, TRIGGER_STARTUP, TRIGGER_FIRST_USE})


class ModelInstallPolicy(str, Enum):
    """Step 03 policy values. Default is MANUAL: nothing downloads unless the
    operator runs an explicit pull command."""

    DISABLED = "disabled"
    MANUAL = "manual"
    LAZY = "lazy"
    ON_START = "on_start"

    @classmethod
    def parse(cls, raw: str) -> "ModelInstallPolicy":
        try:
            return cls(raw.strip().lower())
        except ValueError:
            valid = ", ".join(p.value for p in cls)
            raise ConfigError(
                f"MODEL_DOWNLOAD_POLICY must be one of: {valid}; got {raw!r}"
            ) from None

    def may_download(self, trigger: str) -> bool:
        if trigger not in _TRIGGERS:
            raise ValueError(f"unknown download trigger {trigger!r}")
        if self is ModelInstallPolicy.DISABLED:
            return False
        if trigger == TRIGGER_EXPLICIT:
            return True
        if self is ModelInstallPolicy.LAZY:
            return trigger == TRIGGER_FIRST_USE
        if self is ModelInstallPolicy.ON_START:
            return trigger == TRIGGER_STARTUP
        return False  # MANUAL: explicit only
