"""TASK-051: durable TTL — importance-derived expiry, bounded purge,
reads never renew."""
from __future__ import annotations

import numpy as np
import pytest

from another_brain.domain.models import EmbeddingVector, MemoryRecord
from another_brain.errors import ValidationError
from another_brain.protocols import Scope, ScopeKey
from another_brain.services.sql.repository import SQLiteMemoryRepository
from another_brain.services.sql.ttl import (
    DEFAULT_PURGE_BATCH,
    expires_at_ms_for,
    purge_expired,
    ttl_ms_for,
)

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


def _record(clock: _Clock, **overrides) -> MemoryRecord:
    base = dict(
        memory_id="mem-1",
        brain_id="default",
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
        importance=3,
        expires_at_ms=expires_at_ms_for(3, clock()),
        deleted_at_ms=None,
        metadata={},
        profile_id="q4",
        record_version=1,
        embedding=EMBED,
    )
    base.update(overrides)
    return MemoryRecord(**base)


class TestTtlComputation:
    @pytest.mark.parametrize(
        "importance,days",
        [(5, 365), (4, 180), (3, 90), (2, 30), (1, 7)],
    )
    def test_locked_table(self, importance, days):
        assert ttl_ms_for(importance) == days * DAY

    @pytest.mark.parametrize("importance", [0, 6, -1])
    def test_invalid_importance_rejected(self, importance):
        with pytest.raises(ValidationError, match="1..5"):
            ttl_ms_for(importance)

    def test_expires_at_is_now_plus_ttl(self):
        assert expires_at_ms_for(3, 1_000) == 1_000 + 90 * DAY


class TestPurge:
    def test_purges_expired_and_past_grace_keeps_live_and_recent_deleted(
        self, sql_factory
    ):
        clock = _Clock(31 * DAY)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        repository.store(_record(clock, memory_id="live"))
        repository.store(_record(
            clock, memory_id="expired", expires_at_ms=999))
        repository.store(_record(
            clock, memory_id="grace-past", deleted_at_ms=clock() - 31 * DAY - 1,
            expires_at_ms=10**15))
        repository.store(_record(
            clock, memory_id="grace-open", deleted_at_ms=clock() - 1, expires_at_ms=10**15))

        removed = purge_expired(sql_factory, clock=clock)
        assert removed == 2  # expired + grace-past
        assert [r.memory_id for r in repository.recent(USER1, limit=10)] == ["live"]
        with sql_factory.connect() as con:
            remaining = {
                r[0]: r[1]
                for r in con.connection.execute(
                    "SELECT memory_id, deleted_at_ms FROM memories"
                )
            }
        # grace-open survives purge (still inside grace) but stays invisible to live reads
        assert set(remaining) == {"live", "grace-open"}
        assert remaining["grace-open"] is not None

    def test_purge_is_bounded(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        for i in range(100):
            repository.store(_record(
                clock, memory_id=f"exp-{i:03d}", created_at_ms=i, expires_at_ms=999))
        removed = purge_expired(sql_factory, clock=clock, max_rows=10)
        assert removed == 10
        with sql_factory.connect() as con:
            remaining = con.connection.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0]
            fts = con.connection.execute(
                "SELECT COUNT(*) FROM memory_fts"
            ).fetchone()[0]
        assert remaining == 90
        assert fts == remaining  # FTS cascaded through the delete trigger

    def test_empty_purge_returns_zero(self, sql_factory):
        clock = _Clock(1_000)
        assert purge_expired(sql_factory, clock=clock) == 0

    def test_invalid_batch_rejected(self, sql_factory):
        with pytest.raises(ValidationError, match="max_rows"):
            purge_expired(sql_factory, clock=_Clock(0), max_rows=0)

    def test_default_batch_is_locked(self):
        assert DEFAULT_PURGE_BATCH == 500


class TestNeverRenewOnRead:
    def test_get_and_recent_do_not_touch_expires_at(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        repository.store(_record(clock, memory_id="m1"))
        original_expiry = 10**15
        with sql_factory.connect() as con:
            con.connection.execute(
                "UPDATE memories SET expires_at_ms=? WHERE memory_id='m1'",
                (original_expiry,),
            )
            con.connection.commit()
        clock.advance(DAY * 10)  # reads happen much later
        assert repository.get("m1") is not None
        assert repository.recent(USER1, limit=5)
        with sql_factory.connect() as con:
            stored = con.connection.execute(
                "SELECT expires_at_ms FROM memories WHERE memory_id='m1'"
            ).fetchone()[0]
        assert stored == original_expiry  # reads never renewed it
