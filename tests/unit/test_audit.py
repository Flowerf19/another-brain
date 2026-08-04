"""TASK-053: audit persistence — forbidden-text rejection, 90-day retention
cleanup, deterministic day reads, best-effort failure isolation."""
from __future__ import annotations

import pytest

from another_brain.config import AUDIT_RETENTION_DAYS
from another_brain.domain.models import AuditAction, AuditEvent
from another_brain.errors import ValidationError
from another_brain.services.sql.audit import SQLiteAuditRepository

DAY = 86_400_000


class _Clock:
    def __init__(self, start: int) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def _event(**overrides) -> AuditEvent:
    base = dict(
        event_id="evt-1",
        brain_id="default",
        memory_id="mem-1",
        agent_id="agent-a",
        action=AuditAction.REMEMBER,
        event_at_ms=1_000,
        timeline_day="2026-08-04",
        detail={"expires_at_ms": 1_234},
    )
    base.update(overrides)
    return AuditEvent(**base)


@pytest.fixture
def audit(sql_factory):
    return SQLiteAuditRepository(sql_factory, brain_id="default")


class TestRecordAndList:
    def test_roundtrip_all_fields(self, sql_factory, audit):
        audit.record(_event(detail={"a": 1, "b": [1, 2]}))
        events = audit.list_day("2026-08-04")
        assert len(events) == 1
        event = events[0]
        assert event.event_id == "evt-1"
        assert event.action is AuditAction.REMEMBER
        assert event.detail == {"a": 1, "b": [1, 2]}

    def test_deterministic_order_event_at_desc_then_id_asc(self, audit):
        audit.record(_event(event_id="b", event_at_ms=200))
        audit.record(_event(event_id="a", event_at_ms=100))
        audit.record(_event(event_id="c", event_at_ms=200))  # tie with "b"
        ids = [e.event_id for e in audit.list_day("2026-08-04")]
        assert ids == ["b", "c", "a"]

    def test_brain_isolation(self, sql_factory, audit):
        audit.record(_event())
        other = SQLiteAuditRepository(sql_factory, brain_id="other")
        assert other.list_day("2026-08-04") == []

    def test_day_isolation(self, audit):
        audit.record(_event())
        audit.record(_event(event_id="evt-2", event_at_ms=2_000, timeline_day="2026-08-05"))
        assert [e.event_id for e in audit.list_day("2026-08-04")] == ["evt-1"]


class TestValidation:
    @pytest.mark.parametrize(
        "detail",
        [{"topic": "x"}, {"nested": {"summary": "y"}}, {"list": [{"content": "z"}]}, {"metadata": 1}],
    )
    def test_forbidden_text_rejected_recursively(self, audit, detail):
        with pytest.raises(ValidationError, match="memory text"):
            audit.record(_event(detail=detail))

    def test_wrong_brain_rejected(self, audit):
        with pytest.raises(ValidationError, match="bound brain"):
            audit.record(_event(brain_id="other"))

    def test_non_serializable_detail_rejected(self, audit):
        with pytest.raises(ValidationError, match="JSON-serializable"):
            audit.record(_event(detail={"x": object()}))

    def test_duplicate_event_id_is_integrity_error(self, audit):
        audit.record(_event())
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            audit.record(_event())


class TestRetention:
    def test_purges_events_older_than_90_days(self, sql_factory, audit):
        clock = _Clock(10**15)
        audit = SQLiteAuditRepository(sql_factory, brain_id="default", clock=clock)
        audit.record(_event(event_id="old", event_at_ms=1))
        audit.record(_event(event_id="recent", event_at_ms=clock()))
        removed = audit.purge_retained()
        assert removed == 1
        assert [e.event_id for e in audit.list_day("2026-08-04")] == ["recent"]

    def test_retention_is_fixed_90_days(self, sql_factory):
        clock = _Clock(10**15)
        audit = SQLiteAuditRepository(sql_factory, brain_id="default", clock=clock)
        boundary = clock() - AUDIT_RETENTION_DAYS * DAY
        audit.record(_event(event_id="edge", event_at_ms=boundary))
        assert audit.purge_retained() == 0  # exactly at cutoff stays
        audit.record(_event(event_id="past", event_at_ms=boundary - 1))
        assert audit.purge_retained() == 1

    def test_bounded_batch(self, sql_factory):
        clock = _Clock(10**15)
        audit = SQLiteAuditRepository(sql_factory, brain_id="default", clock=clock)
        for i in range(10):
            audit.record(_event(event_id=f"e{i:02d}", event_at_ms=1))
        assert audit.purge_retained(max_rows=3) == 3
        assert audit.purge_retained(max_rows=3) == 3
        assert audit.purge_retained(max_rows=3) == 3
        assert audit.purge_retained(max_rows=3) == 1

    def test_invalid_batch_rejected(self, audit):
        with pytest.raises(ValidationError, match="max_rows"):
            audit.purge_retained(max_rows=0)


class TestFailureIsolation:
    def test_audit_failure_does_not_roll_back_memories(self, sql_factory):
        from another_brain.services.sql.repository import SQLiteMemoryRepository
        from tests.unit.test_lifecycle import EMBED
        from another_brain.domain.models import MemoryRecord
        from another_brain.protocols import Scope

        repository = SQLiteMemoryRepository(sql_factory, brain_id="default")
        record = MemoryRecord(
            memory_id="m1", brain_id="default", agent_id="a", scope=Scope.USER,
            scope_id="u1", topic="t", catalog="c", summary="s", content="",
            timeline_day="2026-08-04", period_start_ms=None, period_end_ms=None,
            created_at_ms=1, updated_at_ms=1, importance=3,
            expires_at_ms=10**15, deleted_at_ms=None, metadata={},
            profile_id="q4", record_version=1, embedding=EMBED,
        )
        repository.store(record)  # mutation committed first
        audit = SQLiteAuditRepository(sql_factory, brain_id="default")
        audit.record(_event())
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            audit.record(_event())  # audit fails (duplicate)
        assert repository.get("m1") is not None  # memory mutation stands
