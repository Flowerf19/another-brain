"""BrainTools — registers the 8 brain_* MCP tools on a FastMCP server and
delegates to MemoryService. Thin adapter: converts domain results to
JSON-safe dicts, timestamps to ISO 8601 in the configured timezone.

Tool descriptions are the LLM-facing contract: search/recent return preview
lines only (detail via brain_get), and after actually using a memory the
agent closes the loop explicitly — brain_reinforce when it proved correct
and valuable, brain_forget when it proved wrong (Step 04 §4.2, §6.5).
brain_audit is the admin/observability read over the mutation trail.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from memory.models import MemoryRecord, MemorySearchResult, timeline_day_from_ts
from memory.service import MemoryService


def _iso(ts: float | None, tz_name: str) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), ZoneInfo(tz_name)).isoformat()


def _preview_core(memory_id: str, item: Any) -> dict[str, Any]:
    return {
        "memory_id": memory_id,
        "topic": item.topic,
        "catalog": item.catalog,
        "summary": item.summary,
        "timeline_day": item.timeline_day,
        "importance": item.importance,
        "has_content": item.has_content,
    }


def _preview(result: MemorySearchResult) -> dict[str, Any]:
    return {
        **_preview_core(result.memory_id, result),
        "relevance_score": result.relevance_score,
        "score_source": result.score_source.value,
    }


def _record_preview(record: MemoryRecord) -> dict[str, Any]:
    return _preview_core(record.identity.memory_id, record)


def _audit_preview(event: Any, tz_name: str) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "action": event.action,
        "memory_id": event.memory_id,
        "agent_id": event.agent_id,
        "ts": _iso(event.ts, tz_name),
        "detail": event.detail,
    }


def register_tools(server: Any, service: MemoryService) -> None:
    """Attach the brain_* tools to a FastMCP server instance."""
    tz = service.timezone

    @server.tool()
    async def brain_remember(
        topic: str,
        summary: str,
        scope: str,
        scope_id: str = "",
        catalog: str = "note",
        content: str = "",
        importance: int = 3,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store one memory as a timeline (diary) entry. Call this when you
        learn something worth recalling later: a decision, a bug and its fix,
        a user preference, a fact, a task.

        topic: lowercase-kebab slug labeling the entry (e.g. "redis-upgrade").
        summary: 1-2 sentences, the diary line — this is what gets embedded
        and previewed in search. content: optional full detail or checklist
        (markdown), fetched only via brain_get. catalog: open kebab-case
        class — starter set: bug, decision, preference, task, fact, note.
        scope: user | project | global. scope_id is REQUIRED for user
        (the user name) and project (the project slug); only scope=global
        may omit it (pinned to "global").
        importance 1-5 sets retention: 5=365d, 4=180d, 3=90d, 2=30d, 1=7d;
        the entry expires unless brain_reinforce renews it. Repeats of the
        same knowledge on the same day are fine — the store is append-only.
        """
        result = await service.remember(
            topic, summary,
            scope=scope, scope_id=scope_id, catalog=catalog, content=content,
            importance=importance, metadata=metadata,
        )
        return {
            "memory_id": result.memory_id,
            "timeline_day": result.timeline_day,
            "expires_at": _iso(result.expires_at, tz),
        }

    @server.tool()
    async def brain_search(
        query: str,
        scope: str,
        scope_id: str = "",
        topic: str | None = None,
        catalog: str | None = None,
        timeline_day: str | None = None,
        min_importance: int | None = None,
        days: int | None = None,
    ) -> dict[str, Any]:
        """Search memories by meaning and keywords (hybrid semantic + text
        ranking). Returns preview lines only — call brain_get(memory_id) when
        a result has has_content=true and you need the detail.

        Reading results changes nothing. After you actually USE a memory,
        close the loop: brain_reinforce if it proved correct and valuable,
        brain_forget if it proved wrong. scope_id is REQUIRED for
        scope=user/project (only global may omit it). Optional filters:
        topic slug, catalog, timeline_day (YYYY-MM-DD), min_importance
        (1-5), days (only memories from the last N days).
        """
        results = await service.search(
            query,
            scope=scope, scope_id=scope_id, topic=topic, catalog=catalog,
            timeline_day=timeline_day, min_importance=min_importance, days=days,
        )
        return {"count": len(results), "results": [_preview(r) for r in results]}

    @server.tool()
    async def brain_recent(
        scope: str,
        scope_id: str = "",
        topic: str | None = None,
        catalog: str | None = None,
        timeline_day: str | None = None,
        min_importance: int | None = None,
        days: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List the newest memories on the timeline (no query — pure listing,
        newest first). Use it to catch up on what was stored recently or to
        trace one day (timeline_day) or one topic. Same preview shape as
        brain_search, without relevance scores. scope_id is REQUIRED for
        scope=user/project (only global may omit it). limit defaults to the
        configured search page size (max 100)."""
        records = await service.recent(
            scope=scope, scope_id=scope_id, topic=topic, catalog=catalog,
            timeline_day=timeline_day, min_importance=min_importance,
            days=days, limit=limit,
        )
        return {
            "count": len(records),
            "results": [_record_preview(r) for r in records],
        }

    @server.tool()
    async def brain_get(memory_id: str) -> dict[str, Any]:
        """Fetch one memory in full, including content and metadata — the
        detail pull behind a search/recent preview. Pure read: fetching never
        extends the memory's lifetime. After using it, brain_reinforce or
        brain_forget as appropriate."""
        detail = await service.get(memory_id)
        if detail is None:
            return {"found": False, "memory_id": memory_id}
        record = detail.record
        return {
            "found": True,
            **_record_preview(record),
            "content": record.content,
            "scope": record.identity.scope.value,
            "scope_id": record.identity.scope_id,
            "agent_id": record.identity.agent_id,
            "metadata": record.metadata,
            "created_at": _iso(record.created_at, tz),
            "updated_at": _iso(record.updated_at, tz),
            "period_start": _iso(record.period_start, tz),
            "period_end": _iso(record.period_end, tz),
            "expires_at": _iso(detail.expires_at, tz),
        }

    @server.tool()
    async def brain_reinforce(memory_id: str) -> dict[str, Any]:
        """Renew a memory's retention after it proved correct and valuable in
        use. This is the ONLY way a memory's expiry is extended — re-arms the
        full TTL for its importance. Don't reinforce on sight: fetch, use,
        judge, then reinforce."""
        detail = await service.reinforce(memory_id)
        if detail is None:
            return {"ok": False, "memory_id": memory_id, "reason": "not_found"}
        return {
            "ok": True,
            "memory_id": memory_id,
            "expires_at": _iso(detail.expires_at, tz),
        }

    @server.tool()
    async def brain_forget(memory_id: str) -> dict[str, Any]:
        """Forget a memory that proved wrong or stale. Soft delete: it
        disappears from all search/recent/get immediately and is purged after
        a grace window (admin can restore within it). Use deliberately —
        harmless outdated memories can just expire on their own."""
        ok = await service.forget(memory_id)
        return {
            "ok": ok,
            "memory_id": memory_id,
            **({} if ok else {"reason": "not_found"}),
        }

    @server.tool()
    async def brain_health() -> dict[str, Any]:
        """Service health: Redis reachability, active index contract
        (embedding model/dim), and the identity this server writes with
        (brain_id/agent_id)."""
        return await service.health()

    @server.tool()
    async def brain_audit(
        day: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Read the memory-mutation audit trail for one day (admin /
        observability). Secret-free: each event records the action
        (remember/reinforce/forget/restore/hard_delete), memory_id, the acting
        agent_id, and a timestamp — never the memory text. Pure read; brain_id
        is server-bound. day is YYYY-MM-DD (defaults to today in the server's
        timezone); newest events first."""
        resolved_day = day or timeline_day_from_ts(time.time(), tz)
        events = await service.audit_events(day=resolved_day, limit=limit)
        return {
            "count": len(events),
            "day": resolved_day,
            "events": [_audit_preview(e, tz) for e in events],
        }
