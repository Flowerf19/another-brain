"""ModelStatus — secret-free status for health and CLI output (Step 03).

Never include API keys, signed URLs, or tokens here; this object is shown
verbatim by `another-brain model status` and by brain_health.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelStatus:
    kind: str                    # only "embedding" exists (server-side memory model cut)
    provider: str                # openai_compat | ollama | gemini | local
    model_name: str
    revision: str                # pinned revision, "" if unpinned
    download_policy: str
    network_allowed: bool
    cached: bool                 # local providers only; True for external
    cache_path: str | None       # None for external providers
    expected_dim: int | None     # embedding models only
    weight_precision: str | None # local providers only
    device: str | None           # local providers only

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
