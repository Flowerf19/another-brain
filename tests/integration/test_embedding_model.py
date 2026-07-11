"""Integration tests for the real local embedding model (Step 03 Harrier
profile). Skips cleanly when the model has not been pulled yet:

    MODEL_ALLOW_NETWORK=true uv run python src/main.py model pull --kind embedding

Loads a 270M model on CPU — expect a few seconds on first encode.
"""
import math
import os
from pathlib import Path

import pytest

from memory.embeddings import LocalEmbeddingProvider
from models.cache import ModelCache
from models.registry import KIND_EMBEDDING, ModelRegistry
from models.runtime import ModelRuntimeProfile

MODEL_NAME = "microsoft/harrier-oss-v1-270m"
REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = Path(
    os.environ.get("MODEL_CACHE_DIR", REPO_ROOT / ".cache/another-brain/models")
)

cache = ModelCache(CACHE_DIR)
pytestmark = pytest.mark.skipif(
    not cache.is_cached(MODEL_NAME),
    reason=f"{MODEL_NAME} not in {CACHE_DIR} — run model pull first",
)


@pytest.fixture(scope="module")
def provider() -> LocalEmbeddingProvider:
    spec = ModelRegistry().resolve(MODEL_NAME, KIND_EMBEDDING, configured_dim=640)
    profile = ModelRuntimeProfile(query_prompt_name=spec.query_prompt_name)
    return LocalEmbeddingProvider(
        cache.model_dir(MODEL_NAME),
        model_name=MODEL_NAME,
        dim=spec.expected_dim,
        profile=profile,
    )


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a.values, b.values))


async def test_document_embedding_shape_and_norm(provider):
    vector = await provider.embed_document(
        "Chọn Redis Stack làm storage chính cho memory service."
    )
    assert len(vector.values) == 640
    norm = math.sqrt(sum(v * v for v in vector.values))
    assert norm == pytest.approx(1.0, abs=1e-3)  # NORMALIZE_EMBEDDINGS=true


async def test_query_ranks_related_document_higher(provider):
    related = await provider.embed_document(
        "Chọn Redis Stack làm storage chính cho memory service."
    )
    unrelated = await provider.embed_document(
        "Công thức nấu phở bò cần hầm xương ống trong tám tiếng."
    )
    query = await provider.embed_query("dịch vụ memory dùng database gì để lưu trữ?")

    assert cosine(query, related) > cosine(query, unrelated)


async def test_query_and_document_embeddings_differ(provider):
    text = "Chọn Redis Stack làm storage chính cho memory service."
    as_document = await provider.embed_document(text)
    as_query = await provider.embed_query(text)
    # Harrier applies a query prompt, so the two sides must not be identical.
    assert as_document.values != as_query.values
