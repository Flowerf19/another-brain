"""Durable TTL — computation, bounded purge, read-never-renews (TASK-051).

``expires_at`` is persisted at write time from importance (5..1 →
365/180/90/30/7 days) and is the single authoritative durability clock.
Every live read excludes expired/deleted rows BEFORE any limit (repository
``_LIVE`` predicate); reads never touch ``expires_at`` — no renewal on read.
Purge is bounded and opportunistic: a small batch of expired / past-grace
rows is hard-deleted per call (startup + write-time opportunities), and
correctness never depends on a sweeper.
"""
from __future__ import annotations

from collections.abc import Callable

from another_brain.config import FORGET_GRACE_DAYS, TTL_DAYS_BY_IMPORTANCE
from another_brain.errors import ValidationError
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.retry import busy_retry

_DAY_MS = 86_400_000

DEFAULT_PURGE_BATCH = 500


def ttl_ms_for(importance: int) -> int:
    """Locked TTL for one importance level, in milliseconds."""
    try:
        days = TTL_DAYS_BY_IMPORTANCE[importance]
    except KeyError:
        raise ValidationError(
            f"importance must be in 1..5, got {importance}"
        ) from None
    return days * _DAY_MS


def expires_at_ms_for(importance: int, now_ms: int) -> int:
    """The absolute expiry a new memory of ``importance`` gets at ``now``."""
    return now_ms + ttl_ms_for(importance)


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
    grace_ms = FORGET_GRACE_DAYS * _DAY_MS
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
                    (now, grace_ms, now, max_rows),
                )
                raw.commit()
                return cursor.rowcount
            except Exception:
                raw.rollback()
                raise

        return busy_retry(_tx)  # type: ignore[return-value]
