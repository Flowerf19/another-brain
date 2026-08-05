"""TASK-062: ranking behavior on the real schema (GOAL-012 gate part 1).

Covers the locked bugfix-v1 cases (lexical-only survival, vector floor,
live-filter starvation), canonical micro-cosine boundaries, ties, malformed
vectors, Vietnamese diacritics, adversarial terms, fused promotion, branch
evidence labels, brain isolation, and exact sqlite-vec/NumPy
candidate/order/RRF parity within the 1e-6 raw-score tolerance, plus
whole-brain recall: search and both candidate branches see every stored row
of the bound brain.

The 24-case behavior partition of embedding-quality-v1 (gate part 2,
deferred from GOAL-001) runs in
``tests/integration/test_retrieval_behavior_gate.py`` with the real q4 model.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from another_brain.domain.models import EmbeddingVector, MemoryRecord, RecentFilters
from another_brain.retrieval.fusion import rrf_fuse
from another_brain.retrieval.service import HybridMemoryRetriever
from another_brain.retrieval.vector import (
    NumpyVectorRetriever,
    SQLiteVecVectorRetriever,
    micro_cosine_key,
)

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "retrieval" / "bugfix-v1.json")
    .read_text(encoding="utf-8")
)
NOW_MS = FIXTURE["now_ms"]
BRAIN_ID = "fixture-brain"
AGENT_ID = "fixture-agent"


def lift(vector8: list[float]) -> EmbeddingVector:
    """8-dim fixture vector -> 640-dim float32, L2-normalized (cosines kept)."""
    values = np.zeros(640, dtype=np.float64)
    values[: len(vector8)] = vector8
    values /= np.linalg.norm(values)
    return EmbeddingVector(values=values.astype(np.float32))


def cosine_with(value: float) -> EmbeddingVector:
    """Unit 640-vector whose cosine to e1 is ``value`` (float32-stable)."""
    values = np.zeros(640, dtype=np.float64)
    values[0] = value
    values[1] = math.sqrt(1.0 - value * value)
    return EmbeddingVector(values=values.astype(np.float32))


E1 = lift([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])


def _record(mid: str, *, topic: str, summary: str, content: str,
            embedding: EmbeddingVector, created: int, expires: int,
            deleted: int | None, catalog: str = "note") -> MemoryRecord:
    return MemoryRecord(
        memory_id=mid, brain_id=BRAIN_ID, agent_id=AGENT_ID,
        topic=topic,
        catalog=catalog, summary=summary, content=content,
        timeline_day="2026-07-25", period_start_ms=None, period_end_ms=None,
        created_at_ms=created, updated_at_ms=created, importance=3,
        expires_at_ms=expires, deleted_at_ms=deleted, metadata={},
        profile_id="q4", record_version=1, embedding=embedding,
    )


@pytest.fixture
def fixture_store(sql_factory):
    """The bugfix-v1 store on the real v1 schema."""
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    repo = SQLiteMemoryRepository(sql_factory, brain_id=BRAIN_ID)
    for m in FIXTURE["memories"]:
        repo.store(_record(
            m["memory_id"], topic=m["topic"], summary=m["summary"],
            content=m["content"], embedding=lift(m["vector"]),
            created=m["created_at_ms"], expires=m["expires_at_ms"],
            deleted=m["deleted_at_ms"],
        ))
    return sql_factory


def retriever(factory, **overrides) -> HybridMemoryRetriever:
    return HybridMemoryRetriever(
        factory, brain_id=BRAIN_ID, clock=lambda: NOW_MS, **overrides
    )


# ---------------------------------------------------------------------------
# bugfix-v1 locked cases


def test_content_identifier_survives_low_cosine(fixture_store):
    fused = retriever(fixture_store).rank(
        query_text="ZXQ-8842", query_vector=E1
    )
    by_id = {r.memory_id: r for r in fused}
    assert "m-content-id" in by_id
    assert by_id["m-content-id"].lexical_rank is not None
    assert by_id["m-content-id"].vector_rank is None  # below floor: lexical-only
    assert "m-deleted" not in by_id
    assert "m-expired" not in by_id


def test_vector_only_below_floor_excluded(fixture_store):
    fused = retriever(fixture_store).rank(
        query_text="kwojek zalquin", query_vector=E1
    )
    by_id = {r.memory_id: r for r in fused}
    assert "m-semantic-ok" in by_id
    assert "m-semantic-low" not in by_id  # 0.25 < floor, no lexical support
    for result in fused:
        assert result.lexical_rank is None  # lexical branch empty (no terms match)


def test_live_filter_before_branch_limits(fixture_store):
    fused = retriever(fixture_store, candidate_limit=2).rank(
        query_text="ZXQ-8842", query_vector=lift([0, 0, 1, 0, 0, 0, 0, 0]),
    )
    by_id = {r.memory_id: r for r in fused}
    assert "m-content-id" in by_id
    assert "m-live-tail" in by_id  # not starved by the two stale rows
    assert "m-deleted" not in by_id
    assert "m-expired" not in by_id


def test_previews_carry_no_content_and_are_live(fixture_store):
    previews = retriever(fixture_store).search(
        query_text="ZXQ-8842", query_vector=E1
    )
    assert previews
    for preview in previews:
        assert not hasattr(preview, "content")
        assert not hasattr(preview, "embedding")
        assert preview.expires_at_ms > NOW_MS


def test_brain_isolation(fixture_store):
    other_brain = HybridMemoryRetriever(
        fixture_store, brain_id="other-brain", clock=lambda: NOW_MS
    )
    assert other_brain.rank(query_text="ZXQ-8842", query_vector=E1) == []


# ---------------------------------------------------------------------------
# canonical micro-cosine floor and rounding


def test_micro_cosine_key_is_half_even_round_of_scaled_score():
    assert micro_cosine_key(0.3) == 300000
    assert micro_cosine_key(0.299998) == 299998
    assert micro_cosine_key(0.300002) == 300002
    assert micro_cosine_key(-0.5) == -500000
    for value in (0.0, 0.123456789, 0.9999999, -0.25, 0.3000005):
        assert micro_cosine_key(value) == round(value * 1_000_000)


@pytest.mark.parametrize(
    "value, expected",
    [(0.299998, False), (0.299999, False), (0.300000, True), (0.300002, True)],
)
def test_canonical_floor_boundaries(sql_factory, value, expected):
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    repo = SQLiteMemoryRepository(sql_factory, brain_id=BRAIN_ID)
    repo.store(_record(
        "boundary-doc", topic="boundary-topic", summary="boundary summary",
        content="", embedding=cosine_with(value), created=NOW_MS - 1000,
        expires=NOW_MS + 1000, deleted=None,
    ))
    hits = retriever(sql_factory).rank(
        query_text="nomatch-zzz", query_vector=E1
    )
    assert ("boundary-doc" in {r.memory_id for r in hits}) is expected


# ---------------------------------------------------------------------------
# ties, fused promotion, branch evidence


def test_equal_score_ties_break_by_memory_id(sql_factory):
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    repo = SQLiteMemoryRepository(sql_factory, brain_id=BRAIN_ID)
    shared = cosine_with(0.9)
    for mid in ("tie-b", "tie-a", "tie-c"):
        repo.store(_record(
            mid, topic=f"topic-{mid}", summary=f"summary {mid}", content="",
            embedding=shared, created=NOW_MS - 1000, expires=NOW_MS + 1000,
            deleted=None,
        ))
    fused = retriever(sql_factory).rank(
        query_text="nomatch-zzz", query_vector=E1
    )
    assert [r.memory_id for r in fused] == ["tie-a", "tie-b", "tie-c"]
    assert all(r.vector_rank is not None for r in fused)


def test_fused_dual_branch_promotes_above_single_branch(fixture_store):
    fused = retriever(fixture_store).rank(
        query_text="latency budget", query_vector=E1
    )
    by_id = {r.memory_id: r for r in fused}
    dual = by_id["m-semantic-ok"]
    assert dual.lexical_rank is not None and dual.vector_rank is not None
    singles = [r for r in fused if r.lexical_rank is None or r.vector_rank is None]
    assert all(dual.score > r.score for r in singles)


# ---------------------------------------------------------------------------
# malformed vectors


def _insert_raw_blob(sql_factory, memory_id: str, blob: bytes) -> None:
    with sql_factory.connect() as con:
        con.connection.execute(
            "INSERT INTO memories(memory_id, brain_id, agent_id,"
            " topic, catalog, summary, content, timeline_day, period_start_ms,"
            " period_end_ms, created_at_ms, updated_at_ms, importance,"
            " expires_at_ms, deleted_at_ms, metadata, profile_id, embedding,"
            " record_version)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                memory_id, BRAIN_ID, AGENT_ID,
                "malformed-topic", "note", "malformed summary", "",
                "2026-07-25", None, None, NOW_MS - 1000, NOW_MS - 1000, 3,
                NOW_MS + 1000, None, "{}", "q4", blob, 1,
            ),
        )
        con.connection.commit()


def test_malformed_vectors_rejected_by_both_adapters(sql_factory):
    _insert_raw_blob(sql_factory, "nan-doc", np.full(640, np.nan, dtype="<f4").tobytes())
    _insert_raw_blob(sql_factory, "zero-doc", np.zeros(640, dtype="<f4").tobytes())
    with pytest.raises(Exception):  # schema CHECK(length(embedding)=2560)
        _insert_raw_blob(sql_factory, "short-doc", np.zeros(8, dtype="<f4").tobytes())

    with sql_factory.connect() as con:
        assert con.load_vec()
        vec_hits = SQLiteVecVectorRetriever(con.connection, brain_id=BRAIN_ID).candidates(
            query_vector=E1, now_ms=NOW_MS
        )
        np_hits = NumpyVectorRetriever(con.connection, brain_id=BRAIN_ID).candidates(
            query_vector=E1, now_ms=NOW_MS
        )
    for hits in (vec_hits, np_hits):
        assert "nan-doc" not in {h.memory_id for h in hits}
        assert "zero-doc" not in {h.memory_id for h in hits}
    # Hybrid search never crashes on corrupt blobs: the rows remain lexical-
    # only candidates (their vectors are rejected, vector_rank stays None).
    fused = retriever(sql_factory).rank(
        query_text="malformed", query_vector=E1
    )
    by_id = {r.memory_id: r for r in fused}
    assert by_id["nan-doc"].lexical_rank is not None
    assert by_id["nan-doc"].vector_rank is None
    assert by_id["zero-doc"].vector_rank is None


# ---------------------------------------------------------------------------
# Vietnamese diacritics + adversarial terms (end to end)


def test_vietnamese_diacritics_match_both_directions(sql_factory):
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    repo = SQLiteMemoryRepository(sql_factory, brain_id=BRAIN_ID)
    repo.store(_record(
        "vi-doc", topic="tưới-cây", summary="Tưới cây mỗi sáng định kỳ",
        content="", embedding=cosine_with(0.1), created=NOW_MS - 1000,
        expires=NOW_MS + 1000, deleted=None,
    ))
    no_diacritic = retriever(sql_factory).rank(
        query_text="tuoi cay", query_vector=E1
    )
    assert "vi-doc" in {r.memory_id for r in no_diacritic}
    with_diacritics = retriever(sql_factory).rank(
        query_text="tưới cây", query_vector=E1
    )
    assert "vi-doc" in {r.memory_id for r in with_diacritics}


def test_adversarial_duplicate_and_syntax_terms(fixture_store):
    fused = retriever(fixture_store).rank(
        query_text='ZXQ-8842 ZXQ-8842 zxq " OR NEAR/1 AND NOT',
        query_vector=E1,
    )
    assert "m-content-id" in {r.memory_id for r in fused}


# ---------------------------------------------------------------------------
# filters


def test_filters_narrow_both_branches(fixture_store):
    fused = retriever(fixture_store).rank(
        query_text="ZXQ-8842", query_vector=E1,
        filters=RecentFilters(topic="run-followups"),
    )
    assert {r.memory_id for r in fused} == {"m-live-tail"}


# ---------------------------------------------------------------------------
# whole-brain recall (the only mode: every read spans the bound brain)


def _store_whole_brain_rows(sql_factory) -> None:
    """Three live rows with distinct topics; no partitioning exists."""
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    repo = SQLiteMemoryRepository(sql_factory, brain_id=BRAIN_ID)
    for i, topic in enumerate(("whole-brain-alpha", "whole-brain-beta",
                               "whole-brain-gamma")):
        repo.store(_record(
            f"whole-{i:02d}", topic=topic, summary=f"whole brain row {i}",
            content="", embedding=E1, created=NOW_MS - 1000 + i,
            expires=NOW_MS + 1000, deleted=None,
        ))


def test_search_returns_matching_memories_across_the_whole_brain(sql_factory):
    _store_whole_brain_rows(sql_factory)
    previews = retriever(sql_factory).search(
        query_text="whole-brain", query_vector=E1,
    )
    assert {p.memory_id for p in previews} == {"whole-00", "whole-01", "whole-02"}


def test_lexical_candidates_cover_the_whole_brain(sql_factory):
    from another_brain.retrieval.lexical import SQLiteLexicalRetriever
    from another_brain.retrieval.query import build_match_query

    _store_whole_brain_rows(sql_factory)
    with sql_factory.connect() as con:
        hits = SQLiteLexicalRetriever(con.connection, brain_id=BRAIN_ID).candidates(
            match_query=build_match_query("whole-brain"), now_ms=NOW_MS,
        )
    assert {h.memory_id for h in hits} == {"whole-00", "whole-01", "whole-02"}


def test_vector_candidates_cover_the_whole_brain(sql_factory):
    _store_whole_brain_rows(sql_factory)
    with sql_factory.connect() as con:
        assert con.load_vec()
        vec_hits = SQLiteVecVectorRetriever(
            con.connection, brain_id=BRAIN_ID
        ).candidates(query_vector=E1, now_ms=NOW_MS)
        np_hits = NumpyVectorRetriever(
            con.connection, brain_id=BRAIN_ID
        ).candidates(query_vector=E1, now_ms=NOW_MS)
    for hits in (vec_hits, np_hits):
        assert {h.memory_id for h in hits} == {"whole-00", "whole-01", "whole-02"}


# ---------------------------------------------------------------------------
# sqlite-vec / NumPy parity (candidate IDs, keys, order, ranks, RRF)


def test_vector_adapter_parity(fixture_store):
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    repo = SQLiteMemoryRepository(fixture_store, brain_id=BRAIN_ID)
    rng = np.random.default_rng(20260804)
    query = rng.standard_normal(640, dtype=np.float32)
    query /= np.linalg.norm(query)
    qv = EmbeddingVector(values=query)
    # Correlated docs with engineered integer-micro cosines to e1: exact
    # keys with a 0.5-micro gap to every rounding boundary (the contract's
    # "rounding-boundary gaps" — float32 accumulation error is ~0.2 micro),
    # plus deliberate 4-way key ties to exercise tie ordering parity.
    query8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    qv = lift(query8)
    for i in range(200):
        key = 300000 + (i % 50) * 10000  # integer micro-cosine, ties included
        cosine = key / 1_000_000
        values = np.zeros(640, dtype=np.float64)
        values[0] = cosine
        tail = rng.standard_normal(639, dtype=np.float64)
        tail /= np.linalg.norm(tail)
        values[1:] = math.sqrt(1.0 - cosine * cosine) * tail
        repo.store(_record(
            f"rand-{i:03d}", topic=f"random-topic-{i}", summary=f"random {i}",
            content="", embedding=EmbeddingVector(values=values.astype(np.float32)),
            created=NOW_MS - 1000 + i, expires=NOW_MS + 1000, deleted=None,
        ))

    with fixture_store.connect() as con:
        assert con.load_vec()
        vec_hits = SQLiteVecVectorRetriever(con.connection, brain_id=BRAIN_ID).candidates(
            query_vector=qv, now_ms=NOW_MS
        )
        np_hits = NumpyVectorRetriever(con.connection, brain_id=BRAIN_ID).candidates(
            query_vector=qv, now_ms=NOW_MS
        )

    assert [(h.memory_id, h.cosine_key, h.rank) for h in vec_hits] == [
        (h.memory_id, h.cosine_key, h.rank) for h in np_hits
    ]
    assert len(vec_hits) == 50  # full candidate page, all above the floor
    raw = {h.memory_id: h.raw_cosine for h in np_hits}
    for hit in vec_hits:
        assert abs(hit.raw_cosine - raw[hit.memory_id]) <= 1e-6

    # RRF output parity: same lexical input, adapter-specific vector input.
    from another_brain.retrieval.lexical import SQLiteLexicalRetriever
    from another_brain.retrieval.query import build_match_query

    with fixture_store.connect() as con:
        lexical = SQLiteLexicalRetriever(con.connection, brain_id=BRAIN_ID).candidates(
            match_query=build_match_query("ZXQ-8842 random"),
            now_ms=NOW_MS,
        )
    assert rrf_fuse(lexical, vec_hits) == rrf_fuse(lexical, np_hits)
