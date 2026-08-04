from __future__ import annotations

import pytest

from another_brain.errors import ValidationError


NOW = 1_800_000_000_000


@pytest.mark.asyncio
async def test_complete_service_lifecycle_and_audit(memory_service):
    remembered = await memory_service.remember(
        "service-lifecycle", "Complete service lifecycle.", agent_id="writer",
        scope="project", scope_id="project", content="SERVICE-LIFECYCLE",
    )
    memory_id = remembered.memory_id
    assert (await memory_service.get(memory_id)).content == "SERVICE-LIFECYCLE"
    assert len(await memory_service.recent(scope="project", scope_id="project")) == 1
    assert len(await memory_service.search("SERVICE-LIFECYCLE", scope="project", scope_id="project")) == 1
    assert await memory_service.reinforce(memory_id, agent_id="reader")
    assert await memory_service.forget(memory_id, agent_id="reader")
    assert await memory_service.get(memory_id) is None
    assert await memory_service.restore(memory_id, agent_id="admin")
    assert await memory_service.hard_delete(memory_id, agent_id="admin")
    assert await memory_service.get(memory_id) is None
    actions = [event["action"] for event in await memory_service.audit()]
    assert len(actions) == 5
    assert set(actions) == {"hard_delete", "restore", "forget", "reinforce", "remember"}


@pytest.mark.asyncio
async def test_remember_delegates_token_validation_and_embedding(memory_service, fake_embedder):
    result = await memory_service.remember(
        "validated-topic", "Validated summary.", agent_id="agent", scope="global",
        content="validated content", metadata={"source": "test"}, now_ms=NOW,
    )
    assert result.memory_id
    assert fake_embedder.validated_topics == ["validated-topic"]
    assert fake_embedder.validated_content == ["validated content"]
    assert fake_embedder.documents == [("validated-topic", "Validated summary.")]


@pytest.mark.asyncio
async def test_recent_filters_and_limits(memory_service):
    await memory_service.remember(
        "recent-decision", "Recent decision.", agent_id="agent", scope="global",
        catalog="decision", importance=4, now_ms=NOW,
    )
    assert len(await memory_service.recent(
        scope="global", topic="recent-decision", catalog="decision",
        min_importance=4, days=1, limit=1, now_ms=NOW,
    )) == 1
    assert await memory_service.recent(scope="global", min_importance=5, now_ms=NOW) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", None])
async def test_search_rejects_empty_query(memory_service, query):
    with pytest.raises(ValidationError, match="non-empty"):
        await memory_service.search(query, scope="global")


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, -1, True])
async def test_search_rejects_invalid_days(memory_service, days):
    with pytest.raises(ValidationError, match="positive integer"):
        await memory_service.search("query", scope="global", days=days)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 101, True])
async def test_recent_rejects_invalid_limit(memory_service, limit):
    with pytest.raises(ValidationError, match="between 1 and 100"):
        await memory_service.recent(scope="global", limit=limit)


@pytest.mark.xfail(strict=True, reason="recent does not validate days yet")
@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, -1, True])
async def test_recent_rejects_invalid_days(memory_service, days):
    with pytest.raises(ValidationError, match="positive integer"):
        await memory_service.recent(scope="global", days=days)


@pytest.mark.asyncio
async def test_missing_memory_mutations_are_idempotent(memory_service):
    assert await memory_service.get("missing") is None
    assert await memory_service.reinforce("missing", agent_id="agent") is None
    assert not await memory_service.forget("missing", agent_id="agent")
    assert await memory_service.restore("missing", agent_id="admin") is None
    assert not await memory_service.hard_delete("missing", agent_id="admin")


@pytest.mark.asyncio
async def test_health_is_secret_free_and_reports_embedding(memory_service):
    health = await memory_service.health(agent_id="health-client")
    assert health["status"] == "ok"
    assert health["brain_id"] == "test-brain"
    assert health["agent_id"] == "health-client"
    assert health["embedding_model"] == "fake-harrier"
    assert health["embedding_ready"] is True
    assert "content" not in repr(health)


@pytest.mark.xfail(strict=True, reason="audit limit/day arguments are not validated yet")
@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 501, True])
async def test_audit_rejects_invalid_limit(memory_service, limit):
    with pytest.raises(ValidationError):
        await memory_service.audit(limit=limit)
