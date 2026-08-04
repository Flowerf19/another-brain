from __future__ import annotations

import math

from another_brain.domain.models import MemoryRecord, SearchFilters
from another_brain.retrieval.fusion import rrf_fuse
from another_brain.retrieval.service import HybridRetriever
from another_brain.storage.repository import SQLiteRepository


NOW = 1_800_000_000_000


def vector(axis=0):
    values = [0.0] * 640
    values[axis] = 1.0
    return tuple(values)


def cosine_vector(cosine: float):
    values = [0.0] * 640
    values[0] = cosine
    values[1] = math.sqrt(1.0 - cosine * cosine)
    return tuple(values)


def memory(topic, summary, *, content="", now_ms=NOW, importance=3):
    return MemoryRecord.new(
        brain_id="brain", agent_id="pytest", scope="project", scope_id="project",
        topic=topic, summary=summary, content=content, importance=importance,
        timezone="Asia/Ho_Chi_Minh", now_ms=now_ms,
    )


def test_rrf_combines_both_branches_and_uses_deterministic_ties():
    results = rrf_fuse(["both", "lexical"], ["both", "vector"], limit=5)
    assert results[0].memory_id == "both"
    assert results[0].source == "fused"
    assert results[0].score == 2 / 61
    assert [item.memory_id for item in results[1:]] == ["lexical", "vector"]


def test_rrf_limit_and_memory_id_tie_break_are_stable():
    results = rrf_fuse(["z", "a", "b"], ["z", "a", "b"], limit=2)
    assert [item.memory_id for item in results] == ["z", "a"]


def test_candidate_in_both_fts_and_vector_branches_is_fused(tmp_path):
    repository = SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")
    item = memory("native-search", "Native search marker.")
    repository.store(item, vector())
    result = HybridRetriever(repository).search(
        "brain", SearchFilters.create("project", "project"), "native search", vector(), now_ms=NOW
    )[0]
    assert result.memory_id == item.memory_id
    assert result.score_source == "fused"


def test_punctuation_only_query_uses_vector_branch_only(tmp_path):
    repository = SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")
    item = memory("semantic-memory", "Vector branch result.")
    repository.store(item, vector())
    result = HybridRetriever(repository).search(
        "brain", SearchFilters.create("project", "project"), "!!!", vector(), now_ms=NOW
    )[0]
    assert result.memory_id == item.memory_id
    assert result.score_source == "knn"


def test_vietnamese_without_diacritics_matches_fts5(tmp_path):
    repository = SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")
    item = memory("bo-nho", "Bộ nhớ dùng SQLite.")
    repository.store(item, vector(1))
    results = HybridRetriever(repository).search(
        "brain", SearchFilters.create("project", "project"), "bo nho", vector(), now_ms=NOW
    )
    assert item.memory_id in {result.memory_id for result in results}


def test_cosine_floor_accepts_exact_030_and_rejects_below(tmp_path):
    repository = SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")
    accepted = memory("accepted-vector", "No query terms.")
    rejected = memory("rejected-vector", "No query terms either.", now_ms=NOW + 1)
    repository.store(accepted, cosine_vector(0.3000004))
    repository.store(rejected, cosine_vector(0.2999994))
    results = HybridRetriever(repository).search(
        "brain", SearchFilters.create("project", "project"), "!!!", vector(), now_ms=NOW + 2
    )
    assert [result.memory_id for result in results] == [accepted.memory_id]


def test_expired_deleted_and_other_scope_records_cannot_enter_candidates(tmp_path):
    repository = SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")
    live = memory("shared-marker", "live", now_ms=NOW)
    expired = memory("shared-marker", "expired", now_ms=NOW - 100 * 86_400_000)
    deleted = memory("shared-marker", "deleted", now_ms=NOW + 1)
    other = MemoryRecord.new(
        brain_id="brain", agent_id="pytest", scope="project", scope_id="other",
        topic="shared-marker", summary="other", timezone="Asia/Ho_Chi_Minh", now_ms=NOW,
    )
    for index, item in enumerate((live, expired, deleted, other)):
        repository.store(item, vector(index))
    repository.soft_delete("brain", deleted.memory_id, now_ms=NOW + 2, grace_ms=1_000)
    results = HybridRetriever(repository).search(
        "brain", SearchFilters.create("project", "project"), "shared marker", vector(), now_ms=NOW + 3
    )
    assert [result.memory_id for result in results] == [live.memory_id]


def test_zero_query_vector_still_returns_lexical_matches(tmp_path):
    repository = SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")
    item = memory("lexical-only", "identifier ZERO-VECTOR")
    repository.store(item, vector())
    results = HybridRetriever(repository).search(
        "brain", SearchFilters.create("project", "project"), "ZERO-VECTOR", (0.0,) * 640, now_ms=NOW
    )
    assert [result.memory_id for result in results] == [item.memory_id]
    assert results[0].score_source == "bm25"


def test_final_results_are_capped_at_five(tmp_path):
    repository = SQLiteRepository(tmp_path / "brain.sqlite3", timezone="Asia/Ho_Chi_Minh")
    for index in range(8):
        repository.store(memory(f"common-{index}", "common result", now_ms=NOW + index), vector())
    results = HybridRetriever(repository).search(
        "brain", SearchFilters.create("project", "project"), "common", vector(), now_ms=NOW + 10
    )
    assert len(results) == 5
