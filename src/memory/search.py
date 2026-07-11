"""MemorySearchEngine — one FT.HYBRID round trip, then the app-layer cosine
gate (Step 04 §6.4 / decision 10, on the step-05 FT.HYBRID mechanism).

Redis fuses BM25 + KNN with RRF (CONSTANT = SEARCH_FUSION_K); what remains
client-side is exactly the quality gate: every candidate must clear
SEARCH_MIN_COSINE, computed from its stored embedding — a BM25-only doc never
got a vector score from Redis, so BM25 must not bypass the similarity gate
(march7 fix B3). Gating happens BEFORE the final top-k cut: if the limit came
first, ungated BM25 noise could occupy top-k slots (gate-before-limit).
"""
from __future__ import annotations

import math
from typing import Sequence

from config import SearchConfig
from memory.models import (
    EmbeddingVector,
    MemorySearchResult,
    ScoreSource,
    SearchFilters,
)
from storage.redis_repository import (
    HybridHit,
    RedisMemoryRepository,
    RedisSearchHit,
    sanitize_terms,
)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Plain cosine; -1.0 for missing/degenerate vectors so they never pass
    the gate. Norms are computed rather than assumed 1.0 so the gate stays
    correct even with NORMALIZE_EMBEDDINGS=false."""
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if norm == 0.0:
        return -1.0
    return dot / norm


class MemorySearchEngine:
    def __init__(self, repository: RedisMemoryRepository, config: SearchConfig):
        self._repo = repository
        self._config = config

    async def search(
        self,
        brain_id: str,
        filters: SearchFilters,
        query_text: str,
        query_embedding: EmbeddingVector,
    ) -> list[MemorySearchResult]:
        """Hybrid search returning gated previews in fusion order.

        A query with no BM25-safe terms (e.g. pure punctuation) degrades to
        KNN-only — a filter-only SEARCH branch would rank every doc equally
        and feed noise into the RRF sum.
        """
        if sanitize_terms(query_text):
            hits = await self._repo.hybrid_search(
                brain_id,
                filters,
                query_text,
                query_embedding,
                knn_k=self._config.top_k,
                window=self._config.top_k,
                fusion_constant=self._config.fusion_k,
                limit=2 * self._config.top_k,
            )
            results = self._gate_hybrid(hits, query_embedding)
        else:
            knn_hits = await self._repo.knn_search(
                brain_id, filters, query_embedding, limit=self._config.top_k
            )
            results = self._gate_knn(knn_hits, query_embedding)
        return results[: self._config.top_k]

    # -------------------------------------------------------------- gating

    def _gate_hybrid(
        self, hits: list[HybridHit], query_embedding: EmbeddingVector
    ) -> list[MemorySearchResult]:
        results: list[MemorySearchResult] = []
        for hit in hits:
            if self._cosine(hit, query_embedding) < self._config.min_cosine:
                continue
            if hit.text_score is not None and hit.vector_score is not None:
                source = ScoreSource.FUSED
            elif hit.vector_score is not None:
                source = ScoreSource.KNN
            else:
                source = ScoreSource.BM25
            results.append(
                MemorySearchResult(
                    memory_id=hit.memory_id,
                    topic=hit.topic,
                    catalog=hit.catalog,
                    summary=hit.summary,
                    timeline_day=hit.timeline_day,
                    importance=hit.importance,
                    has_content=hit.has_content,
                    relevance_score=hit.fused_score,
                    score_source=source,
                )
            )
        return results

    def _gate_knn(
        self, hits: list[RedisSearchHit], query_embedding: EmbeddingVector
    ) -> list[MemorySearchResult]:
        results: list[MemorySearchResult] = []
        for hit in hits:
            cosine = cosine_similarity(query_embedding.values, hit.embedding)
            if cosine < self._config.min_cosine:
                continue
            record = hit.record
            results.append(
                MemorySearchResult(
                    memory_id=record.identity.memory_id,
                    topic=record.topic,
                    catalog=record.catalog,
                    summary=record.summary,
                    timeline_day=record.timeline_day,
                    importance=record.importance,
                    has_content=record.has_content,
                    relevance_score=cosine,
                    score_source=ScoreSource.KNN,
                )
            )
        return results

    def _cosine(self, hit: HybridHit, query_embedding: EmbeddingVector) -> float:
        """One gate for every hit, whichever branch surfaced it: the exact
        cosine against the stored embedding. Falls back to the VSIM score
        (vector_score = (1 + cosine) / 2, verified on 8.8) only if the
        embedding failed to load."""
        if hit.embedding:
            return cosine_similarity(query_embedding.values, hit.embedding)
        if hit.vector_score is not None:
            return 2.0 * hit.vector_score - 1.0
        return -1.0
