"""Hybrid retrieval orchestration (TASK-061).

Runs the two branches independently over one read-only connection, fuses
with the locked equal-weight RRF, and resolves previews:

- lexical branch runs only when the query yields safe FTS terms
  (punctuation-only input is vector-only);
- vector branch runs on sqlite-vec when the connection loads the extension,
  otherwise on the exact NumPy fallback — per-connection capability, never
  global state;
- lexical-only candidates are valid results: there is NO universal
  post-fusion cosine gate (the locked fix for the legacy content-match bug);
- the cosine floor applies only to vector candidates, before branch ranks.

``rank`` returns the fused list with branch evidence; ``search`` (the
``MemoryRetriever`` protocol) maps it to ``SearchPreview`` — previews never
carry ``content`` or embeddings.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from another_brain.config import CANDIDATE_LIMIT, TOP_K
from another_brain.domain.models import EmbeddingVector, RecentFilters, SearchPreview
from another_brain.protocols import Scope, ScopeKey
from another_brain.retrieval.fusion import FusedResult, rrf_fuse
from another_brain.retrieval.lexical import SQLiteLexicalRetriever
from another_brain.retrieval.query import build_match_query
from another_brain.retrieval.vector import NumpyVectorRetriever, SQLiteVecVectorRetriever
from another_brain.services.sql.connection import SQLiteConnectionFactory

_PREVIEW_COLUMNS = (
    "memory_id", "topic", "summary", "scope", "scope_id", "created_at_ms",
    "importance", "expires_at_ms",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


class HybridMemoryRetriever:
    """The ``MemoryRetriever`` protocol over FTS5 + exact vector + RRF."""

    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        *,
        brain_id: str,
        clock: Callable[[], int] = _now_ms,
        candidate_limit: int = CANDIDATE_LIMIT,
        top_k: int = TOP_K,
    ) -> None:
        factory.verify_schema()
        self._factory = factory
        self._brain_id = brain_id
        self._clock = clock
        self._candidate_limit = candidate_limit
        self._top_k = top_k

    def vector_backend(self) -> str:
        """``"sqlite-vec"`` or ``"numpy"`` for health/doctor; no semantic drift.

        Probes one read-only connection's extension capability — the same
        per-connection decision a search makes. Never loads the model.
        """
        with self._factory.connect(read_only=True) as con:
            return "sqlite-vec" if con.load_vec() else "numpy"

    def rank(
        self,
        *,
        query_text: str,
        query_vector: EmbeddingVector,
        scope: ScopeKey,
        filters: RecentFilters | None = None,
    ) -> list[FusedResult]:
        """Fused candidate list with branch evidence (test/parity surface)."""
        now_ms = self._clock()
        match_query = build_match_query(query_text)
        with self._factory.connect(read_only=True) as con:
            raw = con.connection
            lexical = (
                SQLiteLexicalRetriever(raw, brain_id=self._brain_id).candidates(
                    match_query=match_query,
                    scope=scope,
                    filters=filters,
                    now_ms=now_ms,
                    limit=self._candidate_limit,
                )
                if match_query is not None
                else []
            )
            vector_retriever = (
                SQLiteVecVectorRetriever if con.load_vec() else NumpyVectorRetriever
            )
            vector = vector_retriever(raw, brain_id=self._brain_id).candidates(
                query_vector=query_vector,
                scope=scope,
                filters=filters,
                now_ms=now_ms,
                limit=self._candidate_limit,
            )
        return rrf_fuse(lexical, vector, top_k=self._top_k)

    def search(
        self,
        *,
        query_text: str,
        query_vector: EmbeddingVector,
        scope: ScopeKey,
        filters: RecentFilters | None = None,
    ) -> Sequence[SearchPreview]:
        """Fused previews for one bounded query in one collection scope."""
        fused = self.rank(
            query_text=query_text, query_vector=query_vector, scope=scope,
            filters=filters,
        )
        if not fused:
            return []
        now_ms = self._clock()
        placeholders = ", ".join("?" for _ in fused)
        with self._factory.connect(read_only=True) as con:
            rows = con.connection.execute(
                f"SELECT {', '.join(_PREVIEW_COLUMNS)} FROM memories"
                f" WHERE brain_id = ? AND memory_id IN ({placeholders})"
                " AND deleted_at_ms IS NULL AND expires_at_ms > ?",
                (self._brain_id, *(r.memory_id for r in fused), now_ms),
            ).fetchall()
        by_id = {}
        for row in rows:
            values = dict(zip(_PREVIEW_COLUMNS, row))
            values["scope"] = Scope(values["scope"])
            by_id[values["memory_id"]] = SearchPreview(**values)
        # A row hard-deleted between the branch snapshot and this fetch is
        # skipped; the fused order is preserved for the rest.
        return [by_id[r.memory_id] for r in fused if r.memory_id in by_id]
