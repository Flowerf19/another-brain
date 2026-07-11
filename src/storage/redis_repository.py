"""RedisMemoryRepository and RedisMemoryMapper — Redis implementation
of the memory store (Step 04 sections 1.7, 2, 4.2, 6).

Query shapes, TAG escaping, and RESP2/RESP3 reply parsing inherit march7
diary/codec.py + diary/store.py, which are production-proven. Hybrid search
runs as a single native FT.HYBRID call (Redis 8.4+, step-05 explainer)
instead of the two-query KNN + BM25 flow the contract was written against.
"""
from __future__ import annotations

import json
import logging
import re
import struct
from dataclasses import dataclass, replace
from typing import Any

from errors import ValidationError
from memory.models import (
    EmbeddingVector,
    MemoryIdentity,
    MemoryRecord,
    SearchFilters,
)
from memory.retention import RetentionPolicy
from redis.exceptions import RedisError
from storage.redis_keys import RedisKeyBuilder

logger = logging.getLogger(__name__)

# Search paths degrade to empty results only on Redis I/O failures; anything
# else (parse bugs, programming errors) must propagate — a swallowed bug is
# indistinguishable from "no matching memories" to the calling agent.
_REDIS_IO_ERRORS = (RedisError, OSError)

_INT_FIELDS = frozenset({"importance", "schema_version"})
_FLOAT_FIELDS = frozenset(
    {
        "period_start", "period_end", "created_at", "updated_at", "deleted_at",
        "score", "text_score", "vector_score", "fused_score",
    }
)

# Every query excludes soft-deleted records at the index level (Step 04 §1.7):
# a record missing deleted_at matches the negation; a record having it never
# does. No app-layer double filtering.
_NOT_DELETED = "(-@deleted_at:[-inf +inf])"

# BM25 sanitizer keeps Latin + Vietnamese ranges (march7 _search_bm25).
_BM25_STRIP_RE = re.compile(r"[^a-zA-Z0-9\sÀ-ɏẠ-ỹ]")


def sanitize_terms(query_text: str) -> list[str]:
    """Free text -> safe BM25 tokens. Terms are OR-joined by callers: lexical
    recall wants any-keyword-matches, not all-words-present (march7 lesson —
    AND semantics silently degrade hybrid to KNN-only)."""
    safe = _BM25_STRIP_RE.sub(" ", query_text or "").strip()
    return [t for t in safe.split() if t]


def pack_embedding(values: Any) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def unpack_embedding(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value]
    if not isinstance(value, (bytes, bytearray)):
        return []
    count = len(value) // 4
    return list(struct.unpack(f"{count}f", value[: count * 4]))


def escape_tag_value(value: str) -> str:
    """Backslash-escape every non-word char for TAG queries — '-' in
    timeline_day or brain_id breaks unescaped TAG syntax."""
    return re.sub(r"([^\w])", r"\\\1", value)


def decode_fields(mapping: Any) -> dict[str, Any]:
    """Decode a Redis hash reply to native types.

    Accepts a dict OR the flat RESP2 ``[k1, v1, k2, v2, ...]`` pair list
    (march7 fix B8). The embedding field is unpacked from raw bytes BEFORE
    any utf-8 decode attempt — packed floats can accidentally be valid
    utf-8, which would silently turn the vector into an empty list.
    """
    if isinstance(mapping, (list, tuple)):
        pairs = list(zip(mapping[0::2], mapping[1::2]))
    else:
        pairs = list(mapping.items())
    result: dict[str, Any] = {}
    for raw_key, raw_value in pairs:
        name = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        if name == "embedding":
            result[name] = unpack_embedding(raw_value)
            continue
        try:
            value: Any = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
        except UnicodeDecodeError:
            value = raw_value
        if name in _INT_FIELDS:
            try:
                value = int(value)
            except (TypeError, ValueError):
                pass
        elif name in _FLOAT_FIELDS:
            try:
                value = float(value)
            except (TypeError, ValueError):
                pass
        result[name] = value
    return result


@dataclass(frozen=True)
class RedisSearchHit:
    """One raw search hit: the record, its stored embedding (needed for the
    app-layer cosine gate, including BM25-only docs — march7 fix B3), and
    the engine score (KNN: cosine distance, lower is better; BM25: relevance,
    higher is better)."""

    record: MemoryRecord
    embedding: tuple[float, ...]
    score: float | None


@dataclass(frozen=True)
class HybridHit:
    """One raw FT.HYBRID hit, in Redis fusion order (ungated).

    Carries exactly the preview fields plus what the cosine gate needs: the
    stored embedding, and the per-branch scores. A missing branch score means
    Redis never ranked the doc on that branch — in particular a BM25-only doc
    has vector_score=None and its cosine must be computed client-side.
    """

    memory_id: str
    topic: str
    catalog: str
    summary: str
    timeline_day: str
    importance: int
    has_content: bool
    embedding: tuple[float, ...]
    text_score: float | None
    vector_score: float | None
    fused_score: float


class RedisMemoryMapper:
    """MemoryRecord <-> Redis HASH fields (Step 04 §1.7)."""

    def __init__(self, keys: RedisKeyBuilder):
        self._keys = keys

    def record_to_hash(
        self, record: MemoryRecord, embedding: EmbeddingVector
    ) -> dict[str, Any]:
        mapping: dict[str, Any] = {
            "brain_id": record.identity.brain_id,
            "agent_id": record.identity.agent_id,
            "scope": record.identity.scope.value,
            "scope_id": record.identity.scope_id,
            "topic": record.topic,
            "catalog": record.catalog,
            "summary": record.summary,
            "content": record.content,
            "timeline_day": record.timeline_day,
            "period_start": record.period_start,
            "period_end": record.period_end,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "importance": record.importance,
            "metadata": json.dumps(record.metadata, ensure_ascii=False),
            "schema_version": record.schema_version,
            "embedding": pack_embedding(embedding.values),
        }
        # deleted_at is ABSENT on live records — its mere presence excludes
        # the record from every query (Step 04 §1.7).
        if record.deleted_at is not None:
            mapping["deleted_at"] = record.deleted_at
        return mapping

    def hash_to_record(self, key: str, fields: dict[str, Any]) -> MemoryRecord:
        """memory_id is not a hash field — it is derived from the key."""
        _, memory_id = self._keys.parse_memory_key(key)
        try:
            metadata_raw = fields.get("metadata") or "{}"
            metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            identity = MemoryIdentity(
                memory_id=memory_id,
                brain_id=fields["brain_id"],
                agent_id=fields["agent_id"],
                scope=fields["scope"],
                scope_id=fields["scope_id"],
            )
            return MemoryRecord(
                identity=identity,
                topic=fields["topic"],
                summary=fields["summary"],
                timeline_day=fields["timeline_day"],
                period_start=fields["period_start"],
                period_end=fields["period_end"],
                created_at=fields["created_at"],
                updated_at=fields["updated_at"],
                catalog=fields.get("catalog") or "note",
                content=fields.get("content") or "",
                importance=fields.get("importance", 3),
                metadata=metadata,
                deleted_at=fields.get("deleted_at"),
                schema_version=fields.get("schema_version", 1),
            )
        except KeyError as exc:
            raise ValidationError(f"hash {key!r} is missing field {exc}") from None


class RedisMemoryRepository:
    """Async Redis repository: HASH read/write, TTL, soft delete /
    restore / hard delete, reinforce re-arm, and the Step 04 §6 queries."""

    def __init__(
        self,
        redis: Any,
        keys: RedisKeyBuilder,
        retention: RetentionPolicy,
        *,
        grace_seconds: int,
        embedding_dim: int,
    ):
        self._redis = redis
        self._keys = keys
        self._mapper = RedisMemoryMapper(keys)
        self._retention = retention
        self._grace = grace_seconds
        self._dim = embedding_dim

    # ---------------------------------------------------------------- write

    async def store(self, record: MemoryRecord, embedding: EmbeddingVector) -> None:
        """Append one memory: HSET + EXPIRE by importance (Step 04 §4.2.1).
        Always appends — there is no merge (§6.6)."""
        if embedding.dim != self._dim:
            raise ValidationError(
                f"embedding dim mismatch — got {embedding.dim}, expected {self._dim}"
            )
        key = self._key(record.identity.brain_id, record.identity.memory_id)
        await self._redis.hset(key, mapping=self._mapper.record_to_hash(record, embedding))
        await self._redis.expire(key, self._retention.ttl_seconds(record.importance))

    # ----------------------------------------------------------------- read

    async def get(self, brain_id: str, memory_id: str) -> MemoryRecord | None:
        """Pure read — NEVER refreshes TTL (Step 04 §4.2.6). Soft-deleted
        records are still returned (the service layer decides; search never
        surfaces them)."""
        fields = decode_fields(await self._redis.hgetall(self._key(brain_id, memory_id)))
        if not fields:
            return None
        return self._mapper.hash_to_record(self._key(brain_id, memory_id), fields)

    async def expire_at(self, brain_id: str, memory_id: str) -> int | None:
        """Display expiry derives from EXPIRETIME at read time — there is no
        stored expiry field (Step 04 §4.2.7)."""
        ts = await self._redis.execute_command(
            "EXPIRETIME", self._key(brain_id, memory_id)
        )
        return int(ts) if ts is not None and int(ts) > 0 else None

    # ------------------------------------------------------------ lifecycle

    async def reinforce(
        self, brain_id: str, memory_id: str, *, now_ts: float
    ) -> MemoryRecord | None:
        """The ONLY TTL renewal (Step 04 §4.2.2): re-apply the full importance
        TTL and bump updated_at. Refuses soft-deleted records."""
        record = await self.get(brain_id, memory_id)
        if record is None or record.is_deleted:
            return None
        key = self._key(brain_id, memory_id)
        await self._redis.hset(key, mapping={"updated_at": now_ts})
        await self._redis.expire(key, self._retention.ttl_seconds(record.importance))
        return replace(record, updated_at=float(now_ts))

    async def soft_delete(
        self, brain_id: str, memory_id: str, *, now_ts: float
    ) -> bool:
        """brain_forget (Step 04 §4.2.3): set deleted_at and shrink the TTL
        to the grace window — never extend a shorter remaining TTL."""
        key = self._key(brain_id, memory_id)
        remaining = await self._redis.ttl(key)
        if remaining == -2:
            return False
        await self._redis.hset(key, mapping={"deleted_at": now_ts})
        if remaining == -1 or remaining > self._grace:
            await self._redis.expire(key, self._grace)
        return True

    async def restore(self, brain_id: str, memory_id: str) -> MemoryRecord | None:
        """Admin restore within the grace window (Step 04 §4.2.4): clear
        deleted_at and re-apply the importance TTL."""
        record = await self.get(brain_id, memory_id)
        if record is None:
            return None
        key = self._key(brain_id, memory_id)
        await self._redis.hdel(key, "deleted_at")
        await self._redis.expire(key, self._retention.ttl_seconds(record.importance))
        return replace(record, deleted_at=None)

    async def hard_delete(self, brain_id: str, memory_id: str) -> bool:
        """Admin-only DEL (Step 04 §4.2.5)."""
        return bool(await self._redis.delete(self._key(brain_id, memory_id)))

    # --------------------------------------------------------------- health

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except _REDIS_IO_ERRORS as exc:
            logger.warning("Redis ping failed: %s", exc)
            return False

    # --------------------------------------------------------------- search

    async def knn_search(
        self,
        brain_id: str,
        filters: SearchFilters,
        query_embedding: EmbeddingVector,
        limit: int,
    ) -> list[RedisSearchHit]:
        """Raw KNN hits, ungated — the search engine applies the cosine
        floor (Step 04 §6.1)."""
        expr = self._filter_expr(brain_id, filters)
        query = f"({expr})=>[KNN {int(limit)} @embedding $vec AS score]"
        try:
            reply = await self._redis.execute_command(
                "FT.SEARCH", self._keys.index_name,
                query,
                "PARAMS", "2", "vec", pack_embedding(query_embedding.values),
                "SORTBY", "score", "ASC",
                "LIMIT", "0", str(int(limit)),
                "DIALECT", "2",
            )
        except _REDIS_IO_ERRORS as exc:
            logger.error("KNN search failed: %s", exc)
            return []
        return self._hits(reply, score_in_fields=True, has_scores=False)

    async def hybrid_search(
        self,
        brain_id: str,
        filters: SearchFilters,
        query_text: str,
        query_embedding: EmbeddingVector,
        *,
        knn_k: int,
        window: int,
        fusion_constant: int,
        limit: int,
    ) -> list[HybridHit]:
        """One FT.HYBRID round trip: BM25 + KNN + RRF fused inside Redis
        (step-05 explainer), replacing the two-query §6.1/§6.2 flow.

        The mandatory filters are applied to BOTH branches: inside the SEARCH
        query string and as the VSIM FILTER — without the VSIM FILTER the
        vector branch ignores them and other brains / soft-deleted records
        leak into the fused results (verified on 8.8). FILTER takes full
        FT.SEARCH syntax and must sit between the KNN block and its
        YIELD_SCORE_AS.

        Hits come back ungated, in fusion order — the search engine applies
        the cosine floor and the final limit (gate-before-limit).
        """
        terms = sanitize_terms(query_text)
        if not terms:
            raise ValidationError("hybrid search needs at least one text term")
        expr = self._filter_expr(brain_id, filters)
        search_query = f"({expr}) (@summary|content:({' | '.join(terms)}))"
        try:
            reply = await self._redis.execute_command(
                "FT.HYBRID", self._keys.index_name,
                "SEARCH", search_query,
                "YIELD_SCORE_AS", "text_score",
                "VSIM", "@embedding", "$vec",
                "KNN", "2", "K", str(int(knn_k)),
                "FILTER", f"({expr})",
                "YIELD_SCORE_AS", "vector_score",
                "COMBINE", "RRF", "6",
                "CONSTANT", str(int(fusion_constant)),
                "WINDOW", str(int(window)),
                "YIELD_SCORE_AS", "fused_score",
                "LOAD", "8",
                "@__key", "@topic", "@catalog", "@summary", "@timeline_day",
                "@importance", "@content", "@embedding",
                "LIMIT", "0", str(int(limit)),
                "PARAMS", "2", "vec", pack_embedding(query_embedding.values),
            )
        except _REDIS_IO_ERRORS as exc:
            logger.error("Hybrid search failed: %s", exc)
            return []
        return self._hybrid_hits(reply)

    async def recent(
        self,
        brain_id: str,
        filters: SearchFilters,
        limit: int,
    ) -> list[RedisSearchHit]:
        """Timeline listing: pure filter + SORTBY period_start DESC
        (Step 04 §6.3)."""
        query = f"({self._filter_expr(brain_id, filters)})"
        try:
            reply = await self._redis.execute_command(
                "FT.SEARCH", self._keys.index_name,
                query,
                "SORTBY", "period_start", "DESC",
                "LIMIT", "0", str(int(limit)),
                "DIALECT", "2",
            )
        except _REDIS_IO_ERRORS as exc:
            logger.error("Recent query failed: %s", exc)
            return []
        return self._hits(reply, score_in_fields=False, has_scores=False)

    # -------------------------------------------------------------- helpers

    def _key(self, brain_id: str, memory_id: str) -> str:
        return self._keys.memory_key(brain_id, memory_id)

    @staticmethod
    def _filter_expr(brain_id: str, filters: SearchFilters) -> str:
        """brain_id, scope, scope_id, and the soft-delete exclusion are always
        present; topic/catalog/timeline_day/min_importance/time range are
        optional (Step 04 §3.2)."""
        clauses = [
            f"@brain_id:{{{escape_tag_value(brain_id)}}}",
            f"@scope:{{{escape_tag_value(filters.scope.value)}}}",
            f"@scope_id:{{{escape_tag_value(filters.scope_id)}}}",
        ]
        if filters.topic:
            clauses.append(f"@topic:{{{escape_tag_value(filters.topic)}}}")
        if filters.catalog:
            clauses.append(f"@catalog:{{{escape_tag_value(filters.catalog)}}}")
        if filters.timeline_day:
            clauses.append(f"@timeline_day:{{{escape_tag_value(filters.timeline_day)}}}")
        if filters.min_importance is not None:
            clauses.append(f"@importance:[{filters.min_importance} +inf]")
        if filters.since_ts is not None or filters.until_ts is not None:
            lo = filters.since_ts if filters.since_ts is not None else "-inf"
            hi = filters.until_ts if filters.until_ts is not None else "+inf"
            clauses.append(f"@period_start:[{lo} {hi}]")
        clauses.append(_NOT_DELETED)
        return " ".join(clauses)

    def _hits(
        self, reply: Any, *, score_in_fields: bool, has_scores: bool
    ) -> list[RedisSearchHit]:
        hits: list[RedisSearchHit] = []
        for key, doc_score, fields in _parse_search_reply(reply, has_scores=has_scores):
            embedding = tuple(fields.pop("embedding", []) or [])
            if score_in_fields:
                raw = fields.get("score")
                score = float(raw) if raw is not None else None
            else:
                score = doc_score
            try:
                record = self._mapper.hash_to_record(key, fields)
            except ValidationError as exc:
                logger.warning("Skipping malformed hit %r: %s", key, exc)
                continue
            hits.append(RedisSearchHit(record=record, embedding=embedding, score=score))
        return hits

    def _hybrid_hits(self, reply: Any) -> list[HybridHit]:
        hits: list[HybridHit] = []
        for fields in _parse_hybrid_reply(reply):
            key = fields.get("__key")
            try:
                _, memory_id = self._keys.parse_memory_key(key)
                hits.append(
                    HybridHit(
                        memory_id=memory_id,
                        topic=fields["topic"],
                        catalog=fields.get("catalog") or "note",
                        summary=fields["summary"],
                        timeline_day=fields["timeline_day"],
                        importance=int(fields.get("importance", 3)),
                        has_content=bool(fields.get("content")),
                        embedding=tuple(fields.get("embedding") or []),
                        text_score=_opt_float(fields.get("text_score")),
                        vector_score=_opt_float(fields.get("vector_score")),
                        fused_score=float(fields.get("fused_score", 0.0)),
                    )
                )
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed hybrid hit %r: %s", key, exc)
        return hits


def _opt_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_hybrid_reply(reply: Any) -> list[dict[str, Any]]:
    """Parse an FT.HYBRID reply into decoded per-row field dicts.

    redis-py >= 7.1 normalizes both protocols to a map shape
    ``{total_results, results: [row, ...]}`` where each row is a flat
    attribute map (no id/extra_attributes nesting, unlike FT.SEARCH).
    The RESP2 flat-array shape ``[count, [k, v, ...], ...]`` is handled
    defensively.
    """
    if isinstance(reply, dict):
        raw = reply.get(b"results")
        if raw is None:
            raw = reply.get("results") or []
        return [decode_fields(row) for row in raw]
    if isinstance(reply, (list, tuple)):
        return [
            decode_fields(row)
            for row in reply[1:]
            if isinstance(row, (list, tuple, dict))
        ]
    return []


def _parse_search_reply(
    reply: Any, *, has_scores: bool
) -> list[tuple[str, float | None, dict[str, Any]]]:
    """Parse an FT.SEARCH reply into (key, doc_score, fields) tuples.

    Handles the RESP3 dict shape and the RESP2 flat list shape
    ``[count, key, (score,) [fields...], ...]`` (march7 parse_results).
    """
    out: list[tuple[str, float | None, dict[str, Any]]] = []

    if isinstance(reply, dict):
        raw = reply.get(b"results") or reply.get("results") or []
        for item in raw:
            key = item.get(b"id") or item.get("id")
            if not key:
                continue
            key_str = key.decode() if isinstance(key, bytes) else key
            score_raw = item.get(b"score") or item.get("score")
            try:
                score = float(score_raw) if score_raw is not None else None
            except (TypeError, ValueError):
                score = None
            extra = item.get(b"extra_attributes") or item.get("extra_attributes") or {}
            out.append((key_str, score, decode_fields(extra)))
        return out

    i = 1
    while i < len(reply):
        key = reply[i]
        i += 1
        score: float | None = None
        if has_scores and i < len(reply) and not isinstance(reply[i], (list, tuple)):
            try:
                score = float(reply[i])
                i += 1
            except (TypeError, ValueError):
                pass
        if i < len(reply) and isinstance(reply[i], (list, tuple)):
            fields = reply[i]
            i += 1
        else:
            continue
        key_str = key.decode() if isinstance(key, bytes) else key
        out.append((key_str, score, decode_fields(fields)))
    return out
