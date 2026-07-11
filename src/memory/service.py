"""MemoryService — remember, search, recent, get, reinforce, forget, and
health use cases on top of the repository + search engine.

Owns the write-time validation the storage contract assigns to the service
layer (CONTENT_MAX_CHARS, JSON-safe metadata) and binds the trusted
brain_id/agent_id from config — tool inputs never choose the brain. All
reads are pure: only brain_reinforce re-arms a TTL (Step 04 §4.2).

Audit events (reinforce/forget/restore, Step 04 §4.2) are wired in the
audit slice; this service exposes the lifecycle results they will record.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

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
)
from memory.search import MemorySearchEngine
from storage.redis_index import RedisIndexManager
from storage.redis_repository import RedisMemoryRepository

RECENT_LIMIT_MAX = 100


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
    ):
        self._repo = repository
        self._engine = engine
        self._embedder = embedder
        self._config = config
        self._index = index

    @property
    def timezone(self) -> str:
        return self._config.timeline_timezone

    # --------------------------------------------------------------- write

    async def remember(
        self,
        topic: str,
        summary: str,
        *,
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
            json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError):
            raise ValidationError("metadata must be a JSON-serializable object") from None

        record = MemoryRecord.new(
            brain_id=self._config.brain_id,
            agent_id=self._config.agent_id,
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
            limit = self._config.search.top_k
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
        record = await self._repo.get(self._config.brain_id, memory_id)
        if record is None or record.is_deleted:
            return None
        expires_at = await self._repo.expire_at(self._config.brain_id, memory_id)
        return MemoryDetail(record=record, expires_at=expires_at)

    # ------------------------------------------------------------ lifecycle

    async def reinforce(
        self, memory_id: str, *, now_ts: float | None = None
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
        return MemoryDetail(record=record, expires_at=expires_at)

    async def forget(self, memory_id: str, *, now_ts: float | None = None) -> bool:
        """Soft delete (§4.2.3): sets deleted_at, shrinks the TTL to the grace
        window. False when the memory does not exist."""
        now = float(now_ts) if now_ts is not None else time.time()
        return await self._repo.soft_delete(
            self._config.brain_id, memory_id, now_ts=now
        )

    # -------------------------------------------------------------- health

    async def health(self) -> dict[str, Any]:
        redis_ok = await self._repo.ping()
        index_meta: dict[str, str] = {}
        if redis_ok and self._index is not None:
            try:
                index_meta = await self._index.read_meta()
            except Exception:
                index_meta = {}
        ok = redis_ok and (self._index is None or bool(index_meta))
        return {
            "status": "ok" if ok else "degraded",
            "redis": redis_ok,
            "index": index_meta,
            "brain_id": self._config.brain_id,
            "agent_id": self._config.agent_id,
            "embedding_model": self._embedder.model_name,
            "embedding_dim": self._embedder.dim,
        }

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _pin_scope_id(scope: str, scope_id: str) -> str:
        """scope=global pins the literal scope_id 'global' (Step 04 §1.6);
        an omitted scope_id is filled in, a conflicting one is rejected by
        the domain model."""
        if MemoryScope.parse(scope) is MemoryScope.GLOBAL and not scope_id:
            return GLOBAL_SCOPE_ID
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
