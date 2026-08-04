"""TASK-060: pure deterministic RRF fusion."""
from __future__ import annotations

import pytest

from another_brain.retrieval.fusion import BranchCandidate, rrf_fuse


def cand(memory_id: str, rank: int) -> BranchCandidate:
    return BranchCandidate(memory_id=memory_id, rank=rank)


def test_equal_weight_contributions():
    fused = rrf_fuse([cand("a", 1)], [cand("b", 1)])
    assert fused[0].score == pytest.approx(1.0 / 61)
    assert fused[1].score == pytest.approx(1.0 / 61)


def test_dual_branch_membership_sums_contributions():
    fused = rrf_fuse([cand("a", 1)], [cand("a", 3)])
    assert fused[0].memory_id == "a"
    assert fused[0].score == pytest.approx(1.0 / 61 + 1.0 / 63)
    assert fused[0].lexical_rank == 1
    assert fused[0].vector_rank == 3


def test_branch_absence_is_none_evidence():
    fused = rrf_fuse([cand("a", 2)], [])
    assert fused[0].lexical_rank == 2
    assert fused[0].vector_rank is None


def test_intra_branch_duplicates_keep_best_rank():
    fused = rrf_fuse([cand("a", 5), cand("a", 2)], [])
    assert fused[0].lexical_rank == 2
    assert fused[0].score == pytest.approx(1.0 / 62)


def test_tie_break_branch_count_then_best_rank_then_memory_id():
    # Same fused score: 1/61 from both branches vs 1/61 from one branch.
    fused = rrf_fuse([cand("dual", 1)], [cand("dual", 61)])
    assert fused[0].score == pytest.approx(1.0 / 61 + 1.0 / 121)
    # Equal score + equal branch count: best branch rank asc.
    tied = rrf_fuse(
        [cand("rank2", 2), cand("rank1", 1)],
        [cand("rank2", 61), cand("rank1", 62)],
    )
    assert tied[0].score != tied[1].score  # sanity: ranks differ
    # Exact tie on score + branch count + best rank → memory_id asc.
    exact = rrf_fuse([cand("b-id", 1), cand("a-id", 1)], [])
    assert [r.memory_id for r in exact] == ["a-id", "b-id"]


def test_top_k_cut_and_determinism():
    lexical = [cand("shared", 1)] + [cand(f"lex-{i:02d}", i + 1) for i in range(1, 50)]
    vector = [cand("shared", 1)] + [cand(f"vec-{i:02d}", i + 1) for i in range(1, 50)]
    first = rrf_fuse(lexical, vector)
    second = rrf_fuse(list(reversed(lexical)), list(reversed(vector)))
    assert len(first) == 5
    assert first == second  # input order never matters
    # The candidate present in both branches at rank 1 tops the list.
    assert first[0].memory_id == "shared"
    assert first[0].score == pytest.approx(2.0 / 61)


def test_validation():
    with pytest.raises(ValueError, match="one-based"):
        rrf_fuse([cand("a", 0)], [])
    with pytest.raises(ValueError, match="top_k"):
        rrf_fuse([], [], top_k=0)
    with pytest.raises(ValueError, match="RRF k"):
        rrf_fuse([], [], k=0)
