"""TASK-068: tool contracts over an in-memory MCP client session.

Every response shape, the shared ``not_found`` shapes, actionable
actual/allowed validation errors, agent attribution from the handshake, and
the locked retrieval behaviors (content-only lexical match below the cosine
floor, punctuation-only query falling back to the vector branch) — all
through the real ``MCPServer``, so the same guarantees hold whether a call
arrives over stdio or HTTP.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from mcp.client import Client
from mcp.types import Implementation

from another_brain.mcp.tools import DEFAULT_AGENT_ID, agent_id_from

PREVIEW_KEYS = {
    "memory_id", "topic", "catalog", "summary", "timeline_day", "importance",
    "has_content",
}
GET_EXTRA_KEYS = {
    "found", "content", "agent_id", "metadata", "created_at", "updated_at",
    "period_start", "period_end", "expires_at",
}


def _payload(result) -> dict:
    """The structured content of a successful call, failing loudly otherwise."""
    assert not result.is_error, result.content[0].text
    assert result.structured_content is not None
    return result.structured_content


def _error_text(result) -> str:
    assert result.is_error, "expected an error result"
    return result.content[0].text


async def _remember(client, **overrides):
    args = {"topic": "auth-token-refresh", "summary": "Tokens rotate hourly."}
    args.update(overrides)
    result = await client.call_tool("remember", args)
    return _payload(result)


# -- response shapes ----------------------------------------------------------


async def test_remember_response_shape(mcp_server):
    async with Client(mcp_server) as client:
        payload = await _remember(client, importance=4, content="detail")
    assert set(payload) == {"memory_id", "timeline_day", "expires_at"}
    assert re.fullmatch(r"[0-9a-f]{32}", payload["memory_id"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload["timeline_day"])
    # ISO 8601 with an explicit offset in the configured timezone (UTC here).
    assert datetime.fromisoformat(payload["expires_at"]).tzinfo is not None


async def test_search_and_recent_preview_shape(mcp_server):
    async with Client(mcp_server) as client:
        with_content = await _remember(client, content="the body", metadata={"k": "v"})
        without_content = await _remember(
            client, topic="deploy-runbook", summary="Deploys ship Friday."
        )

        search = _payload(await client.call_tool("search", {"query": "tokens"}))
        assert search["count"] == len(search["results"]) == 2
        by_id = {r["memory_id"]: r for r in search["results"]}
        assert set(by_id) == {with_content["memory_id"], without_content["memory_id"]}
        for preview in search["results"]:
            # The locked preview: no content, no metadata, no relevance score.
            assert set(preview) == PREVIEW_KEYS
        assert by_id[with_content["memory_id"]]["has_content"] is True
        assert by_id[without_content["memory_id"]]["has_content"] is False

        recent = _payload(await client.call_tool("recent", {}))
        assert recent["count"] == 2
        for preview in recent["results"]:
            assert set(preview) == PREVIEW_KEYS


async def test_recent_is_newest_first(mcp_server, fake_clock):
    async with Client(mcp_server) as client:
        older = await _remember(client, topic="older-topic", summary="Older.")
        fake_clock.advance_days(1)
        newer = await _remember(client, topic="newer-topic", summary="Newer.")
        recent = _payload(await client.call_tool("recent", {}))
    order = [r["memory_id"] for r in recent["results"]]
    assert order == [newer["memory_id"], older["memory_id"]]


async def test_get_full_shape_and_not_found(mcp_server):
    async with Client(mcp_server) as client:
        remembered = await _remember(client, content="full body", metadata={"a": 1})
        got = _payload(
            await client.call_tool("get", {"memory_id": remembered["memory_id"]})
        )
        assert got["found"] is True
        assert set(got) == PREVIEW_KEYS | GET_EXTRA_KEYS
        assert got["content"] == "full body"
        assert got["metadata"] == {"a": 1}

        missing = _payload(await client.call_tool("get", {"memory_id": "nope"}))
        assert missing == {"found": False, "memory_id": "nope"}


async def test_reinforce_and_forget_shapes(mcp_server):
    async with Client(mcp_server) as client:
        remembered = await _remember(client)

        reinforced = _payload(
            await client.call_tool(
                "reinforce", {"memory_id": remembered["memory_id"]}
            )
        )
        assert set(reinforced) == {"ok", "memory_id", "expires_at"}
        assert reinforced["ok"] is True

        missing = _payload(await client.call_tool("reinforce", {"memory_id": "x"}))
        assert missing == {"ok": False, "memory_id": "x", "reason": "not_found"}

        forgotten = _payload(
            await client.call_tool("forget", {"memory_id": remembered["memory_id"]})
        )
        # Success carries no reason key; failure always says not_found.
        assert forgotten == {"ok": True, "memory_id": remembered["memory_id"]}

        missing = _payload(await client.call_tool("forget", {"memory_id": "x"}))
        assert missing == {"ok": False, "memory_id": "x", "reason": "not_found"}


async def test_health_response_shape(mcp_server):
    async with Client(mcp_server) as client:
        health = _payload(await client.call_tool("health", {}))
    assert set(health) == {
        "status", "brain_id", "agent_id", "timeline_timezone",
        "embedding_profile", "embedding_state", "embedding_dimensions", "storage",
    }
    assert health["status"] == "ok"
    assert health["embedding_profile"] == "q4"
    # Lazy model: not_loaded is healthy, and answering never loads it.
    assert health["embedding_state"] == "not_loaded"
    assert health["embedding_dimensions"] == 640
    assert set(health["storage"]) == {
        "schema_version", "schema_ok", "profile_id", "profile_matches_manifest",
        "vector_backend", "integrity_ok", "detail",
    }
    assert health["storage"]["schema_ok"] is True
    assert health["storage"]["profile_matches_manifest"] is True
    # No deep check unless asked (doctor's job, not liveness).
    assert health["storage"]["integrity_ok"] is None


async def test_audit_shape_privacy_and_default_day(mcp_server, service):
    async with Client(
        mcp_server,
        client_info=Implementation(name="contract-agent", version="1.0"),
    ) as client:
        remembered = await _remember(
            client, summary="SECRET-MARKER summary", content="SECRET-MARKER body"
        )
        await client.call_tool("forget", {"memory_id": remembered["memory_id"]})

        audit = _payload(await client.call_tool("audit", {}))
        assert audit["day"] == service.today()
        assert audit["count"] == 2
        actions = [e["action"] for e in audit["events"]]
        assert set(actions) == {"remember", "forget"}
        for event in audit["events"]:
            assert set(event) == {
                "event_id", "action", "memory_id", "agent_id", "at", "detail",
            }
            assert event["agent_id"] == "contract-agent"
        # Never memory text: the marker strings must not appear anywhere.
        assert "SECRET-MARKER" not in json.dumps(audit)
        # An explicit day is honored.
        other = _payload(await client.call_tool("audit", {"day": "1999-01-01"}))
        assert other["day"] == "1999-01-01" and other["count"] == 0


# -- identity ------------------------------------------------------------------


async def test_agent_attribution_flows_from_handshake(mcp_server):
    async with Client(
        mcp_server, client_info=Implementation(name="attrib-agent", version="2.0")
    ) as client:
        remembered = await _remember(client)
        got = _payload(
            await client.call_tool("get", {"memory_id": remembered["memory_id"]})
        )
        assert got["agent_id"] == "attrib-agent"
        health = _payload(await client.call_tool("health", {}))
        assert health["agent_id"] == "attrib-agent"


def test_agent_id_falls_back_to_unknown_client():
    # No request context (or a junk handshake) degrades attribution, never access.
    assert agent_id_from(None) == DEFAULT_AGENT_ID == "unknown-client"


# -- locked retrieval behaviors -------------------------------------------------


async def test_content_only_match_survives_below_cosine_floor(
    mcp_server, fake_embedder
):
    """The bugfix at tool level: a lexical-only content hit needs no cosine."""
    from .conftest import basis_vector

    async with Client(mcp_server) as client:
        remembered = await _remember(
            client,
            topic="payment-webhook",
            summary="Webhook retries are exponential.",
            content="Failure signature ZXQW-9871 appears in the gateway log.",
        )
        # Orthogonal query vector: cosine 0.0, far below the 0.30 floor.
        fake_embedder.set_query("ZXQW-9871", basis_vector(1))
        search = _payload(await client.call_tool("search", {"query": "ZXQW-9871"}))
    assert search["count"] == 1
    assert search["results"][0]["memory_id"] == remembered["memory_id"]
    assert search["results"][0]["has_content"] is True


async def test_punctuation_only_query_is_vector_only_and_never_an_error(
    mcp_server, fake_embedder
):
    from .conftest import basis_vector

    async with Client(mcp_server) as client:
        remembered = await _remember(client)
        # No safe FTS terms -> the lexical branch is skipped; the default
        # identical vectors still answer from the vector branch.
        search = _payload(await client.call_tool("search", {"query": "!!!???"}))
        assert search["count"] == 1
        assert search["results"][0]["memory_id"] == remembered["memory_id"]
        # Orthogonal vector -> empty, but still never an error.
        fake_embedder.set_query(";;;", basis_vector(1))
        empty = _payload(await client.call_tool("search", {"query": ";;;"}))
        assert empty == {"count": 0, "results": []}


# -- validation: actionable actual/allowed --------------------------------------


async def test_validation_errors_carry_actual_and_allowed(mcp_server):
    async with Client(mcp_server) as client:
        importance = _error_text(
            await client.call_tool(
                "remember", {"topic": "t", "summary": "s", "importance": 9}
            )
        )
        assert "5" in importance and "9" in importance  # allowed and actual

        limit = _error_text(await client.call_tool("recent", {"limit": 101}))
        assert "100" in limit and "101" in limit

        days = _error_text(await client.call_tool("recent", {"days": 0}))
        assert "1" in days

        min_importance = _error_text(
            await client.call_tool("search", {"query": "q", "min_importance": 7})
        )
        assert "5" in min_importance and "7" in min_importance

        # The service's own message surfaces for what schema types cannot say.
        empty = _error_text(await client.call_tool("search", {"query": "   "}))
        assert "empty" in empty


# -- the loop -------------------------------------------------------------------


async def test_full_loop_remember_search_get_reinforce_forget(
    mcp_server, fake_clock
):
    async with Client(
        mcp_server, client_info=Implementation(name="loop-agent", version="1.0")
    ) as client:
        remembered = await _remember(client, importance=1)
        memory_id = remembered["memory_id"]

        search = _payload(await client.call_tool("search", {"query": "tokens"}))
        assert [r["memory_id"] for r in search["results"]] == [memory_id]

        fake_clock.advance_days(3)
        reinforced = _payload(
            await client.call_tool("reinforce", {"memory_id": memory_id})
        )
        # Re-armed from the reinforce moment: later than the original expiry.
        assert reinforced["expires_at"] > remembered["expires_at"]

        fake_clock.advance_ms(1)  # audit orders same-ms events by random id
        forgotten = _payload(
            await client.call_tool("forget", {"memory_id": memory_id})
        )
        assert forgotten["ok"] is True
        got = _payload(await client.call_tool("get", {"memory_id": memory_id}))
        assert got["found"] is False
        search = _payload(await client.call_tool("search", {"query": "tokens"}))
        assert search["count"] == 0

        # Today (3 days after the write) holds the two mutations; the
        # remember event stays filed on its own diary day.
        audit = _payload(await client.call_tool("audit", {}))
        actions = [e["action"] for e in audit["events"]]
        assert actions == ["forget", "reinforce"]  # newest first
        written = _payload(
            await client.call_tool(
                "audit", {"day": remembered["timeline_day"]}
            )
        )
        assert [e["action"] for e in written["events"]] == ["remember"]
        all_events = audit["events"] + written["events"]
        assert all(e["agent_id"] == "loop-agent" for e in all_events)
