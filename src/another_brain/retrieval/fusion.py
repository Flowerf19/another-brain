"""Deterministic reciprocal-rank fusion."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FusedRank:
    memory_id: str
    score: float
    source: str


def rrf_fuse(
    lexical_ids: list[str], vector_ids: list[str], *, k: int = 60, limit: int = 5
) -> list[FusedRank]:
    evidence: dict[str, dict[str, int]] = {}
    for source, ids in (("bm25", lexical_ids), ("knn", vector_ids)):
        for rank, memory_id in enumerate(ids, start=1):
            evidence.setdefault(memory_id, {})[source] = rank
    ranked = []
    for memory_id, branches in evidence.items():
        score = sum(1.0 / (k + rank) for rank in branches.values())
        ranked.append(
            (
                -score,
                -len(branches),
                min(branches.values()),
                memory_id,
                FusedRank(
                    memory_id,
                    score,
                    "fused" if len(branches) == 2 else next(iter(branches)),
                ),
            )
        )
    ranked.sort(key=lambda item: item[:4])
    return [item[4] for item in ranked[:limit]]
