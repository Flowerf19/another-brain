"""Durable TTL — bounded purge and the read-never-renews invariant (TASK-051).

``expires_at`` is persisted at write time from importance (the policy itself
lives in :mod:`another_brain.domain.retention`, which has no storage
dependency so the service can arm a TTL without importing SQLite internals;
``ttl_ms_for`` and ``expires_at_ms_for`` are re-exported here for storage
callers). It is the single authoritative durability clock: every live read
excludes expired/deleted rows BEFORE any limit (repository ``_LIVE``
predicate), and reads never touch ``expires_at`` — no renewal on read.
Purge is bounded and opportunistic: a small batch of expired / past-grace
rows is hard-deleted per call (startup + write-time opportunities), and
correctness never depends on a sweeper.
"""
from __future__ import annotations

from collections.abc import Callable

from another_brain.config import FORGET_GRACE_DAYS
from another_brain.domain.retention import DAY_MS as _DAY_MS
from another_brain.domain.retention import expires_at_ms_for, ttl_ms_for
from another_brain.errors import ValidationError
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.retry import busy_retry

GRACE_MS = FORGET_GRACE_DAYS * _DAY_MS

DEFAULT_PURGE_BATCH = 500

__all__ = [
    "DEFAULT_PURGE_BATCH",
    "GRACE_MS",
    "expires_at_ms_for",
    "purge_expired",
    "ttl_ms_for",
]


def purge_expired(
    factory: SQLiteConnectionFactory,
    *,
    clock: Callable[[], int],
    max_rows: int = DEFAULT_PURGE_BATCH,
) -> int:
    """Bounded opportunistic hard-delete; returns the number of rows removed.

    Removes rows whose ``expires_at`` has passed, plus soft-deleted rows
    whose grace window has passed. FTS rows cascade through the delete
    trigger. Never blocks a caller beyond one short ``BEGIN IMMEDIATE``
    transaction with the bounded busy retry.
    """
    if max_rows < 1:
        raise ValidationError(f"max_rows must be >= 1, got {max_rows}")
    now = clock()
    with factory.connect() as con:
        raw = con.connection

        def _tx() -> int:
            raw.execute("BEGIN IMMEDIATE")
            try:
                cursor = raw.execute(
                    "DELETE FROM memories WHERE row_id IN ("
                    "  SELECT row_id FROM memories"
                    "  WHERE expires_at_ms <= ?"
                    "     OR (deleted_at_ms IS NOT NULL AND deleted_at_ms + ? < ?)"
                    "  LIMIT ?)",
                    (now, GRACE_MS, now, max_rows),
                )
                raw.commit()
                return cursor.rowcount
            except Exception:
                raw.rollback()
                raise

        return busy_retry(_tx)  # type: ignore[return-value]
