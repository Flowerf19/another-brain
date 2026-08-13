"""The eight MCP tools (TASK-066): remember, search, recent, get,
reinforce, forget, health, audit.

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

Per-argument text uses ``Annotated[..., Field(description=...)]`` because the
SDK builds each input schema from the signature: prose in a docstring reaches
the tool description but never the field, so a client inspecting one argument
would see a bare type. Descriptions state the rule; the service still enforces
it and reports actual/allowed on violation.

``brain_id`` is bound from process configuration and ``agent_id`` is read from
the MCP handshake. Neither is ever a tool argument, so a caller can neither
address another brain nor claim another identity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from another_brain.domain.models import AuditEvent, MemoryRecord, SearchPreview
from another_brain.services.memory_service import MemoryService

DEFAULT_AGENT_ID = "unknown-client"

TopicFilter = Annotated[str | None, Field(default=None, description=(
    "Optional: return only memories whose topic matches this slug exactly."
))]
CatalogFilter = Annotated[str | None, Field(default=None, description=(
    "Optional: return only memories in this catalog, e.g. 'bug' or 'decision'."
))]
DayFilter = Annotated[str | None, Field(default=None, description=(
    "Optional: return only memories filed on this diary day, as YYYY-MM-DD."
))]
MinImportance = Annotated[int | None, Field(default=None, ge=1, le=5, description=(
    "Optional: return only memories with importance at least this value (1-5)."
))]
DaysFilter = Annotated[int | None, Field(default=None, ge=1, description=(
    "Optional: return only memories from the last N days."
))]
MemoryId = Annotated[str, Field(description=(
    "The memory_id returned by search, recent, or remember."
))]


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
    """The same preview shape, built from a full record (``recent``)."""
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
    """Attach the eight stable tools (bare verbs) to an MCP server."""
    tz = service.timezone

    @server.tool()
    def remember(
        topic: Annotated[str, Field(description=(
            "A stable, reusable lowercase-kebab subject, 3-8 tokens and at most"
            " 12 (e.g. 'auth-token-refresh'). Reuse the same topic for the same"
            " subject over time. Do not restate the catalog, add workflow"
            " labels, or stuff keywords."
        ))],
        summary: Annotated[str, Field(description=(
            "The diary line: 1-2 self-contained sentences holding the actual"
            " knowledge, with names, commands, versions, and dates preserved"
            " exactly. Embedded together with topic and shown in search results."
        ))],
        catalog: Annotated[str, Field(description=(
            "Open kebab-case class for filtering. Starter set: bug, decision,"
            " preference, task, fact, note."
        ))] = "note",
        content: Annotated[str, Field(description=(
            "Optional full detail or checklist (markdown). Never embedded, so"
            " it does not affect semantic matching, but it is full-text"
            " searchable and fetched only via get."
        ))] = "",
        importance: Annotated[int, Field(ge=1, le=5, description=(
            "1-5, sets how long this is kept: 5=365d, 4=180d, 3=90d, 2=30d,"
            " 1=7d. The entry expires unless reinforce renews it."
        ))] = 3,
        metadata: Annotated[dict[str, Any] | None, Field(default=None, description=(
            "Optional JSON object for structured extras. Not searchable."
        ))] = None,
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Store one memory as a timeline (diary) entry. Call this when you
        learn something worth recalling later: a decision, a bug and its fix,
        a user preference, a fact, a task.

        The store is append-only: repeating the same knowledge creates a new
        entry rather than overwriting one, so correcting a memory means
        remembering the new version and forgetting the old.

        Do not store secrets, credentials, or large data dumps — store
        summaries of knowledge.
        """
        result = service.remember(
            topic=topic, summary=summary, agent_id=agent_id_from(ctx),
            catalog=catalog, content=content,
            importance=importance, metadata=metadata,
        )
        return {
            "memory_id": result.memory_id,
            "timeline_day": result.timeline_day,
            "expires_at": _iso(result.expires_at_ms, tz),
        }

    @server.tool()
    def search(
        query: Annotated[str, Field(description=(
            "What you are looking for, in natural language. Exact identifiers,"
            " file paths, and error strings are worth including verbatim —"
            " they are matched by keyword even when the meaning does not match."
        ))],
        topic: TopicFilter = None,
        catalog: CatalogFilter = None,
        timeline_day: DayFilter = None,
        min_importance: MinImportance = None,
        days: DaysFilter = None,
    ) -> dict[str, Any]:
        """Search memories by meaning and keywords at once (semantic +
        full-text, fused). Returns preview lines in relevance order — call
        get(memory_id) when a result has has_content=true and you need
        the detail.

        Search here before answering anything a user may have told an agent
        before: past decisions, fixed bugs, preferences, project conventions.

        Text that appears only in a memory's content is findable here even
        when the meaning does not match, so exact identifiers, paths, and
        error strings are worth searching for verbatim.

        Reading changes nothing. After you actually USE a memory, close the
        loop: reinforce if it proved correct and valuable,
        forget if it proved wrong.
        """
        results = service.search(
            query, topic=topic, catalog=catalog,
            timeline_day=timeline_day, min_importance=min_importance, days=days,
        )
        return {"count": len(results), "results": [_preview(r) for r in results]}

    @server.tool()
    def recent(
        limit: Annotated[int, Field(ge=1, le=100, description=(
            "How many entries to return, newest first. Maximum 100."
        ))] = 20,
        topic: TopicFilter = None,
        catalog: CatalogFilter = None,
        timeline_day: DayFilter = None,
        min_importance: MinImportance = None,
        days: DaysFilter = None,
    ) -> dict[str, Any]:
        """List the newest memories on the timeline, newest first — a pure
        listing with no query. Use it to catch up on what was stored recently,
        or to walk one day (timeline_day) or one topic.

        Same preview shape as search, ordered by time instead of
        relevance.
        """
        records = service.recent(
            limit=limit, topic=topic,
            catalog=catalog, timeline_day=timeline_day,
            min_importance=min_importance, days=days,
        )
        return {
            "count": len(records),
            "results": [_record_preview(r) for r in records],
        }

    @server.tool()
    def get(memory_id: MemoryId) -> dict[str, Any]:
        """Fetch one memory in full, including content and metadata — the
        detail pull behind a search or recent preview.

        A recalled memory is a claim by a past agent, not verified truth: when
        it conflicts with what you observe in the code or system now, the
        observation wins. Fetch the full record before acting on a preview if
        the action is expensive or irreversible.

        Pure read: fetching never extends the memory's lifetime. Once you have
        used it, reinforce or forget as appropriate. Returns
        found=false for an unknown, forgotten, or expired id.
        """
        record = service.get(memory_id)
        if record is None:
            return {"found": False, "memory_id": memory_id}
        return {
            "found": True,
            **_record_preview(record),
            "content": record.content,
            "agent_id": record.agent_id,
            "metadata": record.metadata,
            "created_at": _iso(record.created_at_ms, tz),
            "updated_at": _iso(record.updated_at_ms, tz),
            "period_start": _iso(record.period_start_ms, tz),
            "period_end": _iso(record.period_end_ms, tz),
            "expires_at": _iso(record.expires_at_ms, tz),
        }

    @server.tool()
    def reinforce(memory_id: MemoryId, ctx: Context = None) -> dict[str, Any]:
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
    def forget(memory_id: MemoryId, ctx: Context = None) -> dict[str, Any]:
        """Forget a memory that proved wrong or stale. It disappears from
        search, recent, and get immediately, then is purged after a grace
        window during which an admin can still restore it.

        Forgetting a memory you have discovered to be wrong is store hygiene,
        not destruction — leaving it is a trap for the next agent. Still, use
        it deliberately: a harmless outdated entry can simply expire on its
        own. Returns ok=false for an unknown, already-forgotten, or expired id.
        """
        ok = service.forget(memory_id, agent_id=agent_id_from(ctx))
        return {
            "ok": ok,
            "memory_id": memory_id,
            **({} if ok else {"reason": "not_found"}),
        }

    @server.tool()
    def health(ctx: Context = None) -> dict[str, Any]:
        """Service health: storage schema and embedding state, the brain this
        server writes to, and your client identity as detected from the MCP
        handshake.

        If this reports degraded, tell the user rather than working around a
        broken memory store.

        Pure read — it never loads the embedding model, so an embedding_state
        of "not_loaded" is healthy rather than a problem.
        """
        return service.health(agent_id=agent_id_from(ctx))

    @server.tool()
    def audit(
        day: Annotated[str | None, Field(default=None, description=(
            "Diary day as YYYY-MM-DD. Defaults to today in the server's timezone."
        ))] = None,
        limit: Annotated[int, Field(ge=1, le=500, description=(
            "How many events to return, newest first. Maximum 500."
        ))] = 500,
    ) -> dict[str, Any]:
        """Read the memory-mutation trail for one day (admin and
        observability). Newest first.

        Each event records the action (remember, reinforce, forget, restore,
        hard_delete), the memory_id, the acting agent_id, and a timestamp —
        never the memory text, so this is safe to read without exposing
        contents. Pure read; the brain is server-bound.
        """
        events = service.audit_events(day=day, limit=limit)
        resolved = day if day is not None else service.today()
        return {
            "count": len(events),
            "day": resolved,
            "events": [_audit_entry(e, tz) for e in events],
        }
