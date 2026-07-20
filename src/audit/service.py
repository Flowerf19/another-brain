"""AuditService — persists and reads secret-free audit events.

One HASH per brain per day (`ab:audit:{brain_id}:{YYYY-MM-DD}`, Step 04 §2.2),
events keyed by event_id, with a rolling retention TTL (§4.4). Writing an audit
event must never fail the mutation it describes — the memory is already stored,
so a Redis hiccup here degrades to a logged warning, not a raised error.
"""
from __future__ import annotations

import logging
from typing import Any

from audit.models import AuditEvent
from memory.models import timeline_day_from_ts
from redis.exceptions import RedisError
from storage.redis_keys import RedisKeyBuilder

logger = logging.getLogger(__name__)

# Audit writes/reads degrade gracefully on Redis I/O failure; anything else
# (a genuine bug) still propagates.
_REDIS_IO_ERRORS = (RedisError, OSError)

_SECONDS_PER_DAY = 86_400


class AuditService:
    def __init__(
        self,
        redis: Any,
        keys: RedisKeyBuilder,
        *,
        retention_days: int,
        tz_name: str,
    ):
        self._redis = redis
        self._keys = keys
        self._retention_days = retention_days
        self._tz_name = tz_name

    async def record(self, event: AuditEvent) -> None:
        """Append one event to its day HASH and (re)arm the retention TTL.
        Observability, not a transaction gate: never propagates a Redis error."""
        day = timeline_day_from_ts(event.ts, self._tz_name)
        key = self._keys.audit_key(event.brain_id, day)
        try:
            await self._redis.hset(key, event.event_id, event.to_json())
            await self._redis.expire(key, self._retention_days * _SECONDS_PER_DAY)
        except _REDIS_IO_ERRORS as exc:
            logger.warning(
                "audit write dropped (action=%s memory_id=%s): %s",
                event.action, event.memory_id, exc,
            )

    async def list_day(
        self, brain_id: str, day: str, *, limit: int
    ) -> list[AuditEvent]:
        """All events for one brain-day, newest first, capped at limit.
        Degrades to an empty list on Redis I/O failure."""
        key = self._keys.audit_key(brain_id, day)
        try:
            raw = await self._redis.hgetall(key)
        except _REDIS_IO_ERRORS as exc:
            logger.error("audit read failed for %s: %s", key, exc)
            return []
        events = [AuditEvent.from_json(value) for value in raw.values()]
        events.sort(key=lambda e: e.ts, reverse=True)
        return events[:limit]
