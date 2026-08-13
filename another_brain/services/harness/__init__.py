"""Harness connector (TASK-093): the public surface of the harness registry
+ service. ``another-brain connect`` and its tests import from here.

Data rows live in ``harnesses.yaml`` (see :mod:`.registry`); behavior lives in
:mod:`.service`. Everything public is re-exported flat so callers never need
the inner module names.
"""
from another_brain.services.harness.registry import (
    MANUAL_JSON,
    MANUAL_TOML,
    SERVER_ENTRY,
    SERVER_NAME,
    Harness,
    HarnessResult,
    _BY_NAME,
    _HARNESSES,
    _harness_from_row,
    _registry_yaml_text,
)
from another_brain.services.harness.service import (
    SERVER_ENTRY_JSON,
    SKILL_NAME,
    SKILL_RESOURCE_DIR,
    Runner,
    UnknownHarnessError,
    connect,
    detect_harnesses,
    install_skill,
    known_harnesses,
    upsert_server,
)

__all__ = [
    "MANUAL_JSON",
    "MANUAL_TOML",
    "SERVER_ENTRY",
    "SERVER_ENTRY_JSON",
    "SERVER_NAME",
    "SKILL_NAME",
    "SKILL_RESOURCE_DIR",
    "Harness",
    "HarnessResult",
    "Runner",
    "UnknownHarnessError",
    "_BY_NAME",
    "_HARNESSES",
    "_harness_from_row",
    "_registry_yaml_text",
    "connect",
    "detect_harnesses",
    "install_skill",
    "known_harnesses",
    "upsert_server",
]
