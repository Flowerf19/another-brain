"""Final backend-neutral service protocols (TASK-034).

These protocols are the locked contract between ``MemoryService`` and its
dependencies. They exist for service isolation and unit tests — there is no
backend selector and no storage-vendor type or score encoding here.

Locked semantics (master plan 07, "Runtime and identity flows"):

- ``brain_id`` is process-bound configuration; ``agent_id`` comes from the MCP
  handshake. Neither is ever a tool argument, so neither appears in these
  signatures. Repository/audit implementations are constructed already bound
  to one ``brain_id``.
- Collection operations (``store``/``recent``/``search``) normalize one scope
  tuple ``(brain_id, scope, scope_id)``; since the brain is bound, callers
  pass a normalized :class:`ScopeKey`. ``scope_id`` is non-empty for ``user``
  and ``project``; ``global`` canonicalizes to the literal ``"global"`` and
  rejects conflicting values.
- By-ID operations (``get``/``reinforce``/``soft_delete``/``restore``/
  ``hard_delete``) key on ``(bound brain_id, memory_id)``. Scope is read from
  the stored row and never trusted from the caller. An ID that exists only in
  a different brain is indistinguishable from an unknown ID.
- Live reads exclude expired (``expires_at <= now``) and soft-deleted rows
  before any limit. ``restore`` may address a soft-deleted row still inside
  its grace window; ``hard_delete`` may address a live or soft-deleted row.
- All timestamps are signed integer Unix epoch milliseconds; ``timeline_day``
  is ``YYYY-MM-DD`` in the configured timezone.

Domain record types (``MemoryRecord``, ``RecentFilters``, ``SearchPreview``,
``AuditEvent``, ``EmbeddingVector``) land with the storage/embedding phases
(GOAL-011/005); they are imported under ``TYPE_CHECKING`` so this contract
module stays dependency-free.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:
    from another_brain.domain.models import (
        AuditEvent,
        EmbeddingVector,
        MemoryRecord,
        RecentFilters,
        SearchPreview,
    )

GLOBAL_SCOPE_ID = "global"


class Scope(str, Enum):
    """Memory visibility scope. ``scope_id`` rules live in :class:`ScopeKey`."""

    USER = "user"
    PROJECT = "project"
    GLOBAL = "global"


@dataclass(frozen=True)
class ScopeKey:
    """Normalized collection scope for one bound brain.

    ``scope_id`` must be non-empty for ``user``/``project`` and is pinned to
    the literal ``"global"`` for ``Scope.GLOBAL``; any other combination is a
    validation error raised by the service layer before persistence.
    """

    scope: Scope
    scope_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", Scope(self.scope))
        if not isinstance(self.scope_id, str) or not self.scope_id:
            raise ValueError(f"scope_id must be a non-empty string, got {self.scope_id!r}")
        if self.scope is Scope.GLOBAL and self.scope_id != GLOBAL_SCOPE_ID:
            raise ValueError(
                f"scope=global canonicalizes scope_id to {GLOBAL_SCOPE_ID!r},"
                f" got {self.scope_id!r}"
            )


class MutationOutcome(Enum):
    """Result of a by-ID lifecycle mutation.

    ``NOT_FOUND`` covers every invisible-ID case — unknown ID, ID owned by
    another brain, and (for live-only mutations) expired or already deleted
    rows. The service maps it to the single shared ``not_found`` response
    shape so cross-brain existence never leaks.
    """

    APPLIED = "applied"
    NOT_FOUND = "not_found"


class EmbeddingHealth(Enum):
    """Provider load state. Reading it never triggers a model load."""

    NOT_LOADED = "not_loaded"
    READY = "ready"
    ERROR = "error"


@runtime_checkable
class MemoryRepository(Protocol):
    """Append-only memory persistence bound to one ``brain_id``.

    Writes happen in short ``BEGIN IMMEDIATE`` transactions with bounded busy
    retry; ``row + FTS`` commits atomically. Validation and integrity failures
    are raised, never retried.
    """

    def store(self, record: MemoryRecord) -> None:
        """Append one new record with its embedding and FTS row atomically.

        ``record.identity.brain_id`` must equal the bound brain. A duplicate
        ``(brain_id, memory_id)`` is an integrity error, not an overwrite —
        diary entries are append-only; "update" means remember-new +
        forget-old at the service layer.
        """
        ...

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Return the live row for ``(bound brain_id, memory_id)``.

        Returns ``None`` for unknown, cross-brain, expired, or soft-deleted
        IDs — callers cannot distinguish these cases.
        """
        ...

    def recent(
        self,
        scope: ScopeKey,
        *,
        limit: int,
        filters: RecentFilters | None = None,
    ) -> Sequence[MemoryRecord]:
        """Live records in one collection scope, newest first.

        Deterministic order: ``created_at DESC, memory_id ASC``. Expired and
        soft-deleted rows are excluded before ``limit`` is applied.
        """
        ...

    def reinforce(self, memory_id: str) -> MutationOutcome:
        """Re-arm ``expires_at`` from importance for a live row (transactional).

        ``NOT_FOUND`` when the row is not live and visible to the bound brain.
        """
        ...

    def soft_delete(self, memory_id: str) -> MutationOutcome:
        """Forget: set ``deleted_at`` and clamp
        ``expires_at = min(current_expires_at, now + 30 days)``.

        Never extends the row's life. ``NOT_FOUND`` for non-live/invisible IDs.
        """
        ...

    def restore(self, memory_id: str) -> MutationOutcome:
        """Undo a soft delete still inside its grace window; re-arms
        ``expires_at`` from importance transactionally.

        ``NOT_FOUND`` for unknown/cross-brain IDs, live rows, and rows whose
        grace window has passed.
        """
        ...

    def hard_delete(self, memory_id: str) -> MutationOutcome:
        """Admin: permanently remove a live or soft-deleted row.

        Audit history is preserved (``audit_events`` has no memory FK).
        """
        ...


@runtime_checkable
class MemoryRetriever(Protocol):
    """Hybrid retrieval over one bound brain.

    Runs the lexical (FTS5 BM25, weights 5:3:1) and vector (exact cosine,
    micro-cosine floor 300000) branches independently with fixed
    ``candidate_limit=50`` per branch, fuses with equal-weight RRF ``k=60``,
    and returns a deterministic top-k of previews. Lexical-only candidates
    remain valid — there is no universal post-fusion cosine gate (the locked
    fix for the legacy content-match bug). Expired/deleted rows are filtered
    before each branch limit.
    """

    def search(
        self,
        *,
        query_text: str,
        query_vector: EmbeddingVector,
        scope: ScopeKey,
        filters: RecentFilters | None = None,
    ) -> Sequence[SearchPreview]:
        """Return fused previews for a bounded query in one collection scope.

        ``query_text`` drives the lexical branch (skipped when it yields no
        safe FTS terms); ``query_vector`` is the prompted-query embedding,
        computed once by the service. Previews never carry ``content`` or the
        embedding; detail is fetched separately by ID.
        """
        ...


@runtime_checkable
class AuditRepository(Protocol):
    """Secret-free structural mutation audit, bound to one ``brain_id``.

    Events record mutation structure only — never topic, summary, content, or
    metadata. Retention is 90 days by ``event_at``; cleanup is bounded, best
    effort, and cannot roll back an already committed memory mutation.
    """

    def record(self, event: AuditEvent) -> None:
        """Persist one structural mutation fact.

        Events carrying memory text are rejected (validation error), never
        stored. ``event.brain_id`` must equal the bound brain.
        """
        ...

    def list_day(self, day: str) -> Sequence[AuditEvent]:
        """Events for ``(bound brain_id, day)`` where ``day`` is ``YYYY-MM-DD``.

        Deterministic order: ``event_at DESC, event_id ASC``.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Harrier q4 ONNX embedding, process-local and lazily loaded.

    Payloads are locked input version 2: documents are exactly
    ``topic.replace("-", " ") + "\\n" + summary.strip()`` with no prompt;
    queries are exactly ``QUERY_PROMPT + query.strip()``. Token budgets are
    enforced by the service's budget validator before these calls, never by
    truncation here. Outputs are validated FLOAT32 ``[640]``, finite and unit
    norm.
    """

    def embed_document(self, *, topic: str, summary: str) -> EmbeddingVector:
        """Embed one document payload. Raises on over-budget input."""
        ...

    def embed_query(self, query: str) -> EmbeddingVector:
        """Embed one prompted query. Empty stripped queries are rejected."""
        ...

    def health(self) -> EmbeddingHealth:
        """Current load state. Never loads the model to answer."""
        ...
