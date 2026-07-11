"""MemoryService unit tests for the review fixes: default-limit clamp,
required scope_id for non-global scopes, and strict-JSON metadata."""
import math

import pytest

from config import AppConfig
from errors import ValidationError
from memory.models import EmbeddingVector
from memory.service import RECENT_LIMIT_MAX, MemoryService

DIM = 4


class FakeEmbedder:
    model_name = "fake-model"
    dim = DIM

    async def embed_document(self, text):
        return EmbeddingVector.from_list([1.0, 0.0, 0.0, 0.0], DIM)

    async def embed_query(self, text):
        return EmbeddingVector.from_list([1.0, 0.0, 0.0, 0.0], DIM)


class FakeRepo:
    def __init__(self):
        self.recent_calls = []

    async def recent(self, brain_id, filters, limit):
        self.recent_calls.append(limit)
        return []


def make_service(repo=None, **env):
    config = AppConfig.from_env(env)
    return MemoryService(repo or FakeRepo(), engine=None,
                         embedder=FakeEmbedder(), config=config)


async def test_recent_default_limit_clamps_to_max():
    """SEARCH_TOP_K has no upper bound; the default limit must not trip the
    service's own RECENT_LIMIT_MAX guard."""
    repo = FakeRepo()
    service = make_service(repo, SEARCH_TOP_K="150")
    await service.recent(scope="user", scope_id="flowerf")
    assert repo.recent_calls == [RECENT_LIMIT_MAX]

    # An explicit limit above the cap is still the caller's error.
    with pytest.raises(ValidationError, match="between 1 and"):
        await service.recent(scope="user", scope_id="flowerf", limit=101)


async def test_scope_id_required_for_non_global_scopes():
    """The tool schema marks scope_id optional (global doesn't need it);
    for user/project an omission must fail with an actionable message,
    not a generic domain error."""
    service = make_service()
    for scope in ("user", "project"):
        with pytest.raises(ValidationError, match="scope_id is required"):
            await service.recent(scope=scope)
        with pytest.raises(ValidationError, match="scope_id is required"):
            await service.remember("a-topic", "a summary", scope=scope)
    # global still auto-pins.
    repo = FakeRepo()
    service = make_service(repo)
    await service.recent(scope="global")
    assert repo.recent_calls == [20]  # default top_k


async def test_metadata_rejects_nan_and_infinity():
    """allow_nan=False: NaN/Infinity pass the default json.dumps but are
    not JSON — they must fail validation, not reach storage."""
    service = make_service()
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError, match="JSON-serializable"):
            await service.remember(
                "a-topic", "a summary",
                scope="user", scope_id="flowerf", metadata={"ratio": bad},
            )
