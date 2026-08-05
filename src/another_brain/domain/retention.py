"""Importance → durable expiry policy (TASK-051 policy half).

Pure retention arithmetic with no storage dependency, so the service layer
can arm a TTL at write time without importing SQLite internals. The bounded
purge that acts on these values lives in
:mod:`another_brain.services.sql.ttl`.

``expires_at`` is the single authoritative durability clock: it is persisted
once at write time and never renewed by a read. Only an explicit reinforce or
restore re-arms it; forget clamps it to the grace window and never extends it.
"""
from __future__ import annotations

from another_brain.config import TTL_DAYS_BY_IMPORTANCE
from another_brain.errors import ValidationError

DAY_MS = 86_400_000


def ttl_ms_for(importance: int) -> int:
    """Locked TTL for one importance level, in milliseconds."""
    try:
        days = TTL_DAYS_BY_IMPORTANCE[importance]
    except KeyError:
        raise ValidationError(
            f"importance must be in 1..5, got {importance}"
        ) from None
    return days * DAY_MS


def expires_at_ms_for(importance: int, now_ms: int) -> int:
    """The absolute expiry a new memory of ``importance`` gets at ``now``."""
    return now_ms + ttl_ms_for(importance)
