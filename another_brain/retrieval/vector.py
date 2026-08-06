"""Exact cosine vector candidate source (TASK-058) + NumPy fallback (TASK-059).

Two adapters, one canonical contract:

- ``SQLiteVecVectorRetriever`` — scalar ``vec_distance_cosine`` over the
  filtered regular BLOBs (no ANN index, no ``vec0``), used when the
  connection loaded sqlite-vec;
- ``NumpyVectorRetriever`` — streaming exact scan over the SAME filtered
  BLOBs, the compatibility fallback when the extension is unavailable.

Both return finite FLOAT32 cosine converted to the canonical integer
micro-cosine ``cosine_key = round(float(score) * 1_000_000)`` (Python
half-even :func:`round`) and apply the locked floor ``cosine_key >= 300000``
before one-based ranking by ``cosine_key DESC, memory_id ASC`` and the fixed
50-candidate limit. Malformed rows (wrong-length blob), non-finite vectors,
and zero-norm vectors are rejected as candidates — never scored, never
raised. sqlite-vec reports those as a NULL distance; NumPy detects them
directly, so both adapters reject the same rows.

Parity contract: identical filtered input IDs, candidate IDs, order, keys,
and ranks; raw scores may differ by at most ``1e-6`` (FLOAT32 accumulation
inside the extension) — never enough to change a canonical key away from a
rounding boundary on the judged fixtures.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

import numpy as np

from another_brain.config import CANDIDATE_LIMIT, COSINE_FLOOR_MICRO
from another_brain.domain.models import EmbeddingVector, RecentFilters
from another_brain.errors import ValidationError
from another_brain.retrieval.fusion import BranchCandidate
from another_brain.retrieval.query import live_where

_EMBEDDING_BYTES = 2560  # 640 x FLOAT32-LE (schema CHECK)


def micro_cosine_key(score: float) -> int:
    """Canonical integer micro-cosine via Python half-even rounding."""
    return round(float(score) * 1_000_000)


@dataclass(frozen=True)
class VectorCandidate(BranchCandidate):
    """Vector branch candidate with canonical key + raw score for parity."""

    cosine_key: int
    raw_cosine: float


def _validate_query(query_vector: EmbeddingVector) -> np.ndarray:
    values = np.asarray(query_vector.values, dtype=np.float64)
    if values.shape != (640,):
        raise ValidationError(
            f"query vector must be FLOAT32[640], got shape {values.shape}"
        )
    if not np.all(np.isfinite(values)):
        raise ValidationError("query vector must be finite")
    if float(np.linalg.norm(values)) == 0.0:
        raise ValidationError("query vector must be non-zero")
    return values


def _canonicalize(
    scored: list[tuple[str, float]],
    *,
    limit: int,
) -> list[VectorCandidate]:
    """Floor, canonical key, deterministic order, one-based ranks."""
    passed = [
        (memory_id, micro_cosine_key(cosine), cosine)
        for memory_id, cosine in scored
        if math.isfinite(cosine) and micro_cosine_key(cosine) >= COSINE_FLOOR_MICRO
    ]
    passed.sort(key=lambda item: (-item[1], item[0]))
    return [
        VectorCandidate(
            memory_id=memory_id, rank=rank, cosine_key=key, raw_cosine=cosine
        )
        for rank, (memory_id, key, cosine) in enumerate(passed[:limit], 1)
    ]


class _VectorBase:
    def __init__(self, con: sqlite3.Connection, *, brain_id: str) -> None:
        self._con = con
        self._brain_id = brain_id

    def _where(
        self,
        filters: RecentFilters | None,
        now_ms: int,
        limit: int,
    ) -> tuple[str, list[object]]:
        if limit < 1:
            raise ValidationError(f"candidate limit must be >= 1, got {limit}")
        return live_where(brain_id=self._brain_id, filters=filters, now_ms=now_ms)


class SQLiteVecVectorRetriever(_VectorBase):
    """Exact cosine via the sqlite-vec scalar function on regular BLOBs."""

    def candidates(
        self,
        *,
        query_vector: EmbeddingVector,
        filters: RecentFilters | None = None,
        now_ms: int,
        limit: int = CANDIDATE_LIMIT,
    ) -> list[VectorCandidate]:
        query = np.asarray(query_vector.values, dtype="<f4")
        _validate_query(query_vector)
        where, params = self._where(filters, now_ms, limit)
        rows = self._con.execute(
            "SELECT m.memory_id, vec_distance_cosine(m.embedding, ?) AS distance"
            f" FROM memories m WHERE {where}",
            (query.tobytes(), *params),
        ).fetchall()
        # NULL distance = malformed/non-finite/zero-norm row: rejected.
        scored = [
            (memory_id, 1.0 - float(distance))
            for memory_id, distance in rows
            if distance is not None
        ]
        return _canonicalize(scored, limit=limit)


class NumpyVectorRetriever(_VectorBase):
    """Streaming exact cosine over the same filtered BLOBs (fallback).

    Scores one row at a time on purpose. Stacking the BLOBs into a single
    ``(N, 640)`` matmul is ~1.26x faster on the judged 100k store (17.2 ms
    vs 21.6 ms) but allocates the whole candidate set at once: 43 MB there,
    and 917 MB when every live row of the whole brain is a candidate — over
    the 500 MiB RSS budget. This adapter is the fallback for platforms where
    the extension will not load, so bounded memory outranks the 4 ms.
    """

    def candidates(
        self,
        *,
        query_vector: EmbeddingVector,
        filters: RecentFilters | None = None,
        now_ms: int,
        limit: int = CANDIDATE_LIMIT,
    ) -> list[VectorCandidate]:
        query = _validate_query(query_vector)
        where, params = self._where(filters, now_ms, limit)
        rows = self._con.execute(
            f"SELECT m.memory_id, m.embedding FROM memories m WHERE {where}",
            params,
        ).fetchall()
        scored: list[tuple[str, float]] = []
        query_norm = float(np.linalg.norm(query))
        for memory_id, blob in rows:
            if blob is None or len(blob) != _EMBEDDING_BYTES:
                continue  # malformed row: rejected, never scored
            values = np.frombuffer(blob, dtype="<f4").astype(np.float64)
            norm = float(np.linalg.norm(values))
            if norm == 0.0 or not math.isfinite(norm):
                continue  # zero-norm/non-finite row: rejected
            cosine = float(values @ query) / (norm * query_norm)
            scored.append((memory_id, cosine))
        return _canonicalize(scored, limit=limit)
