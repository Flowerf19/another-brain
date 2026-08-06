"""Pure deterministic reciprocal-rank fusion (TASK-060).

Equal-weight RRF over the two retrieval branches: one-based rank ``r``
contributes ``1 / (k + r)`` with locked ``k = 60``; a memory present in both
branches receives both contributions. Inputs are the fixed 50-candidate
branch lists; output is the deterministic top-5.

``k`` is an independent score-smoothing constant — it never tracks the
candidate count. The tie-break sequence is locked: fused score descending,
branch count descending, best branch rank ascending, ``memory_id``
ascending. RRF contributions are IEEE doubles computed from integer ranks,
so identical inputs give bit-identical scores on every platform.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from another_brain.config import RRF_K, TOP_K


@dataclass(frozen=True)
class BranchCandidate:
    """One candidate from one branch; ``rank`` is one-based."""

    memory_id: str
    rank: int


@dataclass(frozen=True)
class FusedResult:
    """One fused hit with branch evidence (``None`` when absent from a branch)."""

    memory_id: str
    score: float
    lexical_rank: int | None
    vector_rank: int | None


def rrf_fuse(
    lexical: Sequence[BranchCandidate],
    vector: Sequence[BranchCandidate],
    *,
    top_k: int = TOP_K,
    k: int = RRF_K,
) -> list[FusedResult]:
    """Fuse two branch candidate lists into the deterministic top-k."""
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")
    if k < 1:
        raise ValueError(f"RRF k must be >= 1, got {k}")

    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for branch, candidates in (("lexical", lexical), ("vector", vector)):
        best: dict[str, int] = {}
        for candidate in candidates:
            if candidate.rank < 1:
                raise ValueError(
                    f"branch ranks are one-based, got {candidate.rank}"
                    f" for {candidate.memory_id!r}"
                )
            previous = best.get(candidate.memory_id)
            if previous is None or candidate.rank < previous:
                best[candidate.memory_id] = candidate.rank
        for memory_id, rank in best.items():
            scores[memory_id] = scores.get(memory_id, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(memory_id, {})[branch] = rank

    def sort_key(memory_id: str) -> tuple:
        branch_ranks = ranks[memory_id]
        return (
            -scores[memory_id],
            -len(branch_ranks),
            min(branch_ranks.values()),
            memory_id,
        )

    ordered = sorted(scores, key=sort_key)
    return [
        FusedResult(
            memory_id=memory_id,
            score=scores[memory_id],
            lexical_rank=ranks[memory_id].get("lexical"),
            vector_rank=ranks[memory_id].get("vector"),
        )
        for memory_id in ordered[:top_k]
    ]
