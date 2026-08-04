"""Domain record types for the SQLite schema tables (TASK-048 models-first).

One frozen dataclass per persisted table: ``MemoryRecord`` (``memories``),
``AuditEvent`` (``audit_events``), ``ImportRun`` (``import_runs``),
``EmbeddingProfile`` (``embedding_profiles``), plus the service shapes
``RecentFilters`` and ``SearchPreview``. Field names follow the JSONL v1
contract (``.agents/contracts/another-brain-jsonl-v1.md``) so export/import
and storage map 1:1.

Every model validates its locked constraints at construction (typed
:class:`ValidationError`, before any SQLite write). ``timeline_day`` is
``YYYY-MM-DD`` in the configured timezone, computed by the service at write
time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from another_brain.errors import ValidationError
from another_brain.protocols import GLOBAL_SCOPE_ID, Scope

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_AUDIT_KEYS = ("topic", "summary", "content", "metadata")


# --------------------------------------------------------------------------
# memories


@dataclass(frozen=True)
class EmbeddingVector:
    """One validated FLOAT32 ``[640]`` unit-norm vector (input version 2).

    Produced only by the embedding provider after finite/unit-norm/shape
    validation; consumed by the storage layer as the canonical vector blob.
    """

    values: np.ndarray


def _require_non_empty(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string, got {value!r}")


@dataclass(frozen=True)
class MemoryRecord:
    """One ``memories`` row (identity + scope + text + timeline + vector)."""

    memory_id: str
    brain_id: str
    agent_id: str
    scope: Scope
    scope_id: str
    topic: str
    catalog: str
    summary: str
    content: str
    timeline_day: str
    period_start_ms: int | None
    period_end_ms: int | None
    created_at_ms: int
    updated_at_ms: int
    importance: int
    expires_at_ms: int
    deleted_at_ms: int | None
    metadata: dict[str, Any]
    profile_id: str
    record_version: int
    embedding: EmbeddingVector | None

    def __post_init__(self) -> None:
        for name in ("memory_id", "brain_id", "agent_id", "topic", "catalog",
                     "summary", "profile_id"):
            _require_non_empty(name, getattr(self, name))
        object.__setattr__(self, "scope", Scope(self.scope))
        if not isinstance(self.scope_id, str) or not self.scope_id:
            raise ValidationError(
                f"scope_id must be a non-empty string, got {self.scope_id!r}"
            )
        if self.scope is Scope.GLOBAL and self.scope_id != GLOBAL_SCOPE_ID:
            raise ValidationError(
                f"scope=global canonicalizes scope_id to {GLOBAL_SCOPE_ID!r},"
                f" got {self.scope_id!r}"
            )
        if not _DAY_RE.fullmatch(self.timeline_day):
            raise ValidationError(
                f"timeline_day must be YYYY-MM-DD, got {self.timeline_day!r}"
            )
        if not 1 <= self.importance <= 5:
            raise ValidationError(
                f"importance must be in 1..5, got {self.importance}"
            )
        if not isinstance(self.metadata, dict):
            raise ValidationError(
                f"metadata must be a JSON object, got {type(self.metadata).__name__}"
            )
        if self.period_start_ms is not None and self.period_end_ms is not None:
            if self.period_start_ms > self.period_end_ms:
                raise ValidationError(
                    f"period must be ordered: start {self.period_start_ms} >"
                    f" end {self.period_end_ms}"
                )
        if self.updated_at_ms < self.created_at_ms:
            raise ValidationError(
                f"updated_at must be >= created_at: {self.updated_at_ms} <"
                f" {self.created_at_ms}"
            )
        if self.record_version < 1:
            raise ValidationError(
                f"record_version must be positive, got {self.record_version}"
            )
        if self.embedding is not None and not isinstance(self.embedding, EmbeddingVector):
            raise ValidationError(
                f"embedding must be an EmbeddingVector, got"
                f" {type(self.embedding).__name__}"
            )


# --------------------------------------------------------------------------
# audit_events


class AuditAction(str, Enum):
    """Allowed structural mutation actions (audit payload contract)."""

    REMEMBER = "remember"
    REINFORCE = "reinforce"
    FORGET = "forget"
    RESTORE = "restore"
    HARD_DELETE = "hard_delete"


def _assert_no_memory_text(value: Any, path: str = "detail") -> None:
    """Memory-text keys are forbidden anywhere in an audit payload."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _FORBIDDEN_AUDIT_KEYS:
                raise ValidationError(
                    f"audit detail must not carry memory text: {path}.{key}"
                )
            _assert_no_memory_text(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_memory_text(item, f"{path}[{index}]")


@dataclass(frozen=True)
class AuditEvent:
    """One ``audit_events`` row; structural facts only, no memory text."""

    event_id: str
    brain_id: str
    memory_id: str
    agent_id: str
    action: AuditAction
    event_at_ms: int
    timeline_day: str
    detail: dict[str, Any]

    def __post_init__(self) -> None:
        for name in ("event_id", "brain_id", "memory_id", "agent_id"):
            _require_non_empty(name, getattr(self, name))
        object.__setattr__(self, "action", AuditAction(self.action))
        if not isinstance(self.event_at_ms, int):
            raise ValidationError(
                f"event_at_ms must be an integer epoch ms, got {self.event_at_ms!r}"
            )
        if not _DAY_RE.fullmatch(self.timeline_day):
            raise ValidationError(
                f"timeline_day must be YYYY-MM-DD, got {self.timeline_day!r}"
            )
        if not isinstance(self.detail, dict):
            raise ValidationError(
                f"detail must be a JSON object, got {type(self.detail).__name__}"
            )
        _assert_no_memory_text(self.detail)


# --------------------------------------------------------------------------
# import_runs


class ImportStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ImportRun:
    """One ``import_runs`` row — durable JSONL resume checkpoint."""

    export_id: str
    artifact_sha256: str
    format_version: int
    status: ImportStatus
    last_committed_seq: int
    imported_count: int
    skipped_count: int
    failed_count: int
    started_at_ms: int
    completed_at_ms: int | None

    def __post_init__(self) -> None:
        _require_non_empty("export_id", self.export_id)
        if not _SHA256_RE.fullmatch(self.artifact_sha256):
            raise ValidationError(
                f"artifact_sha256 must be 64 hex chars, got {self.artifact_sha256!r}"
            )
        if self.format_version < 1:
            raise ValidationError(
                f"format_version must be >= 1, got {self.format_version}"
            )
        object.__setattr__(self, "status", ImportStatus(self.status))
        for name in ("last_committed_seq", "imported_count", "skipped_count", "failed_count"):
            if getattr(self, name) < 0:
                raise ValidationError(f"{name} must be non-negative")
        if self.status is ImportStatus.RUNNING and self.completed_at_ms is not None:
            raise ValidationError("a running import must not have completed_at_ms")


# --------------------------------------------------------------------------
# embedding_profiles


@dataclass(frozen=True)
class EmbeddingProfile:
    """One ``embedding_profiles`` row — the locked embedding contract."""

    profile_id: str
    model_repo: str
    model_revision: str
    variant: str
    dimension: int
    dtype: str
    normalized: bool
    tokenizer_sha256: str
    config_sha256: str
    prompt_utf8_sha256: str
    query_prompt: str
    input_version: int
    created_at_ms: int

    @classmethod
    def from_manifest(
        cls, manifest: "ModelManifest", *, created_at_ms: int, profile_id: str | None = None
    ) -> "EmbeddingProfile":
        """Build the profile row from the immutable model manifest."""
        files = dict(manifest.files)
        return cls(
            profile_id=profile_id or manifest.profile,
            model_repo=manifest.repo,
            model_revision=manifest.revision,
            variant=manifest.profile,
            dimension=manifest.dimensions,
            dtype=manifest.dtype,
            normalized=manifest.normalization == "unit_l2",
            tokenizer_sha256=files["tokenizer.json"],
            config_sha256=files["config.json"],
            prompt_utf8_sha256=manifest.query_prompt_utf8_sha256,
            query_prompt=manifest.query_prompt,
            input_version=manifest.input_version,
            created_at_ms=created_at_ms,
        )

    def __post_init__(self) -> None:
        _require_non_empty("profile_id", self.profile_id)
        _require_non_empty("model_repo", self.model_repo)
        _require_non_empty("model_revision", self.model_revision)
        if self.dimension < 1:
            raise ValidationError(f"dimension must be positive, got {self.dimension}")
        if self.input_version < 1:
            raise ValidationError(
                f"input_version must be >= 1, got {self.input_version}"
            )
        for name in ("tokenizer_sha256", "config_sha256", "prompt_utf8_sha256"):
            if not _SHA256_RE.fullmatch(getattr(self, name)):
                raise ValidationError(f"{name} must be 64 hex chars")


# --------------------------------------------------------------------------
# service shapes


@dataclass(frozen=True)
class RecentFilters:
    """Optional narrowing for ``recent``/``search`` (live rows only)."""

    topic: str | None = None
    catalog: str | None = None
    since_ms: int | None = None
    until_ms: int | None = None

    def __post_init__(self) -> None:
        if self.topic is not None and not self.topic.strip():
            raise ValidationError("topic filter must not be empty")
        if self.catalog is not None and not self.catalog.strip():
            raise ValidationError("catalog filter must not be empty")
        if self.since_ms is not None and self.until_ms is not None:
            if self.since_ms > self.until_ms:
                raise ValidationError(
                    f"since_ms {self.since_ms} > until_ms {self.until_ms}"
                )


@dataclass(frozen=True)
class SearchPreview:
    """One fused search result; never carries ``content`` or the embedding."""

    memory_id: str
    topic: str
    summary: str
    scope: Scope
    scope_id: str
    created_at_ms: int
    importance: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        for name in ("memory_id", "topic", "summary"):
            _require_non_empty(name, getattr(self, name))
        object.__setattr__(self, "scope", Scope(self.scope))
