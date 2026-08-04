import time

import pytest

from another_brain.domain.models import MemoryRecord, SearchFilters
from another_brain.storage.repository import SQLiteRepository


def vector(axis: int) -> tuple[float, ...]:
    values = [0.0] * 640
    values[axis] = 1.0
    return tuple(values)


def record(*, now_ms: int, content: str = "", brain_id: str = "brain") -> MemoryRecord:
    return MemoryRecord.new(
        brain_id=brain_id,
        agent_id="pytest",
        scope="project",
        scope_id="native",
        topic="windows-native",
        summary="Native SQLite memory works on Windows and Ubuntu.",
        content=content,
        timezone="Asia/Ho_Chi_Minh",
        now_ms=now_ms,
    )


@pytest.fixture
def repository(tmp_path):
    return SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")


def test_store_get_restart_and_fts(repository):
    now = int(time.time() * 1000)
    item = record(now_ms=now, content="identifier WIN-NATIVE-1905")
    repository.store(item, vector(0))
    assert repository.get("brain", item.memory_id, now_ms=now) == item

    reopened = SQLiteRepository(repository.factory.path, timezone="Asia/Ho_Chi_Minh")
    filters = SearchFilters.create("project", "native")
    hits = reopened.lexical_candidates(
        "brain", filters, "WIN-NATIVE-1905", now_ms=now, limit=50
    )
    assert [hit.memory_id for hit, _ in hits] == [item.memory_id]


def test_lifecycle_is_transactional_and_restore_requires_deleted(repository):
    now = 1_800_000_000_000
    item = record(now_ms=now)
    repository.store(item, vector(0))
    assert repository.restore("brain", item.memory_id, now_ms=now + 1) is None
    assert repository.soft_delete(
        "brain", item.memory_id, now_ms=now + 2, grace_ms=30 * 86_400_000
    )
    assert repository.get("brain", item.memory_id, now_ms=now + 3) is None
    restored = repository.restore("brain", item.memory_id, now_ms=now + 4)
    assert restored is not None
    assert restored.deleted_at_ms is None
    assert repository.reinforce("brain", item.memory_id, now_ms=now + 5) is not None
    assert repository.hard_delete("brain", item.memory_id)
    assert repository.get("brain", item.memory_id, now_ms=now + 6) is None


def test_brain_and_scope_isolation(repository):
    now = 1_800_000_000_000
    item = record(now_ms=now, brain_id="brain-a")
    repository.store(item, vector(0))
    assert repository.get("brain-b", item.memory_id, now_ms=now) is None
    filters = SearchFilters.create("project", "other-project")
    assert repository.recent("brain-a", filters, 10, now_ms=now) == []
