"""``MemoryService`` — the use-case layer over the storage Protocols (TASK-064).

The single place tool handlers call: remember, search, recent, get, reinforce,
forget, restore, hard_delete, audit_events, health. Everything below it is a
Protocol (:mod:`another_brain.protocols`), so this module imports no storage
internals — no connection factory, no SQL, no FTS or vector adapter.

Identity is bound, never chosen by a caller. ``brain_id`` comes from config
and is fixed for the process; ``agent_id`` is provenance the MCP layer detects
from ``clientInfo`` and passes in per call. By-ID operations read scope from
the stored row rather than trusting a caller-supplied scope, so a memory in
another brain is indistinguishable from one that never existed.

Reads are pure. Only ``reinforce`` and ``restore`` re-arm a TTL; ``forget``
clamps expiry to the grace window and never extends life.

Each payload is built exactly once per call: ``remember`` embeds the
``topic + summary`` document (locked input version 2, so this is the write
path's only embed), and ``search`` embeds the bounded prompted query once
before handing it to the retriever, which runs both branches from that one
vector.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from another_brain.config import AppConfig
from another_brain.domain.models import (
    AuditAction,
    AuditEvent,
    MemoryRecord,
    RecentFilters,
    SearchPreview,
)
from another_brain.domain.retention import expires_at_ms_for
from another_brain.domain.timeline import timeline_day_for
from another_brain.errors import ValidationError
from another_brain.protocols import (
    GLOBAL_SCOPE_ID,
    AuditRepository,
    EmbeddingHealth,
    EmbeddingProvider,
    MemoryRepository,
    MemoryRetriever,
    MutationOutcome,
    Scope,
    ScopeKey,
    StorageHealthProbe,
)
from another_brain.services.embedding.budgets import TokenBudgetValidator
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST

RECENT_LIMIT_MAX = 100
AUDIT_LIMIT_MAX = 500

_DAY_SECONDS = 86_400


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class RememberResult:
    """What the caller learns about a newly appended entry."""

    memory_id: str
    timeline_day: str
    expires_at_ms: int


class MemoryService:
    """Use cases over bound identity; storage arrives as Protocols."""

    def __init__(
        self,
        *,
        repository: MemoryRepository,
        retriever: MemoryRetriever,
        audit: AuditRepository,
        embedder: EmbeddingProvider,
        budgets: TokenBudgetValidator,
        storage: StorageHealthProbe,
        config: AppConfig,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        self._repo = repository
        self._retriever = retriever
        self._audit = audit
        self._embedder = embedder
        self._budgets = budgets
        self._storage = storage
        self._config = config
        self._clock = clock

    @property
    def brain_id(self) -> str:
        return self._config.brain_id

    @property
    def timezone(self) -> str:
        return self._config.timeline_timezone

    def today(self) -> str:
        """The current diary day in the configured timezone."""
        return self._day(self._clock())

    # -- write ---------------------------------------------------------------

    def remember(
        self,
        *,
        topic: str,
        summary: str,
        agent_id: str,
        scope: str,
        scope_id: str = "",
        catalog: str = "general",
        content: str = "",
        importance: int = 3,
        metadata: Mapping[str, Any] | None = None,
        period_start_ms: int | None = None,
        period_end_ms: int | None = None,
    ) -> RememberResult:
        """Append one diary entry: validate, embed once, store, audit.

        Append-only — there is no merge and no update. Correcting a memory
        means remembering the new one and forgetting the old.
        """
        if not isinstance(content, str):
            raise ValidationError(f"content must be a string, got {type(content).__name__}")
        metadata = _validated_metadata(metadata)
        key = self._scope_key(scope, scope_id)
        # Budgets first: rejecting over-limit input before the embed keeps a
        # doomed call from paying for a model load.
        self._budgets.validate_remember(topic=topic, summary=summary, content=content)

        now_ms = self._clock()
        embedding = self._embedder.embed_document(topic=topic, summary=summary)
        record = MemoryRecord(
            memory_id=uuid.uuid4().hex,
            brain_id=self.brain_id,
            agent_id=agent_id,
            scope=key.scope,
            scope_id=key.scope_id,
            topic=topic,
            catalog=catalog,
            summary=summary,
            content=content,
            timeline_day=self._day(now_ms),
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            importance=importance,
            expires_at_ms=expires_at_ms_for(importance, now_ms),
            deleted_at_ms=None,
            metadata=metadata,
            profile_id=MODEL_MANIFEST.profile,
            record_version=1,
            embedding=embedding,
        )
        self._repo.store(record)
        self._record_audit(
            AuditAction.REMEMBER,
            record.memory_id,
            agent_id=agent_id,
            at_ms=now_ms,
            detail={"importance": importance, "scope": key.scope.value},
        )
        return RememberResult(
            memory_id=record.memory_id,
            timeline_day=record.timeline_day,
            expires_at_ms=record.expires_at_ms,
        )

    # -- read ----------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        scope: str,
        scope_id: str = "",
        topic: str | None = None,
        catalog: str | None = None,
        timeline_day: str | None = None,
        min_importance: int | None = None,
        days: int | None = None,
    ) -> Sequence[SearchPreview]:
        """Hybrid search in one collection scope; pure read, never touches TTL."""
        self._budgets.validate_query(query)
        key = self._scope_key(scope, scope_id)
        filters = self._filters(
            topic=topic, catalog=catalog, timeline_day=timeline_day,
            min_importance=min_importance, days=days,
        )
        query_vector = self._embedder.embed_query(query)
        return self._retriever.search(
            query_text=query, query_vector=query_vector, scope=key, filters=filters,
        )

    def recent(
        self,
        *,
        scope: str,
        scope_id: str = "",
        limit: int = 20,
        topic: str | None = None,
        catalog: str | None = None,
        timeline_day: str | None = None,
        min_importance: int | None = None,
        days: int | None = None,
    ) -> Sequence[MemoryRecord]:
        """Timeline listing, newest first; pure read, no embedding involved."""
        _require_limit(limit, RECENT_LIMIT_MAX)
        key = self._scope_key(scope, scope_id)
        filters = self._filters(
            topic=topic, catalog=catalog, timeline_day=timeline_day,
            min_importance=min_importance, days=days,
        )
        return self._repo.recent(key, limit=limit, filters=filters)

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Full record by ID within the bound brain; ``None`` when not visible.

        Unknown, cross-brain, expired, and soft-deleted IDs are one answer: a
        forgotten memory is gone from the agent's point of view, and restoring
        it is an admin operation.
        """
        return self._repo.get(memory_id)

    # -- lifecycle -----------------------------------------------------------

    def reinforce(self, memory_id: str, *, agent_id: str) -> MemoryRecord | None:
        """The only TTL renewal: an explicit judgment that a memory proved useful."""
        if not self._mutate(self._repo.reinforce, memory_id, AuditAction.REINFORCE, agent_id):
            return None
        return self._repo.get(memory_id)

    def forget(self, memory_id: str, *, agent_id: str) -> bool:
        """Soft delete: sets ``deleted_at`` and clamps expiry to the grace window."""
        return self._mutate(self._repo.soft_delete, memory_id, AuditAction.FORGET, agent_id)

    def restore(self, memory_id: str, *, agent_id: str) -> MemoryRecord | None:
        """Admin: undo a forget still inside its grace window; re-arms the TTL."""
        if not self._mutate(self._repo.restore, memory_id, AuditAction.RESTORE, agent_id):
            return None
        return self._repo.get(memory_id)

    def hard_delete(self, memory_id: str, *, agent_id: str) -> bool:
        """Admin: permanent removal. Audit history survives (no memory FK)."""
        return self._mutate(
            self._repo.hard_delete, memory_id, AuditAction.HARD_DELETE, agent_id,
        )

    # -- audit ---------------------------------------------------------------

    def audit_events(
        self, *, day: str | None = None, limit: int = AUDIT_LIMIT_MAX,
    ) -> Sequence[AuditEvent]:
        """Structural mutation facts for one brain-day; defaults to today."""
        _require_limit(limit, AUDIT_LIMIT_MAX)
        resolved = day if day is not None else self._day(self._clock())
        return self._audit.list_day(resolved)[:limit]

    # -- health --------------------------------------------------------------

    def health(self, *, agent_id: str, deep: bool = False) -> dict[str, Any]:
        """Service state without side effects — never forces a model load.

        The embedding model loads lazily, so ``not_loaded`` is healthy; only a
        recorded load failure degrades the service. Storage contributes schema,
        profile, and extension state. ``deep`` opts into the integrity check,
        which walks the whole database and belongs to ``doctor`` rather than to
        a liveness answer.

        A profile that does not match the locked manifest is degraded, not
        broken: rows written under another profile mean a re-embedding
        migration is incomplete, and mixed-profile search must not start.
        """
        embedding_state = self._embedder.health()
        storage = self._storage.state(deep=deep)
        degraded = (
            embedding_state is EmbeddingHealth.ERROR
            or not storage.schema_ok
            or not storage.profile_matches_manifest
            or storage.integrity_ok is False
        )
        return {
            "status": "degraded" if degraded else "ok",
            "brain_id": self.brain_id,
            "agent_id": agent_id,
            "timeline_timezone": self.timezone,
            "embedding_profile": MODEL_MANIFEST.profile,
            "embedding_state": embedding_state.value,
            "embedding_dimensions": MODEL_MANIFEST.dimensions,
            "storage": {
                "schema_version": storage.schema_version,
                "schema_ok": storage.schema_ok,
                "profile_id": storage.profile_id,
                "profile_matches_manifest": storage.profile_matches_manifest,
                "vector_backend": storage.vector_backend,
                "integrity_ok": storage.integrity_ok,
                "detail": storage.detail,
            },
        }

    # -- helpers -------------------------------------------------------------

    def _day(self, at_ms: int) -> str:
        return timeline_day_for(at_ms, self._config.timeline_timezone)

    def _mutate(
        self,
        action: Callable[[str], MutationOutcome],
        memory_id: str,
        audit_action: AuditAction,
        agent_id: str,
    ) -> bool:
        """Run one by-ID lifecycle mutation; audit it only when it applied."""
        if action(memory_id) is not MutationOutcome.APPLIED:
            return False
        at_ms = self._clock()
        self._record_audit(audit_action, memory_id, agent_id=agent_id, at_ms=at_ms)
        return True

    def _record_audit(
        self,
        action: AuditAction,
        memory_id: str,
        *,
        agent_id: str,
        at_ms: int,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Record one structural fact. Never fails an already-committed mutation."""
        self._audit.record(
            AuditEvent(
                event_id=uuid.uuid4().hex,
                brain_id=self.brain_id,
                memory_id=memory_id,
                agent_id=agent_id,
                action=action,
                event_at_ms=at_ms,
                timeline_day=self._day(at_ms),
                detail=detail or {},
            )
        )

    def _scope_key(self, scope: str, scope_id: str) -> ScopeKey:
        """Normalize scope, filling in the canonical global ``scope_id``.

        ``scope=global`` pins the literal ``'global'``; user/project scopes
        need an explicit id, rejected here with an actionable message because
        the tool schema marks it optional for global's sake.
        """
        try:
            parsed = Scope(scope)
        except ValueError:
            allowed = ", ".join(s.value for s in Scope)
            raise ValidationError(
                f"scope must be one of {allowed}; got {scope!r}"
            ) from None
        if parsed is Scope.GLOBAL:
            if scope_id and scope_id != GLOBAL_SCOPE_ID:
                raise ValidationError(
                    f"scope=global canonicalizes scope_id to {GLOBAL_SCOPE_ID!r},"
                    f" got {scope_id!r}"
                )
            return ScopeKey(parsed, GLOBAL_SCOPE_ID)
        if not scope_id:
            raise ValidationError(
                f"scope_id is required when scope={parsed.value!r} — pass the user"
                f" name or project slug (only scope='global' may omit it)"
            )
        return ScopeKey(parsed, scope_id)

    def _filters(
        self,
        *,
        topic: str | None,
        catalog: str | None,
        timeline_day: str | None,
        min_importance: int | None,
        days: int | None,
    ) -> RecentFilters:
        since_ms: int | None = None
        if days is not None:
            if isinstance(days, bool) or not isinstance(days, int) or days < 1:
                raise ValidationError(f"days must be a positive integer, got {days!r}")
            since_ms = self._clock() - days * _DAY_SECONDS * 1000
        return RecentFilters(
            topic=topic,
            catalog=catalog,
            since_ms=since_ms,
            timeline_day=timeline_day,
            min_importance=min_importance,
        )


def _validated_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """A JSON object with no NaN/Infinity — strict MCP clients reject those."""
    values = dict(metadata or {})
    try:
        json.dumps(values, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValidationError("metadata must be a JSON-serializable object") from None
    return values


def _require_limit(limit: int, maximum: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValidationError(
            f"limit must be an integer between 1 and {maximum}, got {limit!r}"
        )
