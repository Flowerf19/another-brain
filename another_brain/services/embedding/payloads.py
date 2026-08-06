"""Versioned payload construction + profile validation (TASK-027).

The locked input version 2 payloads — the byte-exact templates that make q4
encoding and storage deterministic:

- documents: ``topic.replace("-", " ") + "\\n" + summary.strip()``, no prompt;
- queries: ``QUERY_PROMPT + query.strip()``, empty stripped queries rejected.

Every producer (embedding provider, budget validator, storage) builds
payloads here, never inline, so the template cannot drift between consumers.

``validate_profile`` is the schema/profile gate: a memory row may only be
written under the locked profile + input version. A foreign profile or
version blocks mixed search until re-embedding completes — the schema
persists ``(profile, input_version)`` per memory and consults this gate.
"""
from __future__ import annotations

from another_brain.errors import ValidationError
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST, QUERY_PROMPT


def document_payload(topic: str, summary: str) -> str:
    """Locked input version 2 document payload (no prompt)."""
    return topic.replace("-", " ") + "\n" + summary.strip()


def query_payload(query: str) -> str:
    """Locked input version 2 prompted query; empty stripped queries rejected."""
    stripped = query.strip()
    if not stripped:
        raise ValidationError("query must not be empty")
    return QUERY_PROMPT + stripped


def validate_profile(*, profile: str, input_version: int) -> None:
    """Schema gate: only the locked profile/input version may be stored.

    A foreign profile or version raises :class:`ValidationError` with the
    locked values, so mixed-profile search cannot silently start before the
    re-embedding migration completes.
    """
    if profile != MODEL_MANIFEST.profile:
        raise ValidationError(
            f"memory profile {profile!r} does not match the locked profile "
            f"{MODEL_MANIFEST.profile!r}; re-embed before mixing profiles"
        )
    if input_version != MODEL_MANIFEST.input_version:
        raise ValidationError(
            f"memory input version {input_version} does not match the locked "
            f"version {MODEL_MANIFEST.input_version}; re-embed before mixing"
        )
