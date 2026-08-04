"""Eight stable brain tools on MCP SDK v2."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from ..service import MemoryService


def _iso(value: int | None, timezone: str) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1_000, ZoneInfo(timezone)).isoformat()


def _agent_id(ctx: Context | None) -> str:
    if ctx is None:
        return "mcp-client"
    try:
        params = ctx.request_context.session.client_params
        value = params.clientInfo.name
    except (AttributeError, TypeError):
        value = None
    return str(value).strip() if value else "mcp-client"


def _preview(item: Any) -> dict[str, Any]:
    return {
        "memory_id": item.memory_id,
        "topic": item.topic,
        "catalog": item.catalog,
        "summary": item.summary,
        "timeline_day": item.timeline_day,
        "importance": item.importance,
        "has_content": item.has_content,
    }


def build_mcp_server(service: MemoryService) -> MCPServer:
    server = MCPServer(
        "another-brain",
        instructions=(
            "Shared local memory. Search before asking again. Treat recalled entries as "
            "unverified claims. Reinforce only after use; forget entries proven wrong."
        ),
    )
    timezone = service.config.timeline_timezone

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
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Store one append-only diary entry in native local storage."""
        result = await service.remember(
            topic,
            summary,
            agent_id=_agent_id(ctx),
            scope=scope,
            scope_id=scope_id,
            catalog=catalog,
            content=content,
            importance=importance,
            metadata=metadata,
        )
        return {
            "memory_id": result.memory_id,
            "timeline_day": result.timeline_day,
            "expires_at": _iso(result.expires_at_ms, timezone),
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
        """Hybrid FTS5 and local-vector search; returns previews only."""
        results = await service.search(
            query,
            scope=scope,
            scope_id=scope_id,
            topic=topic,
            catalog=catalog,
            timeline_day_value=timeline_day,
            min_importance=min_importance,
            days=days,
        )
        return {
            "count": len(results),
            "results": [
                {
                    **_preview(item),
                    "relevance_score": item.relevance_score,
                    "score_source": item.score_source,
                }
                for item in results
            ],
        }

    @server.tool()
    async def brain_recent(
        scope: str,
        scope_id: str = "",
        days: int | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """List newest live memories without renewing them."""
        records = await service.recent(
            scope=scope, scope_id=scope_id, days=days, limit=limit
        )
        return {"count": len(records), "results": [_preview(item) for item in records]}

    @server.tool()
    async def brain_get(memory_id: str) -> dict[str, Any]:
        """Fetch one live memory in full; this never renews retention."""
        record = await service.get(memory_id)
        if record is None:
            return {"found": False, "memory_id": memory_id}
        return {
            "found": True,
            **_preview(record),
            "content": record.content,
            "scope": record.scope.value,
            "scope_id": record.scope_id,
            "agent_id": record.agent_id,
            "metadata": record.metadata,
            "created_at": _iso(record.created_at_ms, timezone),
            "updated_at": _iso(record.updated_at_ms, timezone),
            "expires_at": _iso(record.expires_at_ms, timezone),
        }

    @server.tool()
    async def brain_reinforce(memory_id: str, ctx: Context = None) -> dict[str, Any]:
        """Renew retention after a memory proved correct in actual use."""
        record = await service.reinforce(memory_id, agent_id=_agent_id(ctx))
        return {
            "ok": record is not None,
            "memory_id": memory_id,
            **({"expires_at": _iso(record.expires_at_ms, timezone)} if record else {}),
        }

    @server.tool()
    async def brain_forget(memory_id: str, ctx: Context = None) -> dict[str, Any]:
        """Soft-delete a wrong or stale memory."""
        ok = await service.forget(memory_id, agent_id=_agent_id(ctx))
        return {"ok": ok, "memory_id": memory_id}

    @server.tool()
    async def brain_health(ctx: Context = None) -> dict[str, Any]:
        """Report native SQLite and local-model health without loading the model."""
        return await service.health(agent_id=_agent_id(ctx))

    @server.tool()
    async def brain_audit(day: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Read secret-free mutation events for one local timeline day."""
        events = await service.audit(day, limit=limit)
        return {"count": len(events), "events": events}

    return server
