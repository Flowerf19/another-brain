"""Integration tests for the audit trail against a real Redis: mutations through
MemoryService land as secret-free events in the per-day audit HASH, and
audit_events reads them back newest-first.

Shares the skip/version gate shape with test_redis_storage.py.
"""
import os
import uuid

import pytest
import redis.asyncio as aioredis

from audit.models import AuditAction
from audit.service import AuditService
from config import AppConfig
from memory.models import EmbeddingVector, timeline_day_from_ts
from memory.retention import RetentionPolicy
from memory.search import MemorySearchEngine
from memory.service import MemoryService
from storage.redis_index import RedisIndexManager
from storage.redis_keys import RedisKeyBuilder
from storage.redis_repository import RedisMemoryRepository

REDIS_URL = os.environ.get("REDIS_TEST_URL", "redis://localhost:1905")
TZ = "Asia/Ho_Chi_Minh"
DIM = 8
BASE_TS = 1_752_990_000.0
DAY = timeline_day_from_ts(BASE_TS, TZ)
FT_HYBRID_MIN_VER = 80400


class FakeEmbedder:
    model_name = "fake-model"
    dim = DIM

    async def embed_document(self, text):
        return EmbeddingVector.from_list([1.0] + [0.0] * (DIM - 1), DIM)

    async def embed_query(self, text):
        return EmbeddingVector.from_list([1.0] + [0.0] * (DIM - 1), DIM)


@pytest.fixture
async def client():
    redis_client = aioredis.from_url(REDIS_URL)
    try:
        await redis_client.ping()
        modules = await redis_client.module_list()
    except Exception:
        await redis_client.aclose()
        pytest.skip(f"Redis not reachable at {REDIS_URL}")
    search = next((m for m in modules if m.get(b"name") == b"search"), None)
    if search is None or int(search.get(b"ver", 0)) < FT_HYBRID_MIN_VER:
        await redis_client.aclose()
        pytest.skip(f"FT.HYBRID needs Redis 8.4+ at {REDIS_URL}")
    yield redis_client
    await redis_client.aclose()


@pytest.fixture
async def service(client):
    prefix = f"t{uuid.uuid4().hex[:8]}"
    keys = RedisKeyBuilder(prefix=prefix)
    manager = RedisIndexManager(
        client, keys, embedding_dim=DIM, embedding_model="fake-model",
    )
    await manager.ensure()
    repo = RedisMemoryRepository(
        client, keys, RetentionPolicy(), grace_seconds=3600, embedding_dim=DIM,
    )
    audit = AuditService(client, keys, retention_days=90, tz_name=TZ)
    config = AppConfig.from_env({
        "BRAIN_ID": "flowerf-main",
        "TIMELINE_TIMEZONE": TZ, "EMBEDDING_DIM": str(DIM),
    })
    svc = MemoryService(
        repo, MemorySearchEngine(repo, config.search), FakeEmbedder(), config,
        index=manager, audit=audit,
    )
    yield svc, keys, audit
    try:
        await manager.drop(delete_documents=True)
    except Exception:
        pass
    await client.delete(keys.meta_key)
    await client.delete(keys.audit_key("flowerf-main", DAY))


async def test_remember_and_forget_leave_audit_events(service):
    svc, keys, audit = service
    result = await svc.remember(
        "audit-topic", "A memory to trace through the audit log.",
        agent_id="claude-code",
        scope="user", scope_id="flowerf", importance=2, now_ts=BASE_TS,
    )
    mid = result.memory_id

    events = await audit.list_day("flowerf-main", DAY, limit=10)
    assert [e.action for e in events] == [AuditAction.REMEMBER]
    assert events[0].memory_id == mid
    assert events[0].agent_id == "claude-code"
    assert events[0].detail == {"importance": 2, "scope": "user"}

    assert await svc.forget(mid, agent_id="claude-code", now_ts=BASE_TS + 5)
    events = await audit.list_day("flowerf-main", DAY, limit=10)
    # newest first: forget, then remember
    assert [e.action for e in events] == [AuditAction.FORGET, AuditAction.REMEMBER]


async def test_audit_events_read_path_defaults_and_shape(service):
    svc, keys, audit = service
    await svc.remember(
        "audit-read", "Second entry for the read-path test.",
        agent_id="claude-code",
        scope="user", scope_id="flowerf", now_ts=BASE_TS,
    )
    events = await svc.audit_events(day=DAY, limit=10)
    assert len(events) == 1
    assert events[0].action == AuditAction.REMEMBER
