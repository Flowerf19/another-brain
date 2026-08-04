"""Backend-neutral diary models for the native runtime."""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..errors import ValidationError
from .retention import TTL_SECONDS, expires_at_ms

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GLOBAL_SCOPE_ID = "global"


class MemoryScope(str, Enum):
    USER = "user"
    PROJECT = "project"
    GLOBAL = "global"

    @classmethod
    def parse(cls, value: Any) -> "MemoryScope":
        try:
            return cls(value)
        except (ValueError, TypeError):
            raise ValidationError(
                f"scope must be user, project, or global; got {value!r}"
            ) from None


def validate_slug(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        raise ValidationError(f"{field} must be a lowercase-kebab slug")
    return value


def validate_importance(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in TTL_SECONDS:
        raise ValidationError(f"importance must be an integer from 1 to 5, got {value!r}")
    return value


def normalize_scope(scope: MemoryScope | str, scope_id: str) -> tuple[MemoryScope, str]:
    parsed = MemoryScope.parse(scope)
    if parsed is MemoryScope.GLOBAL:
        if scope_id not in ("", GLOBAL_SCOPE_ID):
            raise ValidationError("scope=global requires scope_id='global' or omission")
        return parsed, GLOBAL_SCOPE_ID
    if not isinstance(scope_id, str) or not scope_id.strip():
        raise ValidationError(f"scope_id is required for scope={parsed.value}")
    if ":" in scope_id:
        raise ValidationError("scope_id must not contain ':'")
    return parsed, scope_id.strip()


def timeline_day(timestamp_ms: int, timezone: str) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1_000, ZoneInfo(timezone)).strftime(
        "%Y-%m-%d"
    )


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    brain_id: str
    agent_id: str
    scope: MemoryScope
    scope_id: str
    topic: str
    catalog: str
    summary: str
    content: str
    timeline_day: str
    period_start_ms: int
    period_end_ms: int
    created_at_ms: int
    updated_at_ms: int
    importance: int
    expires_at_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)
    deleted_at_ms: int | None = None
    record_version: int = 1

    def __post_init__(self) -> None:
        for name in ("memory_id", "brain_id", "agent_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or ":" in value:
                raise ValidationError(f"{name} must be non-empty and ':'-free")
        scope, scope_id = normalize_scope(self.scope, self.scope_id)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "scope_id", scope_id)
        validate_slug(self.topic, "topic")
        validate_slug(self.catalog, "catalog")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValidationError("summary must be non-empty")
        if not isinstance(self.content, str):
            raise ValidationError("content must be a string")
        if not DAY_RE.fullmatch(self.timeline_day):
            raise ValidationError("timeline_day must be YYYY-MM-DD")
        validate_importance(self.importance)
        if self.period_end_ms < self.period_start_ms:
            raise ValidationError("period_end_ms must be >= period_start_ms")
        if self.updated_at_ms < self.created_at_ms:
            raise ValidationError("updated_at_ms must be >= created_at_ms")
        if self.expires_at_ms <= 0:
            raise ValidationError("expires_at_ms must be positive")
        try:
            encoded = json.dumps(self.metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValidationError("metadata must be a strict JSON object") from None
        if not isinstance(self.metadata, dict) or not encoded:
            raise ValidationError("metadata must be a strict JSON object")

    @property
    def has_content(self) -> bool:
        return bool(self.content)

    @classmethod
    def new(
        cls,
        *,
        brain_id: str,
        agent_id: str,
        scope: MemoryScope | str,
        scope_id: str,
        topic: str,
        summary: str,
        timezone: str,
        catalog: str = "note",
        content: str = "",
        importance: int = 3,
        metadata: Mapping[str, Any] | None = None,
        period_start_ms: int | None = None,
        period_end_ms: int | None = None,
        now_ms: int | None = None,
    ) -> "MemoryRecord":
        now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
        start = now if period_start_ms is None else int(period_start_ms)
        end = start if period_end_ms is None else int(period_end_ms)
        parsed_scope, parsed_scope_id = normalize_scope(scope, scope_id)
        return cls(
            memory_id=str(uuid.uuid4()),
            brain_id=brain_id,
            agent_id=agent_id,
            scope=parsed_scope,
            scope_id=parsed_scope_id,
            topic=topic,
            catalog=catalog,
            summary=summary.strip(),
            content=content,
            timeline_day=timeline_day(start, timezone),
            period_start_ms=start,
            period_end_ms=end,
            created_at_ms=now,
            updated_at_ms=now,
            importance=validate_importance(importance),
            expires_at_ms=expires_at_ms(importance, now),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class SearchFilters:
    scope: MemoryScope
    scope_id: str
    topic: str | None = None
    catalog: str | None = None
    timeline_day: str | None = None
    min_importance: int | None = None
    since_ms: int | None = None

    @classmethod
    def create(cls, scope: str, scope_id: str, **kwargs: Any) -> "SearchFilters":
        parsed, normalized = normalize_scope(scope, scope_id)
        return cls(parsed, normalized, **kwargs)


@dataclass(frozen=True)
class SearchResult:
    memory_id: str
    topic: str
    catalog: str
    summary: str
    timeline_day: str
    importance: int
    has_content: bool
    relevance_score: float
    score_source: str


def validate_vector(values: Any, expected_dim: int = 640) -> tuple[float, ...]:
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        raise ValidationError("embedding must be numeric") from None
    if len(vector) != expected_dim or not all(math.isfinite(value) for value in vector):
        raise ValidationError(f"embedding must contain {expected_dim} finite floats")
    return vector
