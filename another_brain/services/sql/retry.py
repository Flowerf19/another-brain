"""Bounded busy retry for short write transactions (TASK-050).

Writes are short ``BEGIN IMMEDIATE`` transactions; when another process
holds the write lock they fail with ``database is locked``. The retry
envelope (5 attempts, exponential backoff) mirrors the concurrency harness
validated in TASK-007: within the envelope the write is retried, past it a
typed :class:`BusyExhausted` surfaces instead of a hang.
"""
from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import Callable

from another_brain.errors import BusyExhausted

RETRY_ATTEMPTS = 5
RETRY_BASE_S = 0.05


def busy_retry(
    fn: Callable[[], object],
    *,
    attempts: int = RETRY_ATTEMPTS,
    base_s: float = RETRY_BASE_S,
) -> object:
    """Run ``fn`` retrying only on locked/busy SQLite errors."""
    last: sqlite3.OperationalError | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            text = str(exc).lower()
            if "locked" not in text and "busy" not in text:
                raise
            last = exc
            # bounded exponential backoff with jitter: two writers colliding
            # lockstep must not resynchronize across attempts
            delay = base_s * (2**attempt)
            time.sleep(delay * random.uniform(0.5, 1.5))
    raise BusyExhausted(f"write busy after {attempts} attempts: {last}") from last
