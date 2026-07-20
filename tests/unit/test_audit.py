"""Audit unit tests: AuditEvent JSON round-trip + secret-free guarantee, and
AuditService write/read semantics (day key, rolling TTL, graceful I/O
degradation, newest-first + limit) against a dict-backed fake redis."""
import json

import pytest
from redis.exceptions import RedisError

from audit.models import AuditAction, AuditEvent
from audit.service import AuditService
from errors import ValidationError
from memory.models import timeline_day_from_ts
from storage.redis_keys import RedisKeyBuilder

TZ = "Asia/Ho_Chi_Minh"
# A fixed clock; the day key is derived from it, never hardcoded.
BASE_TS = 1_752_990_000.0
DAY = timeline_day_from_ts(BASE_TS, TZ)


def make_event(**overrides) -> AuditEvent:
    kwargs = dict(
        action=AuditAction.REMEMBER,
        memory_id="mem-1",
        brain_id="flowerf-main",
        agent_id="claude-code",
        ts=BASE_TS,
        detail={"importance": 3, "scope": "user"},
    )
    kwargs.update(overrides)
    return AuditEvent(**kwargs)


def test_event_json_round_trip_preserves_fields():
    event = make_event()
    restored = AuditEvent.from_json(event.to_json())
    assert restored == event


def test_event_rejects_secret_bearing_detail():
    for leaky in ({"summary": "secret"}, {"content": "x"}, {"topic": "t"},
                  {"metadata": {}}):
        with pytest.raises(ValidationError, match="secret-free"):
            make_event(detail=leaky)


def test_event_json_is_secret_free():
    raw = make_event().to_json()
    assert "summary" not in raw and "content" not in raw


class FakeRedis:
    """Minimal HASH + EXPIRE + HGETALL, optionally raising to exercise the
    graceful-degradation path."""

    def __init__(self, *, fail=False):
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}
        self._fail = fail

    async def hset(self, key, field, value):
        if self._fail:
            raise RedisError("boom")
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key, ttl):
        if self._fail:
            raise RedisError("boom")
        self.ttls[key] = ttl

    async def hgetall(self, key):
        if self._fail:
            raise RedisError("boom")
        return dict(self.hashes.get(key, {}))


def _service(redis, *, retention_days=90) -> AuditService:
    return AuditService(
        redis, RedisKeyBuilder(), retention_days=retention_days, tz_name=TZ,
    )


async def test_record_writes_day_key_and_arms_ttl():
    redis = FakeRedis()
    await _service(redis).record(make_event())
    key = f"ab:audit:flowerf-main:{DAY}"
    assert key in redis.hashes
    stored = json.loads(next(iter(redis.hashes[key].values())))
    assert stored["action"] == AuditAction.REMEMBER
    assert redis.ttls[key] == 90 * 86_400


async def test_record_swallows_redis_io_error():
    # The memory is already stored; a Redis hiccup here must not raise.
    await _service(FakeRedis(fail=True)).record(make_event())


async def test_list_day_sorts_newest_first_and_limits():
    redis = FakeRedis()
    service = _service(redis)
    await service.record(make_event(memory_id="old", ts=BASE_TS))
    await service.record(make_event(memory_id="mid", ts=BASE_TS + 10))
    await service.record(make_event(memory_id="new", ts=BASE_TS + 20))

    events = await service.list_day("flowerf-main", DAY, limit=10)
    assert [e.memory_id for e in events] == ["new", "mid", "old"]

    top = await service.list_day("flowerf-main", DAY, limit=2)
    assert [e.memory_id for e in top] == ["new", "mid"]


async def test_list_day_degrades_on_io_error():
    assert await _service(FakeRedis(fail=True)).list_day(
        "flowerf-main", DAY, limit=10
    ) == []
