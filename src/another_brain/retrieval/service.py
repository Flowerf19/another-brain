"""Independent lexical/vector retrieval with deterministic RRF."""
from __future__ import annotations

import math
import time

import numpy as np

from ..domain.models import SearchFilters, SearchResult
from ..storage.repository import SQLiteRepository
from .fusion import rrf_fuse

CANDIDATE_LIMIT = 50
TOP_K = 5
COSINE_FLOOR_KEY = 300_000


class HybridRetriever:
    def __init__(self, repository: SQLiteRepository):
        self.repository = repository

    def search(
        self,
        brain_id: str,
        filters: SearchFilters,
        query: str,
        query_embedding: tuple[float, ...],
        *,
        now_ms: int | None = None,
    ) -> list[SearchResult]:
        now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
        lexical = self.repository.lexical_candidates(
            brain_id, filters, query, now_ms=now, limit=CANDIDATE_LIMIT
        )
        query_vector = np.asarray(query_embedding, dtype=np.float32)
        query_norm = float(np.linalg.norm(query_vector))
        vector_ranked: list[tuple[int, str, object]] = []
        if query_norm > 0 and math.isfinite(query_norm):
            for record, values in self.repository.vector_rows(
                brain_id, filters, now_ms=now
            ):
                vector = np.asarray(values, dtype=np.float32)
                denominator = query_norm * float(np.linalg.norm(vector))
                if denominator <= 0 or not math.isfinite(denominator):
                    continue
                cosine = float(np.dot(query_vector, vector) / denominator)
                if not math.isfinite(cosine):
                    continue
                key = round(cosine * 1_000_000)
                if key >= COSINE_FLOOR_KEY:
                    vector_ranked.append((key, record.memory_id, record))
        vector_ranked.sort(key=lambda item: (-item[0], item[1]))
        vector_ranked = vector_ranked[:CANDIDATE_LIMIT]
        by_id = {record.memory_id: record for record, _ in lexical}
        by_id.update({memory_id: record for _, memory_id, record in vector_ranked})
        fused = rrf_fuse(
            [record.memory_id for record, _ in lexical],
            [memory_id for _, memory_id, _ in vector_ranked],
            limit=TOP_K,
        )
        return [
            SearchResult(
                memory_id=item.memory_id,
                topic=by_id[item.memory_id].topic,
                catalog=by_id[item.memory_id].catalog,
                summary=by_id[item.memory_id].summary,
                timeline_day=by_id[item.memory_id].timeline_day,
                importance=by_id[item.memory_id].importance,
                has_content=by_id[item.memory_id].has_content,
                relevance_score=item.score,
                score_source=item.source,
            )
            for item in fused
        ]
