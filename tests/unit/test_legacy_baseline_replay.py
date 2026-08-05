"""TASK-068 (closing TASK-065's deferred half): replay the legacy oracle
export against the clean ``MemoryService``.

Every scenario in ``tests/fixtures/legacy-baseline/behavior-v1.json`` was
captured from ``main@edc0e57`` (Redis 8.8 + FT.HYBRID, fake 8-dim vectors).
Each replay asserts the locked *clean* equivalent, not bug compatibility.
Two scenarios pass by differing from the oracle, as TASK-008 records:

- ``recent-ordering``: legacy sorts period_start DESC with index-order ties;
  the clean contract is ``created_at DESC, memory_id ASC``.
- ``legacy-cosine-gate-drops-content-match``: the legacy universal cosine
  gate returned ``[]``; the clean branch returns the content match — the bug
  this rebuild exists to fix.

The oracle's 3600 s grace window is Redis-stack-specific; the clean contract
locks the 30-day grace clamp (master plan, lifecycle decision 10).
"""
from __future__ import annotations

import json
from pathlib import Path

from mcp.client import Client

from .conftest import basis_vector

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "legacy-baseline" / "behavior-v1.json")
    .read_text(encoding="utf-8")
)
SCENARIOS = {s["id"]: s for s in FIXTURE["scenarios"]}
DAY_MS = 86_400_000
AGENT = "replay-agent"

PREVIEW_KEYS = {
    "memory_id", "topic", "catalog", "summary", "timeline_day", "importance",
    "has_content",
}


def _remember(service, *, importance: int = 3, topic: str = "replay-topic",
              summary: str = "Replay summary.", content: str = "") -> str:
    return service.remember(
        topic=topic, summary=summary, agent_id=AGENT,
        content=content, importance=importance,
    ).memory_id


def test_identity_binding(make_service):
    oracle = make_service("oracle-brain")
    other = make_service("other-brain")
    m1 = _remember(oracle)

    # Every by-ID op from another brain is the shared not_found shape.
    assert other.get(m1) is None
    assert other.reinforce(m1, agent_id=AGENT) is None
    assert other.forget(m1, agent_id=AGENT) is False
    assert other.restore(m1, agent_id=AGENT) is None
    assert other.hard_delete(m1, agent_id=AGENT) is False
    # ...while the owning brain sees the row, exactly as the oracle records.
    assert oracle.get(m1) is not None


def test_append_only_writes(service):
    first = _remember(service, summary="Identical line.")
    second = _remember(service, summary="Identical line.")
    assert first != second  # never an overwrite
    ids = {r.memory_id for r in service.recent(limit=10)}
    assert {first, second} <= ids


def test_ttl_importance(service, fake_clock):
    expected = SCENARIOS["ttl-importance"]["legacy"]["ttl_days_by_importance"]
    base = fake_clock()
    for importance in (5, 4, 3, 2, 1):
        result = service.remember(
            topic=f"ttl-{importance}", summary="TTL probe.",
            agent_id=AGENT, importance=importance,
        )
        days = expected[str(importance)]
        assert result.expires_at_ms - base == int(days * DAY_MS)


def test_expired_excluded(service, fake_clock):
    memory_id = _remember(service, importance=1)  # 7 days
    fake_clock.advance_days(7)
    fake_clock.advance_ms(1)  # expires_at <= now is gone
    assert service.get(memory_id) is None
    assert memory_id not in {r.memory_id for r in service.recent(limit=10)}


def test_reinforce_rearms(service, fake_clock):
    memory_id = _remember(service, importance=1)  # 7 days
    fake_clock.advance_days(3)
    record = service.reinforce(memory_id, agent_id=AGENT)
    assert record is not None
    # Re-armed the full importance TTL from the reinforce moment.
    assert record.expires_at_ms == fake_clock() + 7 * DAY_MS


def test_soft_delete_restore_grace(service, fake_clock, sql_factory):
    memory_id = _remember(service, importance=5)  # 365 days
    original_expiry = fake_clock() + 365 * DAY_MS

    assert service.forget(memory_id, agent_id=AGENT) is True
    assert service.get(memory_id) is None
    assert memory_id not in {r.memory_id for r in service.recent(limit=10)}

    # The clamp is the locked 30-day grace, and it never extends life.
    with sql_factory.connect() as con:
        deleted_at, expires_at = con.connection.execute(
            "SELECT deleted_at_ms, expires_at_ms FROM memories WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
    assert deleted_at == fake_clock()
    assert expires_at == min(original_expiry, fake_clock() + 30 * DAY_MS)

    restored = service.restore(memory_id, agent_id=AGENT)
    assert restored is not None
    assert restored.expires_at_ms == fake_clock() + 365 * DAY_MS  # re-armed
    assert service.get(memory_id) is not None

    assert service.forget(memory_id, agent_id=AGENT) is True
    assert service.hard_delete(memory_id, agent_id=AGENT) is True
    assert service.restore(memory_id, agent_id=AGENT) is None
    with sql_factory.connect() as con:
        row = con.connection.execute(
            "SELECT 1 FROM memories WHERE memory_id = ?", (memory_id,)
        ).fetchone()
    assert row is None


def test_recent_ordering_uses_the_locked_tie_break(service, fake_clock):
    """Intentional difference: created_at DESC, memory_id ASC — not the
    oracle's period_start DESC with index-order ties."""
    first = _remember(service, topic="tie-a", summary="Tied.")
    second = _remember(service, topic="tie-b", summary="Tied.")  # same clock ms
    fake_clock.advance_ms(1)
    newest = _remember(service, topic="tie-c", summary="Newer.")

    order = [r.memory_id for r in service.recent(limit=10)]
    assert order == [newest, *sorted([first, second])]
    # Deterministic across repeated reads.
    assert [r.memory_id for r in service.recent(limit=10)] == order


def test_audit_privacy_and_survival(service, fake_clock):
    target = _remember(service, summary="ORACLE-MARKER summary",
                       content="ORACLE-MARKER body")
    service.reinforce(target, agent_id=AGENT)
    service.forget(target, agent_id=AGENT)
    service.restore(target, agent_id=AGENT)
    service.hard_delete(target, agent_id=AGENT)

    events = service.audit_events(day=service.today())
    actions = {e.action.value for e in events}
    assert {"remember", "reinforce", "forget", "restore", "hard_delete"} <= actions
    # Structural facts only: no memory text in any event detail.
    for event in events:
        assert "ORACLE-MARKER" not in json.dumps(event.detail)
        assert not {"topic", "summary", "content", "metadata"} & set(event.detail)
    # Audit survives the hard delete of its memory (no memory FK, by design).
    assert service.get(target) is None
    assert any(e.memory_id == target for e in events)


async def test_mcp_preview_shapes(mcp_server):
    """Previews omit content/metadata and carry no score; get returns detail.

    The oracle's search preview had ``relevance_score``/``score_source``; the
    clean surface drops them deliberately — rank is list order, so no
    storage-vendor score encoding crosses the tool boundary (TASK-066).
    """
    legacy = SCENARIOS["mcp-preview-shapes"]["legacy"]
    async with Client(mcp_server) as client:
        remembered = await client.call_tool(
            "brain_remember",
            {"topic": "preview-topic", "summary": "Preview probe.",
             "content": "the body", "metadata": {"k": "v"}},
        )
        memory_id = remembered.structured_content["memory_id"]

        search = (
            await client.call_tool("brain_search", {"query": "preview"})
        ).structured_content
        preview = search["results"][0]
        assert set(preview) == PREVIEW_KEYS
        assert "content" not in preview and "metadata" not in preview
        # Legacy-only score keys are gone, as recorded.
        assert "relevance_score" not in preview
        assert "score_source" not in preview
        assert legacy["previews_never_carry_content"] is True

        recent = (
            await client.call_tool("brain_recent", {})
        ).structured_content
        assert set(recent["results"][0]) == PREVIEW_KEYS

        got = (
            await client.call_tool("brain_get", {"memory_id": memory_id})
        ).structured_content
        assert got["content"] == "the body"
        assert got["metadata"] == {"k": "v"}
        assert "expires_at" in got


def test_health_shape_not_loaded_is_healthy(service):
    health = service.health(agent_id=AGENT)
    assert health["status"] == "ok"
    # The oracle's not_loaded_is_healthy: a lazy model is not degradation.
    assert health["embedding_state"] == "not_loaded"
    assert health["embedding_profile"] == "q4"
    assert health["embedding_dimensions"] == 640
    assert health["brain_id"] == "test-brain"
    assert health["agent_id"] == AGENT


def test_content_match_survives_the_removed_cosine_gate(service, fake_embedder):
    """FIXED, intentionally not bug-compatible: the oracle returned [] here."""
    scenario = SCENARIOS["legacy-cosine-gate-drops-content-match"]
    assert scenario["legacy"]["result_ids"] == []  # the recorded bug

    memory_id = _remember(
        service,
        topic="gateway-incident",
        summary="The gateway 502s under burst load.",
        content="Root cause marker ZXQW-9871 in the upstream log.",
    )
    # Doc vector (default e1) is orthogonal to the query vector: cosine 0.0.
    fake_embedder.set_query("ZXQW-9871", basis_vector(1))
    results = service.search("ZXQW-9871")
    assert [r.memory_id for r in results] == [memory_id]


def test_punctuation_query_uses_vector_only_branch(service, fake_embedder):
    """No safe FTS terms: vector-only retrieval, never an error."""
    memory_id = _remember(service)
    results = service.search("!!!???")
    # Identical default vectors pass the floor, so the row is found.
    assert memory_id in {r.memory_id for r in results}
