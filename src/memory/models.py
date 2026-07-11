"""Domain models: MemoryRecord, MemoryIdentity, MemoryScope, MemoryCatalog,
EmbeddingVector, SearchFilters, MemorySearchResult.

Contract: .agents/plans/04-memory-record-and-redis-index-contract.md.
"""
from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from errors import ValidationError

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TIMELINE_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

IMPORTANCE_MIN = 1
IMPORTANCE_MAX = 5
DEFAULT_IMPORTANCE = 3

GLOBAL_SCOPE_ID = "global"


def validate_slug(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not SLUG_RE.match(value):
        raise ValidationError(
            f"{field_name} must be a lowercase-kebab slug"
            f" (letters/digits separated by single dashes), got {value!r}"
        )
    return value


def validate_importance(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"importance must be an int 1-5, got {value!r}")
    if not IMPORTANCE_MIN <= value <= IMPORTANCE_MAX:
        raise ValidationError(f"importance must be between 1 and 5, got {value}")
    return value


def validate_timeline_day(value: Any) -> str:
    if not isinstance(value, str) or not TIMELINE_DAY_RE.match(value):
        raise ValidationError(f"timeline_day must be YYYY-MM-DD, got {value!r}")
    return value


def timeline_day_from_ts(ts: float, tz_name: str) -> str:
    """Derive the diary day from a timestamp in the configured timezone."""
    return datetime.fromtimestamp(float(ts), ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def _key_segment(value: Any, field_name: str) -> str:
    """Values embedded in Redis keys must be non-empty and ':'-free, or the
    key format ab:memory:{brain_id}:{memory_id} stops being parseable."""
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} must be a non-empty string, got {value!r}")
    if ":" in value:
        raise ValidationError(f"{field_name} must not contain ':', got {value!r}")
    return value


class MemoryScope(str, Enum):
    USER = "user"
    PROJECT = "project"
    GLOBAL = "global"

    @classmethod
    def parse(cls, value: Any) -> "MemoryScope":
        try:
            return cls(value)
        except ValueError:
            allowed = ", ".join(s.value for s in cls)
            raise ValidationError(f"scope must be one of: {allowed}; got {value!r}") from None


class MemoryCatalog:
    """Open vocabulary: any lowercase-kebab slug is a valid catalog value.

    The starter set below is documentation, not a closed enum — new catalog
    values need no schema or code change.
    """

    BUG = "bug"
    DECISION = "decision"
    PREFERENCE = "preference"
    TASK = "task"
    FACT = "fact"
    NOTE = "note"

    DEFAULT = NOTE
    STARTER = frozenset({BUG, DECISION, PREFERENCE, TASK, FACT, NOTE})

    @staticmethod
    def validate(value: Any) -> str:
        return validate_slug(value, "catalog")


class ScoreSource(str, Enum):
    KNN = "knn"
    BM25 = "bm25"
    FUSED = "fused"


@dataclass(frozen=True)
class MemoryIdentity:
    memory_id: str
    brain_id: str
    agent_id: str
    scope: MemoryScope
    scope_id: str

    def __post_init__(self) -> None:
        _key_segment(self.memory_id, "memory_id")
        _key_segment(self.brain_id, "brain_id")
        if not isinstance(self.agent_id, str) or not self.agent_id:
            raise ValidationError(f"agent_id must be a non-empty string, got {self.agent_id!r}")
        object.__setattr__(self, "scope", MemoryScope.parse(self.scope))
        if not isinstance(self.scope_id, str) or not self.scope_id:
            raise ValidationError(f"scope_id must be a non-empty string, got {self.scope_id!r}")
        if self.scope is MemoryScope.GLOBAL and self.scope_id != GLOBAL_SCOPE_ID:
            raise ValidationError(
                f"scope=global pins scope_id={GLOBAL_SCOPE_ID!r}, got {self.scope_id!r}"
            )


@dataclass(frozen=True)
class MemoryRecord:
    """One diary entry: timeline_day + topic + summary, classified by catalog.

    `summary` is the canonical text — the embedding is computed from it.
    `content` is optional detail/checklist, BM25-searchable, never embedded.
    """

    identity: MemoryIdentity
    topic: str
    summary: str
    timeline_day: str
    period_start: float
    period_end: float
    created_at: float
    updated_at: float
    catalog: str = MemoryCatalog.DEFAULT
    content: str = ""
    importance: int = DEFAULT_IMPORTANCE
    metadata: dict[str, Any] = field(default_factory=dict)
    deleted_at: float | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        validate_slug(self.topic, "topic")
        MemoryCatalog.validate(self.catalog)
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValidationError("summary must be a non-empty string")
        if not isinstance(self.content, str):
            raise ValidationError(f"content must be a string, got {type(self.content).__name__}")
        validate_timeline_day(self.timeline_day)
        validate_importance(self.importance)
        for name in ("period_start", "period_end", "created_at", "updated_at"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValidationError(f"{name} must be a positive unix timestamp, got {value!r}")
            object.__setattr__(self, name, float(value))
        if self.period_end < self.period_start:
            raise ValidationError(
                f"period_end ({self.period_end}) must be >= period_start ({self.period_start})"
            )
        if self.deleted_at is not None:
            object.__setattr__(self, "deleted_at", float(self.deleted_at))
        if not isinstance(self.metadata, dict):
            raise ValidationError("metadata must be a dict")
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) \
                or self.schema_version < 1:
            raise ValidationError(f"schema_version must be an int >= 1, got {self.schema_version!r}")

    @property
    def has_content(self) -> bool:
        return bool(self.content)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

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
        tz_name: str,
        catalog: str = MemoryCatalog.DEFAULT,
        content: str = "",
        importance: int = DEFAULT_IMPORTANCE,
        period_start: float | None = None,
        period_end: float | None = None,
        metadata: Mapping[str, Any] | None = None,
        memory_id: str | None = None,
        now_ts: float | None = None,
    ) -> "MemoryRecord":
        now = float(now_ts) if now_ts is not None else time.time()
        ps = float(period_start) if period_start is not None else now
        pe = float(period_end) if period_end is not None else ps
        identity = MemoryIdentity(
            memory_id=memory_id or str(uuid.uuid4()),
            brain_id=brain_id,
            agent_id=agent_id,
            scope=MemoryScope.parse(scope),
            scope_id=scope_id,
        )
        return cls(
            identity=identity,
            topic=topic,
            summary=summary,
            timeline_day=timeline_day_from_ts(ps, tz_name),
            period_start=ps,
            period_end=pe,
            created_at=now,
            updated_at=now,
            catalog=catalog,
            content=content,
            importance=importance,
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class EmbeddingVector:
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.values:
            raise ValidationError("embedding must not be empty")
        if not all(isinstance(v, float) and math.isfinite(v) for v in self.values):
            raise ValidationError("embedding values must all be finite floats")

    @property
    def dim(self) -> int:
        return len(self.values)

    @classmethod
    def from_list(cls, values: Any, expected_dim: int) -> "EmbeddingVector":
        try:
            converted = tuple(float(v) for v in values)
        except (TypeError, ValueError):
            raise ValidationError("embedding must be a sequence of numbers") from None
        vector = cls(converted)
        if vector.dim != expected_dim:
            raise ValidationError(
                f"embedding dim mismatch — got {vector.dim}, expected {expected_dim}"
            )
        return vector


@dataclass(frozen=True)
class SearchFilters:
    scope: MemoryScope
    scope_id: str
    topic: str | None = None
    catalog: str | None = None
    timeline_day: str | None = None
    min_importance: int | None = None
    since_ts: float | None = None
    until_ts: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", MemoryScope.parse(self.scope))
        if not isinstance(self.scope_id, str) or not self.scope_id:
            raise ValidationError(f"scope_id must be a non-empty string, got {self.scope_id!r}")
        if self.scope is MemoryScope.GLOBAL and self.scope_id != GLOBAL_SCOPE_ID:
            raise ValidationError(
                f"scope=global pins scope_id={GLOBAL_SCOPE_ID!r}, got {self.scope_id!r}"
            )
        if self.topic is not None:
            validate_slug(self.topic, "topic")
        if self.catalog is not None:
            MemoryCatalog.validate(self.catalog)
        if self.timeline_day is not None:
            validate_timeline_day(self.timeline_day)
        if self.min_importance is not None:
            validate_importance(self.min_importance)
        if self.since_ts is not None and self.until_ts is not None \
                and self.since_ts > self.until_ts:
            raise ValidationError(
                f"since_ts ({self.since_ts}) must be <= until_ts ({self.until_ts})"
            )


@dataclass(frozen=True)
class MemorySearchResult:
    """Search preview payload (Step 04 section 6.5): summary inline, detail
    on demand via brain_get. Never carries content or embedding."""

    memory_id: str
    topic: str
    catalog: str
    summary: str
    timeline_day: str
    importance: int
    has_content: bool
    relevance_score: float
    score_source: ScoreSource
    widened: bool = False
