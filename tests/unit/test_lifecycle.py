"""TASK-052: lifecycle mutations — reinforce/soft_delete/restore/hard_delete
transactional by (bound brain, memory_id), with live/grace semantics."""
from __future__ import annotations

import numpy as np
import pytest

from another_brain.domain.models import EmbeddingVector, MemoryRecord
from another_brain.protocols import MutationOutcome, Scope, ScopeKey
from another_brain.services.sql.repository import SQLiteMemoryRepository
from another_brain.services.sql.ttl import GRACE_MS, expires_at_ms_for, ttl_ms_for

EMBED = EmbeddingVector(values=np.zeros(640, dtype=np.float32))
USER1 = ScopeKey(Scope.USER, "user-1")
DAY = 86_400_000


class _Clock:
    def __init__(self, start: int) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def _store(repository, clock, memory_id, **overrides) -> None:
    record = MemoryRecord(
        memory_id=memory_id,
        brain_id=repository._brain_id,
        agent_id="agent-a",
        scope=Scope.USER,
        scope_id="user-1",
        topic="t",
        catalog="c",
        summary="s",
        content="",
        timeline_day="2026-08-04",
        period_start_ms=None,
        period_end_ms=None,
        created_at_ms=clock(),
        updated_at_ms=clock(),
        importance=overrides.pop("importance", 3),
        expires_at_ms=overrides.pop("expires_at_ms", expires_at_ms_for(3, clock())),
        deleted_at_ms=overrides.pop("deleted_at_ms", None),
        metadata={},
        profile_id="q4",
        record_version=1,
        embedding=EMBED,
    )
    repository.store(record)


def _expiry(repository, memory_id) -> tuple[int, object]:
    with repository._factory.connect() as con:
        row = con.connection.execute(
            "SELECT expires_at_ms, deleted_at_ms FROM memories WHERE memory_id=?",
            (memory_id,),
        ).fetchone()
    return row[0], row[1]


class TestReinforce:
    def test_rearms_from_importance(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1", importance=1, expires_at_ms=2_000)
        assert repository.reinforce("m1") is MutationOutcome.APPLIED
        expiry, _ = _expiry(repository, "m1")
        assert expiry == 1_000 + ttl_ms_for(1)  # 7 days from now, not 1s

    @pytest.mark.parametrize("scenario", ["unknown", "expired", "deleted"])
    def test_not_found(self, sql_factory, scenario):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        if scenario == "unknown":
            memory_id = "nope"
        elif scenario == "expired":
            memory_id = "m1"
            _store(repository, clock, "m1", expires_at_ms=999)
        else:
            memory_id = "m1"
            _store(repository, clock, "m1")
            repository.soft_delete("m1")
        assert repository.reinforce(memory_id) is MutationOutcome.NOT_FOUND

    def test_cross_brain_invisible(self, sql_factory):
        clock = _Clock(1_000)
        owner = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(owner, clock, "m1")
        other = SQLiteMemoryRepository(sql_factory, brain_id="other", clock=clock)
        assert other.reinforce("m1") is MutationOutcome.NOT_FOUND


class TestSoftDelete:
    def test_clamps_to_grace_when_current_is_longer(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")  # expires now + 90d > grace
        assert repository.soft_delete("m1") is MutationOutcome.APPLIED
        expiry, deleted = _expiry(repository, "m1")
        assert deleted == 1_000
        assert expiry == 1_000 + GRACE_MS  # clamped to now + 30 days

    def test_never_extends_a_shorter_expiry(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        short = 1_000 + 5 * DAY  # expires in 5 days < grace
        _store(repository, clock, "m1", expires_at_ms=short)
        repository.soft_delete("m1")
        expiry, _ = _expiry(repository, "m1")
        assert expiry == short  # unchanged — forget never extends life

    def test_row_invisible_after_delete(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")
        repository.soft_delete("m1")
        assert repository.get("m1") is None
        assert repository.recent(USER1, limit=10) == []

    @pytest.mark.parametrize("scenario", ["unknown", "expired", "already-deleted"])
    def test_not_found(self, sql_factory, scenario):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        if scenario == "unknown":
            memory_id = "nope"
        elif scenario == "expired":
            memory_id = "m1"
            _store(repository, clock, "m1", expires_at_ms=999)
        else:
            memory_id = "m1"
            _store(repository, clock, "m1")
            repository.soft_delete("m1")
        assert repository.soft_delete(memory_id) is MutationOutcome.NOT_FOUND


class TestRestore:
    def test_within_grace_rearms_from_importance(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1", importance=5)
        repository.soft_delete("m1")
        assert repository.restore("m1") is MutationOutcome.APPLIED
        expiry, deleted = _expiry(repository, "m1")
        assert deleted is None
        assert expiry == 1_000 + ttl_ms_for(5)  # re-armed 365 days from now

    def test_past_grace_not_found(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")
        repository.soft_delete("m1")
        clock.advance(GRACE_MS + 1)  # 30 days + 1ms later
        assert repository.restore("m1") is MutationOutcome.NOT_FOUND

    def test_live_row_not_found(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")
        assert repository.restore("m1") is MutationOutcome.NOT_FOUND

    def test_unknown_and_cross_brain_not_found(self, sql_factory):
        clock = _Clock(1_000)
        owner = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(owner, clock, "m1")
        owner.soft_delete("m1")
        other = SQLiteMemoryRepository(sql_factory, brain_id="other", clock=clock)
        assert other.restore("m1") is MutationOutcome.NOT_FOUND
        assert other.restore("nope") is MutationOutcome.NOT_FOUND


class TestHardDelete:
    def test_removes_live_row_and_fts(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")
        assert repository.hard_delete("m1") is MutationOutcome.APPLIED
        assert repository.get("m1") is None
        with sql_factory.connect() as con:
            fts = con.connection.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
            mem = con.connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert fts == mem == 0

    def test_removes_deleted_row(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")
        repository.soft_delete("m1")
        assert repository.hard_delete("m1") is MutationOutcome.APPLIED

    def test_unknown_and_cross_brain_not_found(self, sql_factory):
        clock = _Clock(1_000)
        owner = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(owner, clock, "m1")
        other = SQLiteMemoryRepository(sql_factory, brain_id="other", clock=clock)
        assert other.hard_delete("m1") is MutationOutcome.NOT_FOUND
        assert other.hard_delete("nope") is MutationOutcome.NOT_FOUND
        assert owner.get("m1") is not None  # untouched
