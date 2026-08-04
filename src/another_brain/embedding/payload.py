"""Versioned embedding payload construction."""
from __future__ import annotations

from ..errors import ValidationError
from .manifest import QUERY_PROMPT


def document_payload(topic: str, summary: str) -> str:
    return topic.replace("-", " ") + "\n" + summary.strip()


def query_payload(query: str) -> str:
    stripped = query.strip()
    if not stripped:
        raise ValidationError("query must be non-empty")
    return QUERY_PROMPT + stripped
