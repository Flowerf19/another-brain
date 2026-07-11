"""ModelRegistry — resolves configured model names to download metadata
(Step 03). Model names double as Hugging Face repo ids in MVP.

Known models carry verified profile facts (embedding dim, query prompt) so a
misconfigured EMBEDDING_DIM is caught before it poisons the Redis index.
"""
from __future__ import annotations

from dataclasses import dataclass

from errors import ConfigError

KIND_EMBEDDING = "embedding"
KIND_MEMORY = "memory"


@dataclass(frozen=True)
class ModelSpec:
    """Resolved download/runtime metadata for one configured model."""

    name: str                        # configured name == HF repo id
    kind: str                        # embedding | memory
    expected_dim: int | None         # embedding models only
    query_prompt_name: str | None    # ST prompt name for queries, if the model has one


# Verified profiles (Step 03, "Harrier Default Profile"). Harrier's prompt
# table defines web_search_query / sts_query / bitext_query; retrieval of
# memory passages for a search query maps to web_search_query.
_KNOWN: dict[str, dict] = {
    "microsoft/harrier-oss-v1-270m": {
        "expected_dim": 640,
        "query_prompt_name": "web_search_query",
    },
}


class ModelRegistry:
    def resolve(
        self,
        name: str,
        kind: str,
        *,
        configured_dim: int | None = None,
    ) -> ModelSpec:
        """Resolve a configured model name; for known embedding models a
        conflicting EMBEDDING_DIM is a startup error, not a silent override."""
        name = name.strip()
        if not name:
            raise ConfigError(f"no {kind} model configured")
        known = _KNOWN.get(name, {})
        expected_dim = known.get("expected_dim")
        if kind == KIND_EMBEDDING:
            if expected_dim is not None and configured_dim not in (None, expected_dim):
                raise ConfigError(
                    f"EMBEDDING_DIM={configured_dim} conflicts with {name} "
                    f"which produces {expected_dim}-dim vectors"
                )
            expected_dim = expected_dim or configured_dim
        else:
            expected_dim = None
        return ModelSpec(
            name=name,
            kind=kind,
            expected_dim=expected_dim,
            query_prompt_name=known.get("query_prompt_name"),
        )
