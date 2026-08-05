"""SQLite ``MemoryRepository`` — append-only store/get/recent (TASK-050).

Bound to one ``brain_id`` at construction. Collection operations read the
bound brain; by-ID operations key on ``(bound brain_id, memory_id)``. Live
reads exclude expired/soft-deleted rows before any limit. ``row + FTS``
commit atomically inside one short ``BEGIN IMMEDIATE`` transaction with the
bounded busy retry envelope; a duplicate ``(brain_id, memory_id)`` is a
typed integrity error, never an overwrite.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Sequence

import numpy as np

from another_brain.domain.models import EmbeddingVector, MemoryRecord, RecentFilters
from another_brain.errors import DuplicateMemoryError, StorageError, ValidationError
from another_brain.protocols import MutationOutcome
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.retry import busy_retry
from another_brain.services.sql.ttl import GRACE_MS, ttl_ms_for

_MEMORY_COLUMNS = (
    "memory_id", "brain_id", "agent_id", "topic",
    "catalog", "summary", "content", "timeline_day", "period_start_ms",
    "period_end_ms", "created_at_ms", "updated_at_ms", "importance",
    "expires_at_ms", "deleted_at_ms", "metadata", "profile_id",
    "embedding", "record_version",
)
_SELECT_COLUMNS = ", ".join(_MEMORY_COLUMNS)
_PLACEHOLDERS = ", ".join("?" for _ in _MEMORY_COLUMNS)
_LIVE = "deleted_at_ms IS NULL AND expires_at_ms > ?"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row_to_record(row: Sequence) -> MemoryRecord:
    values = dict(zip(_MEMORY_COLUMNS, row))
    try:
        values["metadata"] = json.loads(values["metadata"])
    except (TypeError, ValueError) as exc:
        raise StorageError(
            f"corrupt metadata JSON in row {values['memory_id']!r}: {exc}"
        ) from exc
    blob = values["embedding"]
    if blob is None or len(blob) != 2560:
        raise StorageError(
            f"corrupt embedding blob in row {values['memory_id']!r}:"
            f" {0 if blob is None else len(blob)} bytes"
        )
    values["embedding"] = EmbeddingVector(
        values=np.frombuffer(blob, dtype="<f4").copy()
    )
    return MemoryRecord(**values)


class SQLiteMemoryRepository:
    """Append-only memory persistence over the v1 schema, one bound brain."""

    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        *,
        brain_id: str,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        # Fail-fast open contract: an unmigrated, partial, or missing schema
        # is a typed error here, not a late OperationalError on first query.
        factory.verify_schema()
        self._factory = factory
        self._brain_id = brain_id
        self._clock = clock

    # -- writes --------------------------------------------------------------

    def store(self, record: MemoryRecord) -> None:
        """Append one record + FTS row atomically; duplicates are errors."""
        if record.brain_id != self._brain_id:
            raise ValidationError(
                f"record brain_id {record.brain_id!r} does not match bound"
                f" brain {self._brain_id!r}"
            )
        if record.embedding is None:
            raise ValidationError("store requires an embedding; embed before store")
        try:
            metadata_json = json.dumps(
                record.metadata, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"metadata must be JSON-serializable: {exc}"
            ) from exc
        blob = record.embedding.values.astype("<f4", copy=False).tobytes()

        with self._factory.connect() as con:
            raw = con.connection

            def _tx() -> None:
                raw.execute("BEGIN IMMEDIATE")
                try:
                    raw.execute(
                        f"INSERT INTO memories({_SELECT_COLUMNS})"
                        f" VALUES ({_PLACEHOLDERS})",
                        (
                            record.memory_id, record.brain_id, record.agent_id,
                            record.topic,
                            record.catalog, record.summary, record.content,
                            record.timeline_day, record.period_start_ms,
                            record.period_end_ms, record.created_at_ms,
                            record.updated_at_ms, record.importance,
                            record.expires_at_ms, record.deleted_at_ms,
                            metadata_json, record.profile_id, blob,
                            record.record_version,
                        ),
                    )
                    raw.commit()
                except Exception:
                    raw.rollback()
                    raise

            try:
                busy_retry(_tx)
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed: memories.brain_id, memories.memory_id" in str(exc):
                    raise DuplicateMemoryError(
                        f"memory {record.memory_id!r} already exists for brain"
                        f" {self._brain_id!r}"
                    ) from exc
                raise

    # -- lifecycle -----------------------------------------------------------

    def reinforce(self, memory_id: str) -> MutationOutcome:
        """Re-arm ``expires_at`` from importance for a live row."""
        now = self._clock()

        def _tx() -> MutationOutcome:
            raw.execute("BEGIN IMMEDIATE")
            try:
                row = raw.execute(
                    "SELECT importance FROM memories"
                    " WHERE brain_id = ? AND memory_id = ? AND deleted_at_ms IS NULL"
                    " AND expires_at_ms > ?",
                    (self._brain_id, memory_id, now),
                ).fetchone()
                if row is None:
                    raw.rollback()
                    return MutationOutcome.NOT_FOUND
                raw.execute(
                    "UPDATE memories SET expires_at_ms = ?"
                    " WHERE brain_id = ? AND memory_id = ?",
                    (now + ttl_ms_for(row[0]), self._brain_id, memory_id),
                )
                raw.commit()
                return MutationOutcome.APPLIED
            except Exception:
                raw.rollback()
                raise

        with self._factory.connect() as con:
            raw = con.connection
            return busy_retry(_tx)  # type: ignore[return-value]

    def soft_delete(self, memory_id: str) -> MutationOutcome:
        """Forget: mark deleted and clamp expiry to ``now + grace``, never extending."""
        now = self._clock()
        grace_expiry = now + GRACE_MS

        def _tx() -> MutationOutcome:
            raw.execute("BEGIN IMMEDIATE")
            try:
                row = raw.execute(
                    "SELECT expires_at_ms FROM memories"
                    " WHERE brain_id = ? AND memory_id = ? AND deleted_at_ms IS NULL"
                    " AND expires_at_ms > ?",
                    (self._brain_id, memory_id, now),
                ).fetchone()
                if row is None:
                    raw.rollback()
                    return MutationOutcome.NOT_FOUND
                current = row[0]
                raw.execute(
                    "UPDATE memories SET deleted_at_ms = ?, expires_at_ms = ?"
                    " WHERE brain_id = ? AND memory_id = ?",
                    (now, min(current, grace_expiry), self._brain_id, memory_id),
                )
                raw.commit()
                return MutationOutcome.APPLIED
            except Exception:
                raw.rollback()
                raise

        with self._factory.connect() as con:
            raw = con.connection
            return busy_retry(_tx)  # type: ignore[return-value]

    def restore(self, memory_id: str) -> MutationOutcome:
        """Undo a soft delete still inside its grace window; re-arm from importance."""
        now = self._clock()
        grace_cutoff = now - GRACE_MS

        def _tx() -> MutationOutcome:
            raw.execute("BEGIN IMMEDIATE")
            try:
                row = raw.execute(
                    "SELECT importance FROM memories"
                    " WHERE brain_id = ? AND memory_id = ? AND deleted_at_ms IS NOT NULL"
                    " AND deleted_at_ms > ?",
                    (self._brain_id, memory_id, grace_cutoff),
                ).fetchone()
                if row is None:
                    raw.rollback()
                    return MutationOutcome.NOT_FOUND
                raw.execute(
                    "UPDATE memories SET deleted_at_ms = NULL, expires_at_ms = ?"
                    " WHERE brain_id = ? AND memory_id = ?",
                    (now + ttl_ms_for(row[0]), self._brain_id, memory_id),
                )
                raw.commit()
                return MutationOutcome.APPLIED
            except Exception:
                raw.rollback()
                raise

        with self._factory.connect() as con:
            raw = con.connection
            return busy_retry(_tx)  # type: ignore[return-value]

    def hard_delete(self, memory_id: str) -> MutationOutcome:
        """Admin: permanently remove a live or soft-deleted row of this brain."""
        def _tx() -> MutationOutcome:
            raw.execute("BEGIN IMMEDIATE")
            try:
                cursor = raw.execute(
                    "DELETE FROM memories WHERE brain_id = ? AND memory_id = ?",
                    (self._brain_id, memory_id),
                )
                raw.commit()
                return (
                    MutationOutcome.APPLIED if cursor.rowcount else MutationOutcome.NOT_FOUND
                )
            except Exception:
                raw.rollback()
                raise

        with self._factory.connect() as con:
            raw = con.connection
            return busy_retry(_tx)  # type: ignore[return-value]

    # -- reads ----------------------------------------------------------------

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Live row for ``(bound brain_id, memory_id)`` or ``None``."""
        with self._factory.connect(read_only=True) as con:
            row = con.connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM memories"
                " WHERE brain_id = ? AND memory_id = ? AND " + _LIVE,
                (self._brain_id, memory_id, self._clock()),
            ).fetchone()
        return _row_to_record(row) if row else None

    def recent(
        self,
        *,
        limit: int,
        filters: RecentFilters | None = None,
    ) -> Sequence[MemoryRecord]:
        """Live records of the bound brain, newest first.

        Deterministic order ``created_at DESC, memory_id ASC``; live-filtering
        happens before ``limit`` so stale rows never starve the window.
        """
        if limit < 1:
            raise ValidationError(f"limit must be >= 1, got {limit}")
        filters = filters or RecentFilters()
        where = ["brain_id = ?"]
        params: list[object] = [self._brain_id]
        where.append(_LIVE)
        params.append(self._clock())
        if filters.topic is not None:
            where.append("topic = ?")
            params.append(filters.topic)
        if filters.catalog is not None:
            where.append("catalog = ?")
            params.append(filters.catalog)
        if filters.since_ms is not None:
            where.append("created_at_ms >= ?")
            params.append(filters.since_ms)
        if filters.until_ms is not None:
            where.append("created_at_ms <= ?")
            params.append(filters.until_ms)
        if filters.timeline_day is not None:
            where.append("timeline_day = ?")
            params.append(filters.timeline_day)
        if filters.min_importance is not None:
            where.append("importance >= ?")
            params.append(filters.min_importance)
        params.append(limit)
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM memories"
            f" WHERE {' AND '.join(where)}"
            " ORDER BY created_at_ms DESC, memory_id ASC LIMIT ?"
        )
        with self._factory.connect(read_only=True) as con:
            rows = con.connection.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]
