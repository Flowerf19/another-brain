from another_brain.domain.models import MemoryRecord, SearchFilters
from another_brain.retrieval.service import HybridRetriever
from another_brain.storage.repository import SQLiteRepository


def vec(axis: int):
    values = [0.0] * 640
    values[axis] = 1.0
    return tuple(values)


def memory(topic: str, summary: str, content: str, now: int):
    return MemoryRecord.new(
        brain_id="brain",
        agent_id="pytest",
        scope="project",
        scope_id="native",
        topic=topic,
        summary=summary,
        content=content,
        timezone="Asia/Ho_Chi_Minh",
        now_ms=now,
    )


def test_lexical_only_content_match_survives_low_cosine(tmp_path):
    repository = SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")
    now = 1_800_000_000_000
    lexical = memory("windows-identifier", "Unrelated summary.", "ticket ABWIN-9921", now)
    semantic = memory("native-runtime", "Run the native runtime on Windows.", "", now + 1)
    repository.store(lexical, vec(1))
    repository.store(semantic, vec(0))

    results = HybridRetriever(repository).search(
        "brain",
        SearchFilters.create("project", "native"),
        "ABWIN-9921",
        vec(0),
        now_ms=now + 2,
    )
    by_id = {result.memory_id: result for result in results}
    assert lexical.memory_id in by_id
    assert by_id[lexical.memory_id].score_source == "bm25"
    assert semantic.memory_id in by_id


def test_vector_below_floor_is_excluded(tmp_path):
    repository = SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")
    now = 1_800_000_000_000
    item = memory("orthogonal-memory", "No lexical overlap.", "", now)
    repository.store(item, vec(1))
    results = HybridRetriever(repository).search(
        "brain",
        SearchFilters.create("project", "native"),
        "different query",
        vec(0),
        now_ms=now + 1,
    )
    assert item.memory_id not in {result.memory_id for result in results}
