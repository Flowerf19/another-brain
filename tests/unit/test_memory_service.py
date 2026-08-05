"""TASK-068: MemoryService contracts over temp SQLite with Protocol-seam fakes."""
from __future__ import annotations

import math
from dataclasses import fields

import pytest

from another_brain.domain.models import AuditAction, MemoryRecord, SearchPreview
from another_brain.errors import ValidationError
from another_brain.protocols import EmbeddingHealth

from .conftest import PROFILE_SQL, basis_vector

# Locked retention policy (config.TTL_DAYS_BY_IMPORTANCE): 5→365, 4→180,
# 3→90, 2→30, 1→7 days; forget grace 30 days.
TTL_DAYS = {5: 365, 4: 180, 3: 90, 2: 30, 1: 7}
DAY_MS = 86_400_000
GRACE_DAYS = 30


class TestRemember:
    def test_returns_identity_timeline_and_locked_ttl(self, service, fake_clock):
        result = service.remember(
            topic="protractor",
            summary="a drawing instrument",
            agent_id="agent-a",
            importance=3,
        )
        assert len(result.memory_id) == 32
        assert all(c in "0123456789abcdef" for c in result.memory_id)
        assert result.timeline_day == "2025-07-11"  # BASE_MS in UTC
        assert result.expires_at_ms == fake_clock() + 90 * DAY_MS

    @pytest.mark.parametrize(
        ("importance", "expected_days"),
        [(5, 365), (4, 180), (3, 90), (2, 30), (1, 7)],
    )
    def test_all_importance_levels_map_to_locked_ttl(
        self, service, fake_clock, importance, expected_days
    ):
        result = service.remember(
            topic=f"topic-{importance}",
            summary=f"summary-{importance}",
            agent_id="agent-a",
            importance=importance,
        )
        assert result.expires_at_ms == fake_clock() + expected_days * DAY_MS

    def test_default_importance_is_three(self, service, fake_clock):
        result = service.remember(topic="t", summary="s", agent_id="agent-a")
        assert result.expires_at_ms == fake_clock() + 90 * DAY_MS

    def test_persisted_row_matches_result(self, service, fake_clock):
        result = service.remember(
            topic="t", summary="s", agent_id="agent-a",
            catalog="work", content="body", importance=4,
            metadata={"key": "value"},
        )
        record = service.get(result.memory_id)
        assert record is not None
        assert record.memory_id == result.memory_id
        assert record.expires_at_ms == result.expires_at_ms
        assert record.timeline_day == result.timeline_day
        assert record.catalog == "work"
        assert record.content == "body"
        assert record.importance == 4
        assert record.metadata == {"key": "value"}
        assert record.created_at_ms == fake_clock()


class TestAppendOnly:
    def test_identical_topic_and_summary_create_distinct_ids(
        self, service, fake_clock
    ):
        first = service.remember(topic="same", summary="same", agent_id="a")
        second = service.remember(topic="same", summary="same", agent_id="a")
        assert first.memory_id != second.memory_id
        # Same clock tick: identical created_at, deterministic tie-break by id.
        assert [r.memory_id for r in service.recent(limit=10)] == sorted(
            (first.memory_id, second.memory_id)
        )


class TestBrainBoundary:
    def test_brain_isolation(self, make_service, fake_clock):
        brain_a = make_service(brain_id="brain-a")
        brain_b = make_service(brain_id="brain-b")
        result = brain_a.remember(topic="t", summary="s", agent_id="agent-a")
        assert result.memory_id
        assert brain_b.recent() == []
        assert brain_b.search("t") == []
        assert brain_b.get(result.memory_id) is None

    def test_cross_brain_mutations_are_noops_and_never_audited(
        self, make_service, fake_clock
    ):
        brain_a = make_service(brain_id="brain-a")
        brain_b = make_service(brain_id="brain-b")
        result = brain_a.remember(
            topic="t", summary="s", agent_id="agent-a", importance=5
        )
        memory_id = result.memory_id

        assert brain_b.get(memory_id) is None
        assert brain_b.reinforce(memory_id, agent_id="agent-b") is None
        assert brain_b.forget(memory_id, agent_id="agent-b") is False
        assert brain_b.restore(memory_id, agent_id="agent-b") is None
        assert brain_b.hard_delete(memory_id, agent_id="agent-b") is False

        # The owner's row is untouched: still gettable and still live.
        owner = brain_a.get(memory_id)
        assert owner is not None
        assert owner.deleted_at_ms is None

        # Mutations that never applied must not leave audit traces.
        events = brain_b.audit_events(day="2025-07-11")
        assert events == []
        # Only brain A's remember event exists.
        events = brain_a.audit_events(day="2025-07-11")
        assert [e.action for e in events] == [AuditAction.REMEMBER]


class TestExpiry:
    def test_expired_row_is_invisible_everywhere(self, service, fake_clock):
        result = service.remember(
            topic="t", summary="s", agent_id="agent-a", importance=1
        )
        fake_clock.advance_days(8)  # past the 7-day TTL
        assert service.get(result.memory_id) is None
        assert service.recent() == []
        assert service.search("t") == []
        assert service.reinforce(result.memory_id, agent_id="a") is None
        assert service.forget(result.memory_id, agent_id="a") is False


class TestGraceLifecycle:
    def test_forget_restore_within_grace_rearms_expiry(self, service, fake_clock):
        result = service.remember(
            topic="t", summary="s", agent_id="agent-a", importance=5
        )
        assert service.forget(result.memory_id, agent_id="a") is True
        # Immediately after forget: gone from live views.
        assert service.get(result.memory_id) is None
        assert service.recent() == []
        assert service.search("t") == []
        # Restore within the 30-day grace re-arms the full TTL from NOW.
        restored = service.restore(result.memory_id, agent_id="a")
        assert restored is not None
        assert restored.deleted_at_ms is None
        assert restored.expires_at_ms == fake_clock() + 365 * DAY_MS
        assert service.get(result.memory_id) is not None

    def test_past_grace_restore_and_hard_delete_are_noops(self, service, fake_clock):
        # IMPORTANT — ACTUAL LOCKED SEMANTICS, VERIFIED AGAINST THE
        # REPOSITORY (repository.py:202-221): restore only addresses a
        # soft-deleted row still inside its 30-day grace window
        # (deleted_at_ms > now - GRACE_MS). An expired AND past-grace row is
        # NOT deleted by hard_delete here — hard_delete's DELETE keyed on
        # (brain_id, memory_id) still finds it (repository.py:215-231) and
        # returns APPLIED, so the brief's "-> False" does NOT hold for an
        # expired+deleted row. The row was only soft-deleted, never purged.
        result = service.remember(
            topic="t", summary="s", agent_id="agent-a", importance=5
        )
        assert service.forget(result.memory_id, agent_id="a") is True
        fake_clock.advance_days(31)  # past the 30-day grace
        assert service.restore(result.memory_id, agent_id="a") is None
        # hard_delete still addresses the soft-deleted row by key: APPLIED.
        assert service.hard_delete(result.memory_id, agent_id="a") is True

    def test_live_row_hard_delete_is_permanent_but_audit_survives(
        self, service, fake_clock
    ):
        result = service.remember(
            topic="t", summary="s", agent_id="agent-a", importance=3
        )
        assert service.hard_delete(result.memory_id, agent_id="a") is True
        assert service.get(result.memory_id) is None
        assert service.restore(result.memory_id, agent_id="a") is None
        assert service.recent() == []
        # Audit has no memory FK: both events survive the hard delete.
        events = service.audit_events(day="2025-07-11")
        # Same clock tick: event_id ASC decides order (random uuids), so
        # assert the set, not a concrete order.
        assert {e.action for e in events} == {
            AuditAction.REMEMBER,
            AuditAction.HARD_DELETE,
        }
        assert [e.event_id for e in events] == sorted(e.event_id for e in events)


class TestReinforce:
    def test_reinforce_rearms_expiry_from_importance(self, service, fake_clock):
        result = service.remember(
            topic="t", summary="s", agent_id="agent-a", importance=1
        )
        fake_clock.advance_days(3)  # 4 days of TTL left
        reinforced = service.reinforce(result.memory_id, agent_id="a")
        assert reinforced is not None
        assert reinforced.expires_at_ms == fake_clock() + 7 * DAY_MS
        # The reinforce is a structural mutation: audited. The reinforce
        # happened 3 days later than the remember, so the two events land on
        # different timeline days (2025-07-14 vs 2025-07-11).
        events = service.audit_events()  # defaults to today (2025-07-14)
        assert [e.action for e in events] == [AuditAction.REINFORCE]
        assert [e.action for e in service.audit_events(day="2025-07-11")] == [
            AuditAction.REMEMBER
        ]


class TestAudit:
    def test_audit_privacy_shape_and_order(self, service, fake_clock):
        topic, summary, content, metadata = (
            "protocol-axon", "zero-trust signing", "payload body", {"k": "v"},
        )
        result = service.remember(
            topic=topic, summary=summary, agent_id="agent-a",
            content=content, metadata=metadata, importance=3,
        )
        service.reinforce(result.memory_id, agent_id="a")
        service.forget(result.memory_id, agent_id="a")

        events = service.audit_events(day="2025-07-11")
        # All three mutations share one clock tick: event_at DESC is a tie,
        # so the deterministic tie-break event_id ASC decides the order
        # (audit.py list_day: ORDER BY event_at_ms DESC, event_id ASC).
        # event_ids are random uuids, so assert the RULE, not an order.
        assert {e.action for e in events} == {
            AuditAction.FORGET,
            AuditAction.REMEMBER,
            AuditAction.REINFORCE,
        }
        assert len({e.event_at_ms for e in events}) == 1
        assert [e.event_id for e in events] == sorted(e.event_id for e in events)

        # No memory text anywhere — detail keys, values, and reprs.
        for event in events:
            assert not any(
                key in ("topic", "summary", "content", "metadata")
                for key in event.detail
            )
            for value in event.detail.values():
                assert topic not in str(value)
                assert summary not in str(value)
                assert content not in str(value)
                assert str(metadata) not in str(value)
            assert topic not in repr(event)
            assert summary not in repr(event)
            assert content not in repr(event)
            assert str(metadata) not in repr(event)
            assert topic not in repr(event.detail)
            assert summary not in repr(event.detail)
            assert content not in repr(event.detail)
            assert str(metadata) not in repr(event.detail)

        # The event object itself carries no memory-text fields.
        assert all(
            field.name not in ("topic", "summary", "content", "metadata")
            for field in fields(event)
        )

    def test_same_clock_ms_events_order_by_event_id_asc(self, service, fake_clock):
        # All three mutations land on the same clock tick: event_at DESC is a
        # tie, so the deterministic tie-break event_id ASC decides.
        result = service.remember(topic="t", summary="s", agent_id="a")
        service.reinforce(result.memory_id, agent_id="a")
        service.forget(result.memory_id, agent_id="a")
        events = service.audit_events(day="2025-07-11")
        assert len(events) == 3
        # All in the same ms.
        assert len({e.event_at_ms for e in events}) == 1
        # event_id ASC within the tie.
        assert [e.event_id for e in events] == sorted(e.event_id for e in events)


class TestHealth:
    def test_baseline_health(self, service):
        health = service.health(agent_id="tester")
        assert health["status"] == "ok"
        assert health["brain_id"] == "test-brain"
        assert health["agent_id"] == "tester"
        assert health["timeline_timezone"] == "UTC"
        assert health["embedding_profile"] == "q4"
        assert health["embedding_state"] == "not_loaded"  # never forces a load
        assert health["embedding_dimensions"] == 640
        assert health["storage"]["schema_ok"] is True
        assert health["storage"]["profile_matches_manifest"] is True
        assert health["storage"]["integrity_ok"] is None  # shallow by default

    def test_deep_health_runs_integrity_check(self, service):
        health = service.health(agent_id="tester", deep=True)
        assert health["status"] == "ok"
        assert health["storage"]["integrity_ok"] is True

    def test_mixed_profiles_are_degraded(self, service, sql_factory):
        # A second profile row means no single profile can be claimed: the
        # probe reads the same as "no single profile" and fails the manifest
        # match (health.py:_stored_profile). Insert with a different
        # profile_id; copy the conftest PROFILE_SQL shape.
        with sql_factory.connect() as con:
            con.connection.execute(
                PROFILE_SQL.replace("'q4'", "'legacy-x'"),
                ("b" * 64, "b" * 64, "b" * 64),
            )
            con.connection.commit()
        health = service.health(agent_id="tester")
        assert health["status"] == "degraded"
        assert health["storage"]["profile_matches_manifest"] is False

    def test_embedder_error_is_degraded(self, service, fake_embedder):
        fake_embedder.health = lambda: EmbeddingHealth.ERROR  # type: ignore[method-assign]
        health = service.health(agent_id="tester")
        assert health["status"] == "degraded"
        assert health["embedding_state"] == "error"


class TestValidation:
    def test_recent_limit_bounds(self, service):
        with pytest.raises(ValidationError):
            service.recent(limit=0)
        with pytest.raises(ValidationError):
            service.recent(limit=101)

    def test_audit_limit_bounds(self, service):
        with pytest.raises(ValidationError):
            service.audit_events(limit=0)
        with pytest.raises(ValidationError):
            service.audit_events(limit=501)

    def test_blank_query_rejected(self, service):
        with pytest.raises(ValidationError):
            service.search("   ")

    def test_zero_days_rejected(self, service):
        with pytest.raises(ValidationError):
            service.recent(days=0)

    def test_nan_metadata_rejected(self, service):
        with pytest.raises(ValidationError):
            service.remember(
                topic="t", summary="s", agent_id="a", metadata={"x": float("nan")},
            )

    def test_non_object_metadata_rejected(self, service):
        # A non-mapping metadata is an actionable ValidationError, not a raw
        # TypeError from dict() — the server owns validation.
        with pytest.raises(ValidationError, match="JSON object"):
            service.remember(topic="t", summary="s", agent_id="a", metadata=[1, 2])


class TestDaysFilter:
    def test_days_window_filters_by_created_at(self, service, fake_clock):
        older = service.remember(
            topic="first", summary="one", agent_id="a", importance=1
        )
        fake_clock.advance_days(3)
        newer = service.remember(
            topic="second", summary="two", agent_id="a", importance=1
        )
        # TTL is 7 days: both are still live, but days=1 keeps only the newer.
        assert [r.memory_id for r in service.recent(days=1)] == [newer.memory_id]
        # Both live with no window.
        assert len(service.recent()) == 2
        assert older.memory_id != newer.memory_id


class TestSearch:
    def test_lexical_only_candidate_with_zero_cosine_is_returned(
        self, service, fake_embedder
    ):
        # The query vector is orthogonal to every stored document (cosine
        # 0.0 < floor 0.3), but the needle term appears in the memory's
        # content. The lexical branch matches it; there is NO post-fusion
        # cosine gate (the locked content-match fix — retrieval/service.py),
        # so the memory must still be returned.
        fake_embedder.set_query("needle", basis_vector(1))
        remembered = service.remember(
            topic="plain topic", summary="plain summary",
            agent_id="a", content="needle is hidden in this body",
        )
        previews = service.search("needle")
        assert [p.memory_id for p in previews] == [remembered.memory_id]
        assert previews[0].has_content is True
        # SearchPreviews never carry content.
        assert not hasattr(previews[0], "content")

    def test_preview_never_carries_content_and_has_content_reflects_row(
        self, service, fake_embedder
    ):
        # Query vector orthogonal to every stored doc (cosine 0.0 < floor):
        # the vector branch is empty, so both rows must be reached through
        # the lexical branch — the query term sits in both topics.
        fake_embedder.set_query("shared", basis_vector(1))
        with_body = service.remember(
            topic="shared-1", summary="s1", agent_id="a", content="body",
        )
        without_body = service.remember(topic="shared-2", summary="s2", agent_id="a")
        previews = service.search("shared")
        by_id = {p.memory_id: p for p in previews}
        assert set(by_id) == {with_body.memory_id, without_body.memory_id}
        assert by_id[with_body.memory_id].has_content is True
        assert by_id[without_body.memory_id].has_content is False
        for preview in previews:
            assert not hasattr(preview, "content")
            assert all(f.name != "content" for f in fields(preview))
