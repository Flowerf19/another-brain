"""The eight ``brain_*`` MCP tools (TASK-066).

A thin adapter over :class:`~another_brain.services.memory_service.MemoryService`:
it converts domain objects to JSON-safe dicts and epoch milliseconds to ISO
8601 in the configured timezone. No business rule lives here — validation,
identity binding, and retention all belong to the service, so the same
guarantees hold whether a call arrives over stdio, over HTTP, or from a test.

Tool descriptions are the LLM-facing contract, not documentation. A client
with no skill installed must be able to work from these alone (TASK-091), so
each one says when to call the tool, what the arguments mean, and what comes
back. Two rules they must convey: search and recent return previews only,
with detail fetched by ID; and after actually using a memory the agent closes
the loop — reinforce when it proved right, forget when it proved wrong.

``brain_id`` is bound from process configuration and ``agent_id`` is read from
the MCP handshake. Neither is ever a tool argument, so a caller can neither
address another brain nor claim another identity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from another_brain.domain.models import AuditEvent, MemoryRecord, SearchPreview
from another_brain.services.memory_service import MemoryService

DEFAULT_AGENT_ID = "unknown-client"


def agent_id_from(ctx: Context | None) -> str:
    """Provenance declared by the host in the initialize handshake.

    Advisory only: a missing or junk ``clientInfo`` degrades attribution but
    never blocks an operation, and it can never select a brain or grant
    access — those come from bound configuration.

    The attribute is ``client_info``, not ``clientInfo``: SDK v2 exposes the
    snake_case field and keeps the wire name only as a serialization alias.
    """
    try:
        name = ctx.session.client_params.client_info.name  # type: ignore[union-attr]
    except AttributeError:
        return DEFAULT_AGENT_ID
    return (name or "").strip() or DEFAULT_AGENT_ID


def _iso(epoch_ms: int | None, tz_name: str) -> str | None:
    """Epoch milliseconds as ISO 8601 in the configured timezone."""
    if epoch_ms is None:
        return None
    moment = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return moment.astimezone(ZoneInfo(tz_name)).isoformat()


def _preview(preview: SearchPreview) -> dict[str, Any]:
    """One search/recent line. Never carries content, metadata, or a vector.

    There is no relevance score: rank is expressed by list order, so no
    storage-vendor score encoding crosses the tool boundary.
    """
    return {
        "memory_id": preview.memory_id,
        "topic": preview.topic,
        "catalog": preview.catalog,
        "summary": preview.summary,
        "timeline_day": preview.timeline_day,
        "importance": preview.importance,
        "has_content": preview.has_content,
    }


def _record_preview(record: MemoryRecord) -> dict[str, Any]:
    """The same preview shape, built from a full record (``brain_recent``)."""
    return {
        "memory_id": record.memory_id,
        "topic": record.topic,
        "catalog": record.catalog,
        "summary": record.summary,
        "timeline_day": record.timeline_day,
        "importance": record.importance,
        "has_content": bool(record.content),
    }


def _audit_entry(event: AuditEvent, tz_name: str) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "action": event.action.value,
        "memory_id": event.memory_id,
        "agent_id": event.agent_id,
        "at": _iso(event.event_at_ms, tz_name),
        "detail": event.detail,
    }


def register_tools(server: MCPServer, service: MemoryService) -> None:
    """Attach the eight stable ``brain_*`` tools to an MCP server."""
    tz = service.timezone

    @server.tool()
    def brain_remember(
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
        """Store one memory as a timeline (diary) entry. Call this when you
        learn something worth recalling later: a decision, a bug and its fix,
        a user preference, a fact, a task.

        topic: a stable, reusable lowercase-kebab subject, 3-8 tokens and at
        most 12 (e.g. "auth-token-refresh"). Reuse the same topic for the same
        subject over time — do not restate the catalog, add workflow labels,
        or stuff keywords. summary: 1-2 sentences, the diary line; topic and
        summary together are what get embedded and previewed in search.
        content: optional full detail or checklist (markdown), never
        embedded and fetched only via brain_get. catalog: open kebab-case
        class — starter set: bug, decision, preference, task, fact, note.
        scope: user | project | global. scope_id is REQUIRED for user (the
        user name) and project (the project slug); only scope=global may omit
        it (pinned to "global"). importance 1-5 sets retention: 5=365d,
        4=180d, 3=90d, 2=30d, 1=7d, and the entry expires unless
        brain_reinforce renews it.

        The store is append-only: repeating the same knowledge creates a new
        entry rather than overwriting one, so correcting a memory means
        remembering the new version and forgetting the old.
        """
        result = service.remember(
            topic=topic, summary=summary, agent_id=agent_id_from(ctx),
            scope=scope, scope_id=scope_id, catalog=catalog, content=content,
            importance=importance, metadata=metadata,
        )
        return {
            "memory_id": result.memory_id,
            "timeline_day": result.timeline_day,
            "expires_at": _iso(result.expires_at_ms, tz),
        }

    @server.tool()
    def brain_search(
        query: str,
        scope: str,
        scope_id: str = "",
        topic: str | None = None,
        catalog: str | None = None,
        timeline_day: str | None = None,
        min_importance: int | None = None,
        days: int | None = None,
    ) -> dict[str, Any]:
        """Search memories by meaning and keywords at once (semantic +
        full-text, fused). Returns preview lines in relevance order — call
        brain_get(memory_id) when a result has has_content=true and you need
        the detail.

        Text that appears only in a memory's content is findable here even
        when the meaning does not match, so exact identifiers, paths, and
        error strings are worth searching for verbatim.

        Reading changes nothing. After you actually USE a memory, close the
        loop: brain_reinforce if it proved correct and valuable,
        brain_forget if it proved wrong. scope_id is REQUIRED for
        scope=user/project (only global may omit it). Optional narrowing:
        topic, catalog, timeline_day (YYYY-MM-DD), min_importance (1-5),
        days (only entries from the last N days).
        """
        results = service.search(
            query, scope=scope, scope_id=scope_id, topic=topic, catalog=catalog,
            timeline_day=timeline_day, min_importance=min_importance, days=days,
        )
        return {"count": len(results), "results": [_preview(r) for r in results]}

    @server.tool()
    def brain_recent(
        scope: str,
        scope_id: str = "",
        limit: int = 20,
        topic: str | None = None,
        catalog: str | None = None,
        timeline_day: str | None = None,
        min_importance: int | None = None,
        days: int | None = None,
    ) -> dict[str, Any]:
        """List the newest memories on the timeline, newest first — a pure
        listing with no query. Use it to catch up on what was stored recently,
        or to walk one day (timeline_day) or one topic.

        Same preview shape as brain_search, ordered by time instead of
        relevance. scope_id is REQUIRED for scope=user/project (only global
        may omit it). limit defaults to 20, maximum 100.
        """
        records = service.recent(
            scope=scope, scope_id=scope_id, limit=limit, topic=topic,
            catalog=catalog, timeline_day=timeline_day,
            min_importance=min_importance, days=days,
        )
        return {
            "count": len(records),
            "results": [_record_preview(r) for r in records],
        }

    @server.tool()
    def brain_get(memory_id: str) -> dict[str, Any]:
        """Fetch one memory in full, including content and metadata — the
        detail pull behind a search or recent preview.

        Pure read: fetching never extends the memory's lifetime. Once you have
        used it, brain_reinforce or brain_forget as appropriate. Returns
        found=false for an unknown, forgotten, or expired id.
        """
        record = service.get(memory_id)
        if record is None:
            return {"found": False, "memory_id": memory_id}
        return {
            "found": True,
            **_record_preview(record),
            "content": record.content,
            "scope": record.scope.value,
            "scope_id": record.scope_id,
            "agent_id": record.agent_id,
            "metadata": record.metadata,
            "created_at": _iso(record.created_at_ms, tz),
            "updated_at": _iso(record.updated_at_ms, tz),
            "period_start": _iso(record.period_start_ms, tz),
            "period_end": _iso(record.period_end_ms, tz),
            "expires_at": _iso(record.expires_at_ms, tz),
        }

    @server.tool()
    def brain_reinforce(memory_id: str, ctx: Context = None) -> dict[str, Any]:
        """Renew a memory's retention after it proved correct and valuable in
        actual use. This is the ONLY way an expiry is extended: it re-arms the
        full TTL for the entry's importance.

        Do not reinforce on sight — fetch it, use it, judge it, then
        reinforce. Returns ok=false for an unknown, forgotten, or expired id.
        """
        record = service.reinforce(memory_id, agent_id=agent_id_from(ctx))
        if record is None:
            return {"ok": False, "memory_id": memory_id, "reason": "not_found"}
        return {
            "ok": True,
            "memory_id": memory_id,
            "expires_at": _iso(record.expires_at_ms, tz),
        }

    @server.tool()
    def brain_forget(memory_id: str, ctx: Context = None) -> dict[str, Any]:
        """Forget a memory that proved wrong or stale. It disappears from
        search, recent, and get immediately, then is purged after a grace
        window during which an admin can still restore it.

        Use deliberately: a harmless outdated entry can simply expire on its
        own. Returns ok=false for an unknown, already-forgotten, or expired id.
        """
        ok = service.forget(memory_id, agent_id=agent_id_from(ctx))
        return {
            "ok": ok,
            "memory_id": memory_id,
            **({} if ok else {"reason": "not_found"}),
        }

    @server.tool()
    def brain_health(ctx: Context = None) -> dict[str, Any]:
        """Service health: storage schema and embedding state, the brain this
        server writes to, and your client identity as detected from the MCP
        handshake.

        Pure read — it never loads the embedding model, so an embedding_state
        of "not_loaded" is healthy rather than a problem.
        """
        return service.health(agent_id=agent_id_from(ctx))

    @server.tool()
    def brain_audit(day: str | None = None, limit: int = 500) -> dict[str, Any]:
        """Read the memory-mutation trail for one day (admin and
        observability). Newest first.

        Each event records the action (remember, reinforce, forget, restore,
        hard_delete), the memory_id, the acting agent_id, and a timestamp —
        never the memory text, so this is safe to read without exposing
        contents. Pure read; the brain is server-bound. day is YYYY-MM-DD and
        defaults to today in the server's timezone.
        """
        events = service.audit_events(day=day, limit=limit)
        resolved = day if day is not None else service.today()
        return {
            "count": len(events),
            "day": resolved,
            "events": [_audit_entry(e, tz) for e in events],
        }
