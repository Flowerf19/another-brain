"""MemorySearchEngine unit tests: cosine gate on FT.HYBRID hits (including
BM25-only docs — march7 fix B3), gate-before-limit, score-source
attribution, and the KNN-only fallback for term-less queries."""
import pytest

from config import SearchConfig
from errors import ValidationError
from memory.models import (
    EmbeddingVector,
    MemoryRecord,
    ScoreSource,
    SearchFilters,
)
from memory.search import MemorySearchEngine, cosine_similarity
from storage.redis_repository import HybridHit, RedisSearchHit

DIM = 4
CONFIG = SearchConfig(top_k=3, fusion_k=60, min_cosine=0.30)
FILTERS = SearchFilters(scope="user", scope_id="flowerf")
QUERY_VEC = EmbeddingVector.from_list([1.0, 0.0, 0.0, 0.0], DIM)

CLOSE = (1.0, 0.0, 0.0, 0.0)          # cosine 1.0 vs query
NEAR = (0.9, 0.4359, 0.0, 0.0)        # cosine 0.9
FAR = (0.0, 0.0, 0.0, 1.0)            # cosine 0.0 — below the 0.30 floor


def hybrid_hit(memory_id, embedding, *, text=None, vector=None, fused=0.02):
    return HybridHit(
        memory_id=memory_id,
        topic="redis-upgrade",
        catalog="note",
        summary=f"summary {memory_id}",
        timeline_day="2026-07-11",
        importance=3,
        has_content=False,
        embedding=embedding,
        text_score=text,
        vector_score=vector,
        fused_score=fused,
    )


def knn_hit(memory_id, embedding, score):
    record = MemoryRecord.new(
        brain_id="flowerf-main",
        agent_id="agent-a",
        scope="user",
        scope_id="flowerf",
        topic="redis-upgrade",
        summary=f"summary {memory_id}",
        tz_name="Asia/Ho_Chi_Minh",
        memory_id=memory_id,
        now_ts=1_752_200_000.0,
    )
    return RedisSearchHit(record=record, embedding=embedding, score=score)


class FakeRepo:
    def __init__(self, hybrid_hits=(), knn_hits=()):
        self.hybrid_hits = list(hybrid_hits)
        self.knn_hits = list(knn_hits)
        self.hybrid_calls = []
        self.knn_calls = []

    async def hybrid_search(self, brain_id, filters, query_text, query_embedding, **kw):
        self.hybrid_calls.append({"brain_id": brain_id, "query": query_text, **kw})
        return self.hybrid_hits

    async def knn_search(self, brain_id, filters, query_embedding, limit):
        self.knn_calls.append({"brain_id": brain_id, "limit": limit})
        return self.knn_hits


async def test_bm25_only_doc_is_still_cosine_gated():
    """Fix B3: a doc surfaced only by the text branch has no vector_score —
    its cosine comes from the stored embedding and can still gate it out."""
    repo = FakeRepo(hybrid_hits=[
        hybrid_hit("both", CLOSE, text=1.4, vector=1.0, fused=0.033),
        hybrid_hit("bm25-far", FAR, text=0.9, vector=None, fused=0.016),
        hybrid_hit("bm25-near", NEAR, text=0.8, vector=None, fused=0.015),
    ])
    results = await MemorySearchEngine(repo, CONFIG).search(
        "flowerf-main", FILTERS, "redis storage", QUERY_VEC
    )
    assert [r.memory_id for r in results] == ["both", "bm25-near"]


async def test_gate_runs_before_the_top_k_cut():
    """A gated-out doc must not consume a top_k slot (gate-before-limit)."""
    config = SearchConfig(top_k=2, fusion_k=60, min_cosine=0.30)
    repo = FakeRepo(hybrid_hits=[
        hybrid_hit("a", CLOSE, text=1.0, vector=1.0, fused=0.033),
        hybrid_hit("junk", FAR, text=0.9, vector=None, fused=0.020),
        hybrid_hit("b", NEAR, text=0.5, vector=0.95, fused=0.016),
    ])
    results = await MemorySearchEngine(repo, config).search(
        "flowerf-main", FILTERS, "redis storage", QUERY_VEC
    )
    assert [r.memory_id for r in results] == ["a", "b"]


async def test_score_source_attribution_and_fused_order():
    repo = FakeRepo(hybrid_hits=[
        hybrid_hit("both", CLOSE, text=1.4, vector=1.0, fused=0.033),
        hybrid_hit("vec-only", NEAR, text=None, vector=0.95, fused=0.016),
        hybrid_hit("text-only", NEAR, text=0.9, vector=None, fused=0.015),
    ])
    results = await MemorySearchEngine(repo, CONFIG).search(
        "flowerf-main", FILTERS, "redis storage", QUERY_VEC
    )
    assert [r.score_source for r in results] == [
        ScoreSource.FUSED, ScoreSource.KNN, ScoreSource.BM25
    ]
    assert [r.relevance_score for r in results] == [0.033, 0.016, 0.015]
    # Preview payload carries no content/embedding, only the diary line.
    assert results[0].summary == "summary both"
    assert results[0].timeline_day == "2026-07-11"


async def test_hybrid_parameters_come_from_config():
    repo = FakeRepo(hybrid_hits=[])
    await MemorySearchEngine(repo, CONFIG).search(
        "flowerf-main", FILTERS, "redis", QUERY_VEC
    )
    call = repo.hybrid_calls[0]
    assert call["knn_k"] == CONFIG.top_k
    assert call["window"] == CONFIG.top_k
    assert call["fusion_constant"] == CONFIG.fusion_k
    assert call["limit"] == 2 * CONFIG.top_k
    assert repo.knn_calls == []


async def test_termless_query_falls_back_to_knn_only():
    """Pure punctuation sanitizes to nothing — a filter-only SEARCH branch
    would add RRF noise, so the engine degrades to gated KNN."""
    repo = FakeRepo(knn_hits=[
        knn_hit("close", CLOSE, score=0.0),      # cosine distance 0 → cos 1.0
        knn_hit("far", FAR, score=1.0),          # cos 0.0 → gated
    ])
    results = await MemorySearchEngine(repo, CONFIG).search(
        "flowerf-main", FILTERS, "!!! ???", QUERY_VEC
    )
    assert repo.hybrid_calls == []
    assert repo.knn_calls == [{"brain_id": "flowerf-main", "limit": CONFIG.top_k}]
    assert [r.memory_id for r in results] == ["close"]
    assert results[0].score_source is ScoreSource.KNN
    assert results[0].relevance_score == pytest.approx(1.0)


async def test_missing_embedding_falls_back_to_vector_score():
    repo = FakeRepo(hybrid_hits=[
        hybrid_hit("no-emb-scored", (), text=None, vector=0.9, fused=0.02),   # cos 0.8
        hybrid_hit("no-emb-unscored", (), text=0.9, vector=None, fused=0.01),  # ungateable
    ])
    results = await MemorySearchEngine(repo, CONFIG).search(
        "flowerf-main", FILTERS, "redis", QUERY_VEC
    )
    assert [r.memory_id for r in results] == ["no-emb-scored"]


def test_cosine_similarity_degenerate_inputs():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.6, 0.8]) == pytest.approx(0.6)
    assert cosine_similarity([], [1.0]) == -1.0
    assert cosine_similarity([1.0], [1.0, 0.0]) == -1.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == -1.0


async def test_repository_hybrid_requires_terms():
    """Guard on the repo API itself: hybrid_search without BM25-safe terms is
    a programming error (the engine must route those to KNN)."""
    from memory.retention import RetentionPolicy
    from storage.redis_keys import RedisKeyBuilder
    from storage.redis_repository import RedisMemoryRepository

    repo = RedisMemoryRepository(
        None, RedisKeyBuilder(), RetentionPolicy(),
        grace_seconds=60, embedding_dim=DIM,
    )
    with pytest.raises(ValidationError, match="text term"):
        await repo.hybrid_search(
            "b", FILTERS, "!!!", QUERY_VEC,
            knn_k=1, window=1, fusion_constant=60, limit=1,
        )
