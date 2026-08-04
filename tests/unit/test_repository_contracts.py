"""TASK-054: repository contracts — reopen/restart, temporary file release,
boundary equality, malformed rows, rollback, busy-retry classification."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from another_brain.domain.models import EmbeddingVector, MemoryRecord
from another_brain.errors import BusyExhausted, StorageError
from another_brain.protocols import MutationOutcome, Scope, ScopeKey
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.repository import SQLiteMemoryRepository, _row_to_record
from another_brain.services.sql.retry import busy_retry
from another_brain.services.sql.ttl import GRACE_MS, purge_expired

EMBED = EmbeddingVector(values=np.zeros(640, dtype=np.float32))
USER1 = ScopeKey(Scope.USER, "user-1")
DAY = 86_400_000

_MEMORY_COLUMNS = (
    "memory_id", "brain_id", "agent_id", "scope", "scope_id", "topic",
    "catalog", "summary", "content", "timeline_day", "period_start_ms",
    "period_end_ms", "created_at_ms", "updated_at_ms", "importance",
    "expires_at_ms", "deleted_at_ms", "metadata", "profile_id",
    "embedding", "record_version",
)


class _Clock:
    def __init__(self, start: int) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now


def _store(repository, clock, memory_id, **overrides) -> None:
    repository.store(MemoryRecord(
        memory_id=memory_id,
        brain_id=repository._brain_id,
        agent_id="a",
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
        expires_at_ms=overrides.pop("expires_at_ms", 10**15),
        deleted_at_ms=overrides.pop("deleted_at_ms", None),
        metadata={},
        profile_id="q4",
        record_version=1,
        embedding=EMBED,
    ))


class TestReopenAndRestart:
    def test_data_survives_full_restart(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")
        # simulate restart: brand-new factory on the same file
        reopened = SQLiteMemoryRepository(
            SQLiteConnectionFactory(sql_factory.db_path), brain_id="default", clock=clock
        )
        assert reopened.get("m1") is not None
        assert reopened.recent(USER1, limit=5)[0].memory_id == "m1"

    def test_temporary_wal_files_released_after_last_close(self, sql_factory):
        path = sql_factory.db_path
        for suffix in (f"{path.name}-wal", f"{path.name}-shm"):
            candidate = path.parent / suffix
            assert not candidate.exists(), f"{suffix} leaked after close"
        with sql_factory.connect() as con:
            con.connection.execute("CREATE TABLE probe(x)")
            con.connection.commit()
            assert (path.parent / f"{path.name}-wal").exists()  # live WAL while open
        assert not (path.parent / f"{path.name}-wal").exists()  # checkpointed on close
        assert not (path.parent / f"{path.name}-shm").exists()

    def test_database_file_movable_after_close(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")
        moved = sql_factory.db_path.parent / "moved.sqlite3"
        sql_factory.db_path.rename(moved)  # no open handles -> movable
        reopened = SQLiteMemoryRepository(
            SQLiteConnectionFactory(moved), brain_id="default", clock=clock
        )
        assert reopened.get("m1") is not None


class TestBoundaryEquality:
    def test_expiry_equality_is_not_live(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1", expires_at_ms=1_000)  # expires exactly now
        assert repository.get("m1") is None
        assert repository.reinforce("m1") is MutationOutcome.NOT_FOUND

    def test_purge_includes_expiry_equality(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1", expires_at_ms=1_000)
        assert purge_expired(sql_factory, clock=clock) == 1  # <= now: purged

    def test_restore_grace_equality_is_not_found(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")
        repository.soft_delete("m1")
        with sql_factory.connect() as con:
            con.connection.execute(
                "UPDATE memories SET deleted_at_ms=? WHERE memory_id='m1'",
                (1_000 - GRACE_MS,),  # deleted exactly at the grace cutoff
            )
            con.connection.commit()
        assert repository.restore("m1") is MutationOutcome.NOT_FOUND


class TestMalformedRows:
    def _row(self, **overrides) -> tuple:
        values = dict(zip(_MEMORY_COLUMNS, (
            "m1", "default", "a", "user", "u1", "t", "c", "s", "",
            "2026-08-04", None, None, 1, 1, 3, 10**15, None,
            "{}", "q4", b"\x00" * 2560, 1,
        )))
        values.update(overrides)
        return tuple(values[col] for col in _MEMORY_COLUMNS)

    def test_corrupt_metadata_json_raises_storage_error(self):
        with pytest.raises(StorageError, match="metadata"):
            _row_to_record(self._row(metadata="not-json"))

    def test_wrong_blob_length_raises_storage_error(self):
        with pytest.raises(StorageError, match="embedding"):
            _row_to_record(self._row(embedding=b"\x00" * 100))

    def test_missing_blob_raises_storage_error(self):
        with pytest.raises(StorageError, match="embedding"):
            _row_to_record(self._row(embedding=None))

    def test_valid_row_roundtrips(self):
        record = _row_to_record(self._row())
        assert record.memory_id == "m1"
        assert record.embedding is not None
        assert np.array_equal(record.embedding.values, EMBED.values)


class TestRollback:
    def test_duplicate_store_leaves_first_row_intact(self, sql_factory):
        clock = _Clock(1_000)
        repository = SQLiteMemoryRepository(sql_factory, brain_id="default", clock=clock)
        _store(repository, clock, "m1")
        with pytest.raises(Exception):
            _store(repository, clock, "m1")
        with sql_factory.connect() as con:
            count = con.connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            fts = con.connection.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
        assert count == 1 and fts == 1  # the failed tx wrote nothing

    def test_raw_transaction_rolls_back_mid_error(self, sql_factory):
        with sql_factory.connect() as con:
            raw = con.connection
            raw.execute("BEGIN IMMEDIATE")
            raw.execute(
                "INSERT INTO audit_events(event_id, brain_id, memory_id, agent_id,"
                " action, event_at_ms, timeline_day, detail_json)"
                " VALUES ('e1', 'b', 'm', 'a', 'remember', 1, '2026-08-04', '{}')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(
                    "INSERT INTO audit_events(event_id, brain_id, memory_id,"
                    " agent_id, action, event_at_ms, timeline_day, detail_json)"
                    " VALUES ('e1', 'b', 'm', 'a', 'remember', 1, '2026-08-04', '{}')"
                )
            raw.rollback()
        with sql_factory.connect() as con:
            count = con.connection.execute(
                "SELECT COUNT(*) FROM audit_events"
            ).fetchone()[0]
        assert count == 0  # rollback undid the first insert too


class TestBusyRetryClassification:
    def test_retries_locked_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        assert busy_retry(flaky, attempts=5, base_s=0.001) == "ok"
        assert calls["n"] == 3

    def test_exhausts_envelope_with_typed_error(self):
        def always_locked():
            raise sqlite3.OperationalError("database table is locked")

        with pytest.raises(BusyExhausted, match="busy after 3 attempts"):
            busy_retry(always_locked, attempts=3, base_s=0.001)

    def test_non_busy_error_raises_immediately(self):
        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise sqlite3.OperationalError("no such table: memories")

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            busy_retry(broken, attempts=5, base_s=0.001)
        assert calls["n"] == 1  # not retried — classification, not blindness


class TestCloseAndFileRelease:
    def test_factory_reconnects_after_close(self, sql_factory):
        con = sql_factory.connect()
        con.close()
        with sql_factory.connect() as again:
            assert again.connection.execute("SELECT 1").fetchone()[0] == 1
