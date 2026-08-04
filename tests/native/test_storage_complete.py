from __future__ import annotations

import math
import sqlite3
import threading
from contextlib import AbstractContextManager

import pytest

from another_brain.domain.models import MemoryRecord, SearchFilters
from another_brain.errors import ConfigError, StorageBusyError, ValidationError
from another_brain.storage.connection import SQLiteConnectionFactory
from another_brain.storage.repository import SQLiteRepository, safe_fts_query
from another_brain.storage.schema import SCHEMA_VERSION


NOW = 1_800_000_000_000


def vector(axis: int = 0):
    values = [0.0] * 640
    values[axis] = 1.0
    return tuple(values)


def record(*, now_ms=NOW, **overrides):
    values = {
        "brain_id": "brain",
        "agent_id": "pytest",
        "scope": "project",
        "scope_id": "another-brain",
        "topic": "native-storage",
        "summary": "SQLite storage works.",
        "content": "",
        "timezone": "Asia/Ho_Chi_Minh",
        "now_ms": now_ms,
    }
    values.update(overrides)
    return MemoryRecord.new(**values)


def test_bootstrap_sets_required_pragmas_and_health(repository):
    health = repository.health()
    assert health["schema_version"] == SCHEMA_VERSION
    assert health["integrity"] == "ok"
    assert health["fts5"] is True
    assert health["vector_backend"] == "numpy-exact"
    with repository.factory.connect() as db:
        assert db.execute("PRAGMA page_size").fetchone()[0] == 16_384
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_bootstrap_rejects_wrong_page_size(tmp_path):
    path = tmp_path / "wrong-page.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA page_size=4096")
    db.execute("CREATE TABLE marker(value INTEGER)")
    db.commit()
    db.close()
    with pytest.raises(ConfigError, match="page_size"):
        SQLiteConnectionFactory(path).bootstrap()


def test_bootstrap_rejects_newer_schema(tmp_path):
    path = tmp_path / "future.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA page_size=16384")
    db.execute("CREATE TABLE marker(value INTEGER)")
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    db.commit()
    db.close()
    with pytest.raises(ConfigError, match="newer than supported"):
        SQLiteConnectionFactory(path).bootstrap()


def test_bootstrap_rejects_schema_checksum_tampering(repository):
    with repository.factory.connect() as db:
        db.execute("UPDATE schema_migrations SET checksum='tampered'")
    with pytest.raises(ConfigError, match="checksum mismatch"):
        repository.factory.bootstrap()


@pytest.mark.parametrize("text,expected", [
    ("", None),
    ("!!!", None),
    ('alpha " OR * beta', '"alpha" OR "or" OR "beta"'),
    ("same same SAME", '"same"'),
    ("bộ nhớ", '"bộ" OR "nhớ"'),
])
def test_safe_fts_query_never_exposes_match_syntax(text, expected):
    assert safe_fts_query(text) == expected


def test_fts_updates_and_deletes_follow_source_table(repository):
    item = record(content="marker DELETE-ME")
    repository.store(item, vector())
    filters = SearchFilters.create("project", "another-brain")
    assert repository.lexical_candidates("brain", filters, "DELETE-ME", now_ms=NOW, limit=5)
    assert repository.hard_delete("brain", item.memory_id)
    assert repository.lexical_candidates("brain", filters, "DELETE-ME", now_ms=NOW, limit=5) == []


def test_expired_and_deleted_records_are_filtered_before_recent(repository):
    live = record(now_ms=NOW, topic="live-memory")
    expired = record(now_ms=NOW - 100 * 86_400_000, topic="expired-memory")
    deleted = record(now_ms=NOW + 1, topic="deleted-memory")
    for item, axis in ((live, 0), (expired, 1), (deleted, 2)):
        repository.store(item, vector(axis))
    repository.soft_delete("brain", deleted.memory_id, now_ms=NOW + 2, grace_ms=1_000)
    results = repository.recent(
        "brain", SearchFilters.create("project", "another-brain"), 10, now_ms=NOW + 3
    )
    assert [item.memory_id for item in results] == [live.memory_id]


def test_recent_applies_topic_catalog_importance_day_and_since_filters(repository):
    item = record(topic="filtered-topic", catalog="decision", importance=4)
    repository.store(item, vector())
    filters = SearchFilters.create(
        "project",
        "another-brain",
        topic="filtered-topic",
        catalog="decision",
        timeline_day=item.timeline_day,
        min_importance=4,
        since_ms=NOW,
    )
    assert [x.memory_id for x in repository.recent("brain", filters, 5, now_ms=NOW)] == [item.memory_id]
    too_important = SearchFilters.create("project", "another-brain", min_importance=5)
    assert repository.recent("brain", too_important, 5, now_ms=NOW) == []


def test_soft_delete_never_extends_expiry_and_restore_rearms_ttl(repository):
    item = record(importance=1)
    repository.store(item, vector())
    original = item.expires_at_ms
    assert repository.soft_delete("brain", item.memory_id, now_ms=NOW + 1, grace_ms=30 * 86_400_000)
    assert repository.expire_at("brain", item.memory_id) == original
    restored = repository.restore("brain", item.memory_id, now_ms=NOW + 2)
    assert restored is not None
    assert restored.expires_at_ms > original


def test_restore_rejects_deleted_record_after_grace(repository):
    item = record()
    repository.store(item, vector())
    repository.soft_delete("brain", item.memory_id, now_ms=NOW + 1, grace_ms=10)
    assert repository.restore("brain", item.memory_id, now_ms=NOW + 12) is None


def test_audit_is_secret_free_scoped_and_retained(repository):
    old = NOW - 100 * 86_400_000
    repository.record_audit(
        brain_id="brain", memory_id="old", agent_id="agent", action="remember", event_at_ms=old
    )
    repository.record_audit(
        brain_id="brain", memory_id="new", agent_id="agent", action="forget", event_at_ms=NOW,
        detail={"scope": "project"},
    )
    old_day = record(now_ms=old).timeline_day
    current_day = record(now_ms=NOW).timeline_day
    assert repository.list_audit("brain", old_day, limit=10) == []
    events = repository.list_audit("brain", current_day, limit=10)
    assert [event["action"] for event in events] == ["forget"]
    assert events[0]["detail"] == {"scope": "project"}
    with pytest.raises(ValidationError, match="forbidden"):
        repository.record_audit(
            brain_id="brain", memory_id="bad", agent_id="agent", action="remember",
            event_at_ms=NOW, detail={"content": "secret"},
        )


def test_short_concurrent_writers_all_commit(tmp_path):
    repository = SQLiteRepository(tmp_path / "concurrent.sqlite3", timezone="Asia/Ho_Chi_Minh")
    barrier = threading.Barrier(6)
    errors = []

    def writer(index):
        try:
            barrier.wait()
            repository.store(record(now_ms=NOW + index, topic=f"writer-{index}"), vector(index))
        except Exception as exc:  # pragma: no cover - assertion reports exact failures
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors
    assert repository.health()["memory_count"] == 6


@pytest.mark.xfail(strict=True, reason="repository pack_vector does not reject NaN/Inf yet")
def test_repository_rejects_non_finite_embedding(repository):
    values = [0.0] * 640
    values[0] = math.nan
    with pytest.raises(ValidationError, match="finite"):
        repository.store(record(), values)


class FailingContext(AbstractContextManager):
    def __enter__(self):
        raise sqlite3.OperationalError("database is locked")

    def __exit__(self, *args):
        return False


@pytest.mark.xfail(strict=True, reason="busy OperationalError is not mapped/retried as StorageBusyError")
def test_repository_maps_exhausted_busy_to_public_error(repository, monkeypatch):
    monkeypatch.setattr(repository.factory, "connect", lambda: FailingContext())
    with pytest.raises(StorageBusyError):
        repository.store(record(), vector())
