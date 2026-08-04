"""Application service for native memory operations."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .config import AppConfig
from .domain.models import MemoryRecord, SearchFilters, SearchResult, timeline_day
from .embedding.provider import OnnxEmbeddingProvider
from .errors import ValidationError
from .retrieval.service import HybridRetriever
from .storage.repository import SQLiteRepository


@dataclass(frozen=True)
class RememberResult:
    memory_id: str
    timeline_day: str
    expires_at_ms: int


class MemoryService:
    def __init__(
        self,
        config: AppConfig,
        repository: SQLiteRepository,
        retriever: HybridRetriever,
        embedder: OnnxEmbeddingProvider,
    ):
        self.config = config
        self.repository = repository
        self.retriever = retriever
        self.embedder = embedder

    async def remember(
        self,
        topic: str,
        summary: str,
        *,
        agent_id: str,
        scope: str,
        scope_id: str = "",
        catalog: str = "note",
        content: str = "",
        importance: int = 3,
        metadata: Mapping[str, Any] | None = None,
        now_ms: int | None = None,
    ) -> RememberResult:
        record = MemoryRecord.new(
            brain_id=self.config.brain_id,
            agent_id=agent_id,
            scope=scope,
            scope_id=scope_id,
            topic=topic,
            summary=summary,
            timezone=self.config.timeline_timezone,
            catalog=catalog,
            content=content,
            importance=importance,
            metadata=metadata,
            now_ms=now_ms,
        )
        self.embedder.validate_topic(record.topic)
        self.embedder.validate_content(record.content)
        embedding = await self.embedder.embed_document(record.topic, record.summary)
        await asyncio.to_thread(self.repository.store, record, embedding)
        await asyncio.to_thread(
            self.repository.record_audit,
            brain_id=self.config.brain_id,
            memory_id=record.memory_id,
            agent_id=agent_id,
            action="remember",
            event_at_ms=record.created_at_ms,
            detail={"importance": importance, "scope": record.scope.value},
        )
        return RememberResult(record.memory_id, record.timeline_day, record.expires_at_ms)

    async def search(
        self,
        query: str,
        *,
        scope: str,
        scope_id: str = "",
        topic: str | None = None,
        catalog: str | None = None,
        timeline_day_value: str | None = None,
        min_importance: int | None = None,
        days: int | None = None,
        now_ms: int | None = None,
    ) -> list[SearchResult]:
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("query must be non-empty")
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        if days is not None and (isinstance(days, bool) or days < 1):
            raise ValidationError("days must be a positive integer")
        filters = SearchFilters.create(
            scope,
            scope_id,
            topic=topic,
            catalog=catalog,
            timeline_day=timeline_day_value,
            min_importance=min_importance,
            since_ms=None if days is None else now - days * 86_400_000,
        )
        embedding = await self.embedder.embed_query(query)
        return await asyncio.to_thread(
            self.retriever.search,
            self.config.brain_id,
            filters,
            query,
            embedding,
            now_ms=now,
        )

    async def recent(
        self,
        *,
        scope: str,
        scope_id: str = "",
        topic: str | None = None,
        catalog: str | None = None,
        timeline_day_value: str | None = None,
        min_importance: int | None = None,
        days: int | None = None,
        limit: int = 10,
        now_ms: int | None = None,
    ) -> list[MemoryRecord]:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        filters = SearchFilters.create(
            scope,
            scope_id,
            topic=topic,
            catalog=catalog,
            timeline_day=timeline_day_value,
            min_importance=min_importance,
            since_ms=None if days is None else now - days * 86_400_000,
        )
        return await asyncio.to_thread(
            self.repository.recent,
            self.config.brain_id,
            filters,
            limit,
            now_ms=now,
        )

    async def get(self, memory_id: str) -> MemoryRecord | None:
        return await asyncio.to_thread(self.repository.get, self.config.brain_id, memory_id)

    async def reinforce(
        self, memory_id: str, *, agent_id: str, now_ms: int | None = None
    ) -> MemoryRecord | None:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        record = await asyncio.to_thread(
            self.repository.reinforce, self.config.brain_id, memory_id, now_ms=now
        )
        if record:
            await asyncio.to_thread(
                self.repository.record_audit,
                brain_id=self.config.brain_id,
                memory_id=memory_id,
                agent_id=agent_id,
                action="reinforce",
                event_at_ms=now,
            )
        return record

    async def forget(
        self, memory_id: str, *, agent_id: str, now_ms: int | None = None
    ) -> bool:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        deleted = await asyncio.to_thread(
            self.repository.soft_delete,
            self.config.brain_id,
            memory_id,
            now_ms=now,
            grace_ms=self.config.forget_grace_seconds * 1_000,
        )
        if deleted:
            await asyncio.to_thread(
                self.repository.record_audit,
                brain_id=self.config.brain_id,
                memory_id=memory_id,
                agent_id=agent_id,
                action="forget",
                event_at_ms=now,
            )
        return deleted

    async def restore(
        self, memory_id: str, *, agent_id: str, now_ms: int | None = None
    ) -> MemoryRecord | None:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        record = await asyncio.to_thread(
            self.repository.restore, self.config.brain_id, memory_id, now_ms=now
        )
        if record:
            await asyncio.to_thread(
                self.repository.record_audit,
                brain_id=self.config.brain_id,
                memory_id=memory_id,
                agent_id=agent_id,
                action="restore",
                event_at_ms=now,
            )
        return record

    async def hard_delete(self, memory_id: str, *, agent_id: str) -> bool:
        deleted = await asyncio.to_thread(
            self.repository.hard_delete, self.config.brain_id, memory_id
        )
        if deleted:
            now = int(time.time() * 1000)
            await asyncio.to_thread(
                self.repository.record_audit,
                brain_id=self.config.brain_id,
                memory_id=memory_id,
                agent_id=agent_id,
                action="hard_delete",
                event_at_ms=now,
            )
        return deleted

    async def audit(self, day: str | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
        resolved = day or timeline_day(
            int(time.time() * 1000), self.config.timeline_timezone
        )
        return await asyncio.to_thread(
            self.repository.list_audit, self.config.brain_id, resolved, limit=limit
        )

    async def health(self, *, agent_id: str = "local") -> dict[str, Any]:
        storage = await asyncio.to_thread(self.repository.health)
        return {
            "status": "ok" if storage["integrity"] == "ok" else "degraded",
            "brain_id": self.config.brain_id,
            "agent_id": agent_id,
            "storage": storage,
            "embedding_model": self.embedder.model_name,
            "embedding_ready": self.embedder.is_loaded,
            "embedding_error": self.embedder.load_error,
        }
