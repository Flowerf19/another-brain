"""RedisIndexManager — FT.CREATE / FT.INFO verification and startup safety
checks (Step 04 sections 3, 5.6).

The server refuses to start on an index/config mismatch (embedding DIM);
it never silently creates or mutates a mismatched index.
"""
from __future__ import annotations

import logging
from typing import Any

from errors import MigrationRequiredError
from storage.redis_keys import RedisKeyBuilder

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 1


def _flatten_info(seq: Any) -> list[Any]:
    """Flatten one FT.INFO reply chunk: RESP3 dict -> [k, v, ...] pairs,
    RESP2 sequence -> list(seq) unchanged (march7 diary/schema.py shape)."""
    if isinstance(seq, dict):
        flat: list[Any] = []
        for key, value in seq.items():
            flat.append(key)
            flat.append(value)
        return flat
    return list(seq)


def extract_indexed_dim(info: Any) -> int | None:
    """Best-effort VECTOR field DIM from an FT.INFO reply (RESP2 or RESP3).
    Returns None on any unrecognized shape rather than raising."""
    try:
        top = _flatten_info(info)
        for i, key in enumerate(top):
            key_str = key.decode() if isinstance(key, bytes) else key
            if key_str == "attributes" and i + 1 < len(top):
                for attr in top[i + 1]:
                    fields = _flatten_info(attr)
                    field_map: dict[str, Any] = {}
                    for j in range(0, len(fields) - 1, 2):
                        fk = fields[j]
                        fk = fk.decode() if isinstance(fk, bytes) else fk
                        field_map[fk] = fields[j + 1]
                    field_type = field_map.get("type")
                    if isinstance(field_type, bytes):
                        field_type = field_type.decode()
                    if field_type == "VECTOR" and "dim" in field_map:
                        return int(field_map["dim"])
        return None
    except Exception:
        return None


class RedisIndexManager:
    """Creates and verifies the one global RediSearch index (Step 04 §2.3)."""

    def __init__(
        self,
        redis: Any,
        keys: RedisKeyBuilder,
        *,
        embedding_dim: int,
        embedding_model: str = "",
        vector_dtype: str = "FLOAT32",
        distance_metric: str = "COSINE",
        index_mode: str = "HNSW",
    ):
        self._redis = redis
        self._keys = keys
        self._dim = embedding_dim
        self._embedding_model = embedding_model
        self._dtype = vector_dtype
        self._metric = distance_metric
        self._mode = index_mode

    # ------------------------------------------------------------- startup

    async def ensure(self) -> None:
        """Startup safety checks (Step 04 §5.6): verify or create the index,
        then record the active contract in the meta key."""
        info = None
        try:
            info = await self._redis.execute_command("FT.INFO", self._keys.index_name)
        except Exception:
            pass

        if info is not None:
            indexed_dim = extract_indexed_dim(info)
            if indexed_dim is not None and indexed_dim != self._dim:
                raise MigrationRequiredError(
                    f"index {self._keys.index_name!r} has vector DIM={indexed_dim} "
                    f"but EMBEDDING_DIM={self._dim} — a reindex is required "
                    f"(Step 04 §5.2); refusing to start"
                )
            logger.info("Index %s exists (dim=%s)", self._keys.index_name, indexed_dim)
        else:
            await self._create_index()

        await self._write_meta()

    async def _create_index(self) -> None:
        vector_args = [
            "TYPE", self._dtype,
            "DIM", str(self._dim),
            "DISTANCE_METRIC", self._metric,
        ]
        try:
            await self._redis.execute_command(
                "FT.CREATE", self._keys.index_name,
                "ON", "HASH",
                "PREFIX", "1", self._keys.memory_prefix,
                "SCHEMA",
                "brain_id",     "TAG",
                "scope",        "TAG",
                "scope_id",     "TAG",
                "topic",        "TAG",
                "catalog",      "TAG",
                "timeline_day", "TAG",
                "summary",      "TEXT", "NOSTEM",
                "content",      "TEXT", "NOSTEM",
                "importance",   "NUMERIC", "SORTABLE",
                "period_start", "NUMERIC", "SORTABLE",
                "period_end",   "NUMERIC", "SORTABLE",
                "created_at",   "NUMERIC", "SORTABLE",
                "deleted_at",   "NUMERIC",
                "embedding",    "VECTOR", self._mode, str(len(vector_args)),
                *vector_args,
            )
            logger.info(
                "Created index %s (%s, dim=%d)",
                self._keys.index_name, self._mode, self._dim,
            )
        except Exception as exc:
            # Tolerate a concurrent creator (Step 04 §3.3 point 4).
            if "already exists" in str(exc).lower():
                logger.info("Index %s already exists — reusing", self._keys.index_name)
                return
            raise

    # ---------------------------------------------------------------- meta

    async def _write_meta(self) -> None:
        """ab:idx:meta is the ONLY place embedding model/dim are recorded
        (Step 04 §2.4)."""
        await self._redis.hset(
            self._keys.meta_key,
            mapping={
                "index_version": INDEX_SCHEMA_VERSION,
                "embedding_model": self._embedding_model,
                "embedding_dim": self._dim,
                "vector_dtype": self._dtype,
                "distance_metric": self._metric,
                "index_mode": self._mode,
            },
        )

    async def read_meta(self) -> dict[str, str]:
        raw = await self._redis.hgetall(self._keys.meta_key)
        meta: dict[str, str] = {}
        for key, value in raw.items():
            k = key.decode() if isinstance(key, bytes) else key
            v = value.decode() if isinstance(value, bytes) else value
            meta[k] = str(v)
        return meta

    async def drop(self, *, delete_documents: bool = False) -> None:
        """Admin/migration helper (Step 04 §5.5). Never called at startup."""
        args = ["FT.DROPINDEX", self._keys.index_name]
        if delete_documents:
            args.append("DD")
        await self._redis.execute_command(*args)
