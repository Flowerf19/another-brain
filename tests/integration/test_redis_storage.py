"""Integration tests against a real Redis (Query Engine) (Step 04 sections 2-6).

Requires the compose service: docker compose -f docker/docker-compose.yml up -d
Skips cleanly when Redis (or the RediSearch module) is unavailable.
Each test run uses a unique key prefix and drops its index + docs afterwards.
"""
import os
import uuid

import pytest
import redis.asyncio as aioredis

from errors import MigrationRequiredError
from memory.models import EmbeddingVector, MemoryRecord, SearchFilters
from memory.retention import RetentionPolicy
from storage.redis_index import RedisIndexManager
from storage.redis_keys import RedisKeyBuilder
from storage.redis_repository import RedisMemoryRepository

REDIS_URL = os.environ.get("REDIS_TEST_URL", "redis://localhost:6379")
TZ = "Asia/Ho_Chi_Minh"
DIM = 8
GRACE = 3600
BASE_TS = 1_752_200_000.0  # fixed clock for deterministic assertions


def vec(axis: int) -> EmbeddingVector:
    values = [0.0] * DIM
    values[axis] = 1.0
    return EmbeddingVector.from_list(values, DIM)


def make_record(**overrides) -> MemoryRecord:
    kwargs = dict(
        brain_id="flowerf-main",
        agent_id="agent-a",
        scope="user",
        scope_id="flowerf",
        topic="redis-index",
        summary="Chọn PREFIX ab:memory: để audit key không lọt vào search.",
        tz_name=TZ,
        now_ts=BASE_TS,
    )
    kwargs.update(overrides)
    return MemoryRecord.new(**kwargs)


FILTERS = SearchFilters(scope="user", scope_id="flowerf")


@pytest.fixture
async def client():
    redis_client = aioredis.from_url(REDIS_URL)
    try:
        await redis_client.ping()
        modules = str(await redis_client.execute_command("MODULE", "LIST")).lower()
    except Exception:
        await redis_client.aclose()
        pytest.skip(f"Redis not reachable at {REDIS_URL}")
    if "search" not in modules:
        await redis_client.aclose()
        pytest.skip("search module not loaded (need Redis 8+ / Redis Stack)")
    yield redis_client
    await redis_client.aclose()


@pytest.fixture
async def store(client):
    keys = RedisKeyBuilder(prefix=f"t{uuid.uuid4().hex[:8]}")
    manager = RedisIndexManager(
        client, keys, embedding_dim=DIM, embedding_model="test-model",
    )
    await manager.ensure()
    repo = RedisMemoryRepository(
        client, keys, RetentionPolicy(),
        grace_seconds=GRACE, embedding_dim=DIM,
    )
    yield client, keys, manager, repo
    try:
        await manager.drop(delete_documents=True)
    except Exception:
        pass
    await client.delete(keys.meta_key)


async def test_index_idempotent_and_meta(store):
    client, keys, manager, repo = store
    await manager.ensure()  # second call must be a no-op
    meta = await manager.read_meta()
    assert meta["embedding_dim"] == str(DIM)
    assert meta["embedding_model"] == "test-model"
    assert meta["vector_dtype"] == "FLOAT32"
    assert meta["distance_metric"] == "COSINE"


async def test_dim_mismatch_refuses_start(store):
    client, keys, manager, repo = store
    wrong = RedisIndexManager(client, keys, embedding_dim=DIM * 2)
    with pytest.raises(MigrationRequiredError, match="reindex"):
        await wrong.ensure()


async def test_store_get_roundtrip_and_ttl(store):
    client, keys, manager, repo = store
    record = make_record(
        content="- [x] tái hiện bug\n- [ ] viết test",
        catalog="bug",
        importance=3,
        metadata={"origin": "discord", "message_id": 42},
    )
    await repo.store(record, vec(0))

    loaded = await repo.get("flowerf-main", record.identity.memory_id)
    assert loaded == record
    assert loaded.has_content

    ttl = await client.ttl(keys.memory_key("flowerf-main", record.identity.memory_id))
    assert 7_776_000 - 60 < ttl <= 7_776_000  # importance 3 → 90 days
    assert await repo.expire_at("flowerf-main", record.identity.memory_id) is not None

    wrong_dim = EmbeddingVector.from_list([1.0] * (DIM * 2), DIM * 2)
    with pytest.raises(Exception, match="dim mismatch"):
        await repo.store(make_record(), wrong_dim)


async def test_knn_search_filters_and_isolation(store):
    client, keys, manager, repo = store
    match = make_record(topic="redis-index", catalog="bug", importance=5)
    other_vector = make_record(topic="deploy-flow", catalog="note", importance=2)
    other_brain = make_record(brain_id="other-brain")
    await repo.store(match, vec(0))
    await repo.store(other_vector, vec(1))
    await repo.store(other_brain, vec(0))

    hits = await repo.knn_search("flowerf-main", FILTERS, vec(0), limit=10)
    ids = [h.record.identity.memory_id for h in hits]
    assert ids[0] == match.identity.memory_id          # closest first
    assert other_vector.identity.memory_id in ids      # same brain, k=10
    assert other_brain.identity.memory_id not in ids   # brain isolation
    assert hits[0].score == pytest.approx(0.0, abs=1e-4)  # cosine distance
    assert len(hits[0].embedding) == DIM

    by_topic = await repo.knn_search(
        "flowerf-main",
        SearchFilters(scope="user", scope_id="flowerf", topic="deploy-flow"),
        vec(0), limit=10,
    )
    assert [h.record.topic for h in by_topic] == ["deploy-flow"]

    by_importance = await repo.knn_search(
        "flowerf-main",
        SearchFilters(scope="user", scope_id="flowerf", min_importance=4),
        vec(0), limit=10,
    )
    assert [h.record.identity.memory_id for h in by_importance] == [match.identity.memory_id]

    by_day = await repo.knn_search(
        "flowerf-main",
        SearchFilters(scope="user", scope_id="flowerf", timeline_day=match.timeline_day),
        vec(0), limit=10,
    )
    assert len(by_day) == 2  # TAG escaping on dashes in YYYY-MM-DD works


async def test_bm25_matches_summary_and_content(store):
    client, keys, manager, repo = store
    record = make_record(
        summary="Hotfix cho retention policy sau khi reinforce.",
        content="checklist: xoaseckret token trong audit log",
    )
    await repo.store(record, vec(0))

    for query in ("retention", "xoaseckret"):  # summary hit + content hit
        hits = await repo.bm25_search("flowerf-main", FILTERS, query, limit=10)
        assert [h.record.identity.memory_id for h in hits] == [record.identity.memory_id], query

    assert await repo.bm25_search("flowerf-main", FILTERS, "!!!", limit=10) == []


async def test_soft_delete_restore_flow(store):
    client, keys, manager, repo = store
    record = make_record(importance=5)
    await repo.store(record, vec(0))
    memory_id = record.identity.memory_id
    key = keys.memory_key("flowerf-main", memory_id)

    assert await repo.soft_delete("flowerf-main", memory_id, now_ts=BASE_TS + 10)

    # Excluded from every query at index level, but still gettable (grace).
    assert await repo.knn_search("flowerf-main", FILTERS, vec(0), 10) == []
    assert await repo.bm25_search("flowerf-main", FILTERS, "PREFIX", 10) == []
    assert await repo.recent("flowerf-main", FILTERS, 10) == []
    deleted = await repo.get("flowerf-main", memory_id)
    assert deleted.is_deleted and deleted.deleted_at == BASE_TS + 10
    assert 0 < await client.ttl(key) <= GRACE  # shrunk, never extended

    restored = await repo.restore("flowerf-main", memory_id)
    assert restored is not None and not restored.is_deleted
    assert await client.ttl(key) > GRACE  # importance TTL re-armed
    hits = await repo.knn_search("flowerf-main", FILTERS, vec(0), 10)
    assert [h.record.identity.memory_id for h in hits] == [memory_id]

    assert not await repo.soft_delete("flowerf-main", "missing-id", now_ts=BASE_TS)


async def test_reinforce_rearms_ttl_only_for_live_records(store):
    client, keys, manager, repo = store
    record = make_record(importance=1)  # 7-day TTL
    await repo.store(record, vec(0))
    memory_id = record.identity.memory_id
    key = keys.memory_key("flowerf-main", memory_id)

    await client.expire(key, 100)  # simulate a nearly-expired memory
    reinforced = await repo.reinforce("flowerf-main", memory_id, now_ts=BASE_TS + 50)
    assert reinforced.updated_at == BASE_TS + 50
    assert 604_800 - 60 < await client.ttl(key) <= 604_800  # full importance TTL again

    assert await repo.reinforce("flowerf-main", "missing-id", now_ts=BASE_TS) is None
    await repo.soft_delete("flowerf-main", memory_id, now_ts=BASE_TS + 60)
    assert await repo.reinforce("flowerf-main", memory_id, now_ts=BASE_TS + 70) is None


async def test_recent_orders_by_period_start_desc(store):
    client, keys, manager, repo = store
    old = make_record(topic="old-entry", period_start=BASE_TS - 2_000)
    mid = make_record(topic="mid-entry", period_start=BASE_TS - 1_000)
    new = make_record(topic="new-entry", period_start=BASE_TS)
    for record in (old, mid, new):
        await repo.store(record, vec(0))

    hits = await repo.recent("flowerf-main", FILTERS, limit=10)
    assert [h.record.topic for h in hits] == ["new-entry", "mid-entry", "old-entry"]

    window = SearchFilters(
        scope="user", scope_id="flowerf",
        since_ts=BASE_TS - 1_500, until_ts=BASE_TS - 500,
    )
    hits = await repo.recent("flowerf-main", window, limit=10)
    assert [h.record.topic for h in hits] == ["mid-entry"]

    hits = await repo.recent("flowerf-main", FILTERS, limit=2)
    assert len(hits) == 2


async def test_hard_delete(store):
    client, keys, manager, repo = store
    record = make_record()
    await repo.store(record, vec(0))
    memory_id = record.identity.memory_id

    assert await repo.hard_delete("flowerf-main", memory_id)
    assert await repo.get("flowerf-main", memory_id) is None
    assert not await repo.hard_delete("flowerf-main", memory_id)
