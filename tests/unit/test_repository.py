"""TASK-050: repository — append-only store, by-ID get, deterministic recent,
strict JSON metadata, atomic row+FTS, live-filter before limit."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from another_brain.domain.models import EmbeddingVector, MemoryRecord, RecentFilters
from another_brain.errors import DuplicateMemoryError, ValidationError
from another_brain.protocols import Scope, ScopeKey
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.repository import SQLiteMemoryRepository

EMBED = EmbeddingVector(values=np.zeros(640, dtype=np.float32))


@pytest.fixture
def repo(sql_factory):
    return sql_factory, SQLiteMemoryRepository(sql_factory, brain_id="default")


def _record(**overrides) -> MemoryRecord:
    base = dict(
        memory_id="mem-1",
        brain_id="default",
        agent_id="agent-a",
        scope=Scope.USER,
        scope_id="user-1",
        topic="sqlite-benchmark",
        catalog="engineering",
        summary="notes",
        content="",
        timeline_day="2026-08-04",
        period_start_ms=None,
        period_end_ms=None,
        created_at_ms=1000,
        updated_at_ms=1000,
        importance=3,
        expires_at_ms=10**15,
        deleted_at_ms=None,
        metadata={},
        profile_id="q4",
        record_version=1,
        embedding=EMBED,
    )
    base.update(overrides)
    return MemoryRecord(**base)


class TestStore:
    def test_row_and_fts_commit_atomically(self, repo):
        _, repository = repo
        repository.store(_record())
        with repo[0].connect() as con:
            raw = con.connection
            assert raw.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
            assert raw.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0] == 1
            assert raw.execute(
                "SELECT summary FROM memory_fts WHERE rowid=1"
            ).fetchone()[0] == "notes"

    def test_duplicate_is_typed_integrity_error(self, repo):
        _, repository = repo
        repository.store(_record())
        with pytest.raises(DuplicateMemoryError, match="already exists"):
            repository.store(_record())

    def test_wrong_brain_rejected(self, repo):
        _, repository = repo
        with pytest.raises(ValidationError, match="bound brain"):
            repository.store(_record(brain_id="other"))

    def test_missing_embedding_rejected(self, repo):
        _, repository = repo
        with pytest.raises(ValidationError, match="embedding"):
            repository.store(_record(embedding=None))

    def test_non_serializable_metadata_rejected(self, repo):
        _, repository = repo
        with pytest.raises(ValidationError, match="JSON-serializable"):
            repository.store(_record(metadata={"when": object()}))

    def test_metadata_stored_as_strict_json(self, repo):
        _, repository = repo
        repository.store(_record(metadata={"b": 1, "a": [1, 2]}))
        with repo[0].connect() as con:
            stored = con.connection.execute(
                "SELECT metadata FROM memories"
            ).fetchone()[0]
        assert stored == '{"a":[1,2],"b":1}'  # canonical: sorted keys, compact


class TestGet:
    def test_roundtrip_includes_embedding(self, repo):
        _, repository = repo
        repository.store(_record())
        record = repository.get("mem-1")
        assert record is not None
        assert record.memory_id == "mem-1"
        assert record.embedding is not None
        assert np.array_equal(record.embedding.values, EMBED.values)

    def test_unknown_id_returns_none(self, repo):
        _, repository = repo
        assert repository.get("nope") is None

    def test_expired_row_invisible(self, repo):
        _, repository = repo
        repository.store(_record(expires_at_ms=999))  # before clock 1000
        assert repository.get("mem-1") is None

    def test_deleted_row_invisible(self, repo):
        _, repository = repo
        repository.store(_record(deleted_at_ms=500))
        assert repository.get("mem-1") is None

    def test_cross_brain_invisible(self, repo):
        factory, repository = repo
        repository.store(_record())
        other = SQLiteMemoryRepository(factory, brain_id="other")
        assert other.get("mem-1") is None


class TestRecent:
    USER1 = ScopeKey(Scope.USER, "user-1")

    def test_order_created_desc_then_memory_id_asc(self, repo):
        _, repository = repo
        repository.store(_record(memory_id="old", created_at_ms=100))
        repository.store(_record(memory_id="a-new", created_at_ms=200))
        repository.store(_record(memory_id="b-new", created_at_ms=200))  # tie-break
        ids = [r.memory_id for r in repository.recent(self.USER1, limit=10)]
        assert ids == ["a-new", "b-new", "old"]

    def test_live_filter_before_limit(self, repo):
        _, repository = repo
        repository.store(_record(memory_id="expired", created_at_ms=300, expires_at_ms=999))
        repository.store(_record(memory_id="live", created_at_ms=100))
        ids = [r.memory_id for r in repository.recent(self.USER1, limit=1)]
        assert ids == ["live"]  # expired is newest but excluded before LIMIT 1

    def test_scope_isolation(self, repo):
        _, repository = repo
        repository.store(_record(scope=Scope.PROJECT, scope_id="p-1"))
        assert repository.recent(self.USER1, limit=10) == []

    def test_global_scope_id_pinned(self, repo):
        _, repository = repo
        repository.store(_record(scope=Scope.GLOBAL, scope_id="global"))
        ids = [r.memory_id for r in repository.recent(ScopeKey(Scope.GLOBAL, "global"), limit=10)]
        assert ids == ["mem-1"]

    def test_filters_topic_catalog_window(self, repo):
        _, repository = repo
        repository.store(_record(memory_id="m1", topic="sqlite", created_at_ms=100))
        repository.store(_record(memory_id="m2", topic="rust", created_at_ms=200))
        repository.store(_record(memory_id="m3", topic="sqlite", catalog="research", created_at_ms=300))
        assert [r.memory_id for r in repository.recent(
            self.USER1, limit=10, filters=RecentFilters(topic="sqlite"))] == ["m3", "m1"]
        assert [r.memory_id for r in repository.recent(
            self.USER1, limit=10, filters=RecentFilters(catalog="research"))] == ["m3"]
        assert [r.memory_id for r in repository.recent(
            self.USER1, limit=10, filters=RecentFilters(since_ms=150, until_ms=250))] == ["m2"]

    def test_limit_validation(self, repo):
        _, repository = repo
        with pytest.raises(ValidationError, match="limit"):
            repository.recent(self.USER1, limit=0)

    def test_reads_use_read_only_connections(self, repo, monkeypatch):
        factory, repository = repo
        calls = []
        original = factory.connect
        monkeypatch.setattr(factory, "connect", lambda **kw: calls.append(kw) or original(**kw))
        repository.get("mem-1")
        repository.recent(self.USER1, limit=5)
        assert calls == [{"read_only": True}, {"read_only": True}]

    def test_writes_use_read_write_connection(self, repo, monkeypatch):
        factory, repository = repo
        calls = []
        original = factory.connect
        monkeypatch.setattr(factory, "connect", lambda **kw: calls.append(kw) or original(**kw))
        repository.store(_record())
        assert calls == [{}]
