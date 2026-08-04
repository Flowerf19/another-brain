"""FTS5 BM25 lexical candidate source (TASK-057).

One branch of the hybrid retriever: weighted BM25 over the external-content
``memory_fts(topic, summary, content)`` index with locked weights 5:3:1,
mandatory brain/scope/live filtering (plus optional ``RecentFilters``)
BEFORE the fixed 50-candidate limit, deterministic order
``bm25 ASC, memory_id ASC``, one-based ranks.

This branch has no embedding dependency: lexical-only candidates remain
valid final results even when their vector cosine sits below the floor —
the locked fix for the legacy universal-cosine-gate bug.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from another_brain.config import BM25_WEIGHTS, CANDIDATE_LIMIT
from another_brain.domain.models import RecentFilters
from another_brain.errors import ValidationError
from another_brain.protocols import ScopeKey
from another_brain.retrieval.fusion import BranchCandidate
from another_brain.retrieval.query import scoped_live_where

_WEIGHT_TOPIC, _WEIGHT_SUMMARY, _WEIGHT_CONTENT = BM25_WEIGHTS


@dataclass(frozen=True)
class LexicalCandidate(BranchCandidate):
    """Lexical branch candidate; ``bm25`` kept for diagnostics."""

    bm25: float


class SQLiteLexicalRetriever:
    """Weighted-BM25 candidate source over one open connection."""

    def __init__(self, con: sqlite3.Connection, *, brain_id: str) -> None:
        self._con = con
        self._brain_id = brain_id

    def candidates(
        self,
        *,
        match_query: str,
        scope: ScopeKey,
        filters: RecentFilters | None = None,
        now_ms: int,
        limit: int = CANDIDATE_LIMIT,
    ) -> list[LexicalCandidate]:
        """Filtered, weighted, deterministically ordered candidates.

        ``match_query`` must come from
        :func:`~another_brain.retrieval.query.build_match_query` — raw user
        text is never accepted here.
        """
        if not match_query:
            raise ValidationError("match_query must be a non-empty safe FTS5 query")
        if limit < 1:
            raise ValidationError(f"candidate limit must be >= 1, got {limit}")
        where, params = scoped_live_where(
            brain_id=self._brain_id, scope=scope, filters=filters, now_ms=now_ms
        )
        rows = self._con.execute(
            "SELECT m.memory_id, bm25(memory_fts, ?, ?, ?) AS score"
            " FROM memory_fts JOIN memories m ON m.row_id = memory_fts.rowid"
            f" WHERE memory_fts MATCH ? AND {where}"
            " ORDER BY score ASC, m.memory_id ASC LIMIT ?",
            (
                _WEIGHT_TOPIC, _WEIGHT_SUMMARY, _WEIGHT_CONTENT,
                match_query, *params, limit,
            ),
        ).fetchall()
        return [
            LexicalCandidate(memory_id=memory_id, rank=rank, bm25=score)
            for rank, (memory_id, score) in enumerate(rows, 1)
        ]
