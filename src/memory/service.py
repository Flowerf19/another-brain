"""MemoryService — remember, search, recent, get, reinforce, forget, and
health use cases on top of the repository + search engine.

Owns the write-time validation the storage contract assigns to the service
layer (CONTENT_MAX_CHARS, JSON-safe metadata) and binds the trusted
brain_id from config — tool inputs never choose the brain. agent_id is
provenance detected per MCP session (clientInfo) and arrives as a call
parameter. All reads are pure: only brain_reinforce re-arms a TTL
(Step 04 §4.2).

Audit events (reinforce/forget/restore, Step 04 §4.2) are wired in the
audit slice; this service exposes the lifecycle results they will record.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from audit.models import AuditAction, AuditEvent
from audit.service import AuditService
from config import AppConfig
from errors import ValidationError
from memory.embeddings import EmbeddingProvider
from memory.models import (
    GLOBAL_SCOPE_ID,
    MemoryCatalog,
    MemoryRecord,
    MemoryScope,
    MemorySearchResult,
    SearchFilters,
    timeline_day_from_ts,
)
from memory.search import MemorySearchEngine
from storage.redis_index import RedisIndexManager
from storage.redis_repository import RedisMemoryRepository

RECENT_LIMIT_MAX = 100
AUDIT_LIMIT_MAX = 500


@dataclass(frozen=True)
class RememberResult:
    memory_id: str
    timeline_day: str
    expires_at: float | None


@dataclass(frozen=True)
class MemoryDetail:
    record: MemoryRecord
    expires_at: float | None


class MemoryService:
    def __init__(
        self,
        repository: RedisMemoryRepository,
        engine: MemorySearchEngine,
        embedder: EmbeddingProvider,
        config: AppConfig,
        *,
        index: RedisIndexManager | None = None,
        audit: AuditService | None = None,
    ):
        self._repo = repository
        self._engine = engine
        self._embedder = embedder
        self._config = config
        self._index = index
        self._audit = audit

    @property
    def timezone(self) -> str:
        return self._config.timeline_timezone

    # --------------------------------------------------------------- write

    async def remember(
        self,
        topic: str,
        summary: str,
        *,
        agent_id: str,
        scope: str,
        scope_id: str = "",
        catalog: str = MemoryCatalog.DEFAULT,
        content: str = "",
        importance: int = 3,
        metadata: Mapping[str, Any] | None = None,
        period_start: float | None = None,
        period_end: float | None = None,
        now_ts: float | None = None,
    ) -> RememberResult:
        """Append one diary entry (no merge, §6.6): embed the summary, store
        the record, arm the importance TTL."""
        if not isinstance(content, str):
            raise ValidationError("content must be a string")
        if len(content) > self._config.content_max_chars:
            raise ValidationError(
                f"content is {len(content)} chars — the cap is "
                f"{self._config.content_max_chars} (CONTENT_MAX_CHARS); "
                f"store a summary and keep the detail elsewhere"
            )
        metadata = dict(metadata or {})
        try:
            # allow_nan=False: NaN/Infinity pass the default json.dumps but
            # are not JSON — strict consumers (MCP clients) would choke.
            json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValidationError("metadata must be a JSON-serializable object") from None

        record = MemoryRecord.new(
            brain_id=self._config.brain_id,
            agent_id=agent_id,
            scope=scope,
            scope_id=self._pin_scope_id(scope, scope_id),
            topic=topic,
            summary=summary,
            tz_name=self._config.timeline_timezone,
            catalog=catalog,
            content=content,
            importance=importance,
            period_start=period_start,
            period_end=period_end,
            metadata=metadata,
            now_ts=now_ts,
        )
        embedding = await self._embedder.embed_document(record.summary)
        await self._repo.store(record, embedding)
        expires_at = await self._repo.expire_at(
            self._config.brain_id, record.identity.memory_id
        )
        await self._record_audit(
            AuditAction.REMEMBER,
            record.identity.memory_id,
            agent_id=agent_id,
            ts=record.created_at,
            detail={"importance": record.importance, "scope": record.identity.scope.value},
        )
        return RememberResult(
            memory_id=record.identity.memory_id,
            timeline_day=record.timeline_day,
            expires_at=expires_at,
        )

    # ---------------------------------------------------------------- read

    async def search(
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
        now_ts: float | None = None,
    ) -> list[MemorySearchResult]:
        """Hybrid search, pure read — never touches any TTL."""
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("query must be a non-empty string")
        filters = self._filters(
            scope, scope_id,
            topic=topic, catalog=catalog, timeline_day=timeline_day,
            min_importance=min_importance, days=days, now_ts=now_ts,
        )
        query_embedding = await self._embedder.embed_query(query)
        return await self._engine.search(
            self._config.brain_id, filters, query, query_embedding
        )

    async def recent(
        self,
        *,
        scope: str,
        scope_id: str = "",
        topic: str | None = None,
        catalog: str | None = None,
        timeline_day: str | None = None,
        min_importance: int | None = None,
        days: int | None = None,
        limit: int | None = None,
        now_ts: float | None = None,
    ) -> list[MemoryRecord]:
        """Timeline listing, newest first, pure read (§6.3)."""
        if limit is None:
            # top_k has no upper bound in config — clamp so the default
            # limit never trips the RECENT_LIMIT_MAX guard below.
            limit = min(self._config.search.top_k, RECENT_LIMIT_MAX)
        if isinstance(limit, bool) or not isinstance(limit, int) \
                or not 1 <= limit <= RECENT_LIMIT_MAX:
            raise ValidationError(
                f"limit must be an int between 1 and {RECENT_LIMIT_MAX}, got {limit!r}"
            )
        filters = self._filters(
            scope, scope_id,
            topic=topic, catalog=catalog, timeline_day=timeline_day,
            min_importance=min_importance, days=days, now_ts=now_ts,
        )
        hits = await self._repo.recent(self._config.brain_id, filters, limit)
        return [hit.record for hit in hits]

    async def get(self, memory_id: str) -> MemoryDetail | None:
        """Full record by id, pure read. A soft-deleted record is already
        forgotten from the agent's point of view — reported as not found;
        restore is an admin operation."""
        record, expires_at = await asyncio.gather(
            self._repo.get(self._config.brain_id, memory_id),
            self._repo.expire_at(self._config.brain_id, memory_id),
        )
        if record is None or record.is_deleted:
            return None
        return MemoryDetail(record=record, expires_at=expires_at)

    # ------------------------------------------------------------ lifecycle

    async def reinforce(
        self, memory_id: str, *, agent_id: str, now_ts: float | None = None
    ) -> MemoryDetail | None:
        """The only TTL renewal (§4.2.2) — an explicit judgment that a fetched
        memory proved correct and valuable. None when missing or soft-deleted."""
        now = float(now_ts) if now_ts is not None else time.time()
        record = await self._repo.reinforce(
            self._config.brain_id, memory_id, now_ts=now
        )
        if record is None:
            return None
        expires_at = await self._repo.expire_at(self._config.brain_id, memory_id)
        await self._record_audit(AuditAction.REINFORCE, memory_id, agent_id=agent_id, ts=now)
        return MemoryDetail(record=record, expires_at=expires_at)

    async def forget(
        self, memory_id: str, *, agent_id: str, now_ts: float | None = None
    ) -> bool:
        """Soft delete (§4.2.3): sets deleted_at, shrinks the TTL to the grace
        window. False when the memory does not exist."""
        now = float(now_ts) if now_ts is not None else time.time()
        deleted = await self._repo.soft_delete(
            self._config.brain_id, memory_id, now_ts=now
        )
        if deleted:
            await self._record_audit(AuditAction.FORGET, memory_id, agent_id=agent_id, ts=now)
        return deleted

    # -------------------------------------------------------- admin lifecycle

    async def restore(
        self, memory_id: str, *, agent_id: str, now_ts: float | None = None
    ) -> MemoryDetail | None:
        """Admin restore within the grace window (§4.2.4): clears deleted_at and
        re-arms the importance TTL. None when the memory is already gone."""
        now = float(now_ts) if now_ts is not None else time.time()
        record = await self._repo.restore(self._config.brain_id, memory_id)
        if record is None:
            return None
        expires_at = await self._repo.expire_at(self._config.brain_id, memory_id)
        await self._record_audit(AuditAction.RESTORE, memory_id, agent_id=agent_id, ts=now)
        return MemoryDetail(record=record, expires_at=expires_at)

    async def hard_delete(
        self, memory_id: str, *, agent_id: str, now_ts: float | None = None
    ) -> bool:
        """Admin-only DEL (§4.2.5). False when the memory does not exist."""
        now = float(now_ts) if now_ts is not None else time.time()
        deleted = await self._repo.hard_delete(self._config.brain_id, memory_id)
        if deleted:
            await self._record_audit(AuditAction.HARD_DELETE, memory_id, agent_id=agent_id, ts=now)
        return deleted

    async def audit_events(
        self, *, day: str | None = None, limit: int | None = None, now_ts: float | None = None
    ) -> list[AuditEvent]:
        """Read the audit trail for one brain-day (admin/observability). Pure
        read. Defaults to today in the configured timezone."""
        if self._audit is None:
            return []
        if day is None:
            now = float(now_ts) if now_ts is not None else time.time()
            day = timeline_day_from_ts(now, self._config.timeline_timezone)
        if limit is None:
            limit = AUDIT_LIMIT_MAX
        if isinstance(limit, bool) or not isinstance(limit, int) \
                or not 1 <= limit <= AUDIT_LIMIT_MAX:
            raise ValidationError(
                f"limit must be an int between 1 and {AUDIT_LIMIT_MAX}, got {limit!r}"
            )
        return await self._audit.list_day(self._config.brain_id, day, limit=limit)

    # -------------------------------------------------------------- health

    async def health(self, *, agent_id: str) -> dict[str, Any]:
        redis_ok = await self._repo.ping()
        index_meta: dict[str, str] = {}
        if redis_ok and self._index is not None:
            try:
                index_meta = await self._index.read_meta()
            except Exception:
                index_meta = {}
        # Embedding loads lazily by design, so "not loaded yet" is healthy;
        # only a failed load degrades the service.
        embedding_error = self._embedder.load_error
        ok = (
            redis_ok
            and embedding_error is None
            and (self._index is None or bool(index_meta))
        )
        return {
            "status": "ok" if ok else "degraded",
            "redis": redis_ok,
            "index": index_meta,
            "brain_id": self._config.brain_id,
            "agent_id": agent_id,
            "embedding_model": self._embedder.model_name,
            "embedding_dim": self._embedder.dim,
            "embedding_ready": self._embedder.is_loaded,
            "embedding_error": embedding_error,
        }

    # -------------------------------------------------------------- helpers

    async def _record_audit(
        self,
        action: str,
        memory_id: str,
        *,
        agent_id: str,
        ts: float,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget audit write with the caller's identity. The
        AuditService swallows Redis I/O errors, so this never fails a mutation."""
        if self._audit is None:
            return
        await self._audit.record(
            AuditEvent(
                action=action,
                memory_id=memory_id,
                brain_id=self._config.brain_id,
                agent_id=agent_id,
                ts=ts,
                detail=detail or {},
            )
        )

    @staticmethod
    def _pin_scope_id(scope: str, scope_id: str) -> str:
        """scope=global pins the literal scope_id 'global' (Step 04 §1.6);
        an omitted scope_id is filled in, a conflicting one is rejected by
        the domain model. For user/project an empty scope_id is rejected
        here with an actionable message (the tool schema marks it optional
        because global doesn't need it)."""
        parsed = MemoryScope.parse(scope)
        if parsed is MemoryScope.GLOBAL and not scope_id:
            return GLOBAL_SCOPE_ID
        if not scope_id:
            raise ValidationError(
                f"scope_id is required when scope={parsed.value!r} — pass the "
                f"user name or project slug (only scope='global' may omit it)"
            )
        return scope_id

    def _filters(
        self,
        scope: str,
        scope_id: str,
        *,
        topic: str | None,
        catalog: str | None,
        timeline_day: str | None,
        min_importance: int | None,
        days: int | None,
        now_ts: float | None,
    ) -> SearchFilters:
        since_ts: float | None = None
        if days is not None:
            if isinstance(days, bool) or not isinstance(days, int) or days < 1:
                raise ValidationError(f"days must be a positive int, got {days!r}")
            now = float(now_ts) if now_ts is not None else time.time()
            since_ts = now - days * 86_400
        return SearchFilters(
            scope=MemoryScope.parse(scope),
            scope_id=self._pin_scope_id(scope, scope_id),
            topic=topic,
            catalog=catalog,
            timeline_day=timeline_day,
            min_importance=min_importance,
            since_ts=since_ts,
        )
