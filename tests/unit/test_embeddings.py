"""Unit tests for LocalEmbeddingProvider (Step 03)."""
import pytest

from errors import ConfigError, ValidationError
from memory.embeddings import LocalEmbeddingProvider
from models.runtime import ModelRuntimeProfile

DIM = 4


class StubModel:
    """Stands in for a loaded SentenceTransformer without importing it."""

    def __init__(self, dim=DIM, prompts=None, vector=None):
        self.prompts = {} if prompts is None else prompts
        self._vector = vector if vector is not None else [1] * dim
        self.encode_calls = []

    def encode(self, text, **kwargs):
        self.encode_calls.append((text, kwargs))
        return self._vector


def make_provider(model, profile=None):
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        return model

    provider = LocalEmbeddingProvider(
        "/fake/model/path",
        model_name="fake/model",
        dim=DIM,
        profile=profile or ModelRuntimeProfile(),
        model_factory=factory,
    )
    return provider, calls


class TestLazyLoading:
    async def test_factory_not_called_before_first_embed(self):
        model = StubModel()
        _provider, calls = make_provider(model)
        assert calls["count"] == 0

    async def test_factory_called_once_on_first_embed(self):
        model = StubModel()
        provider, calls = make_provider(model)

        await provider.embed_document("hello")

        assert calls["count"] == 1

    async def test_factory_not_recalled_on_second_embed(self):
        model = StubModel()
        provider, calls = make_provider(model)

        await provider.embed_document("hello")
        await provider.embed_document("world")

        assert calls["count"] == 1


class TestEncodingArgs:
    async def test_embed_document_has_no_prompt_name(self):
        model = StubModel(prompts={"query": "q: "})
        profile = ModelRuntimeProfile(query_prompt_name="query", normalize=True)
        provider, _calls = make_provider(model, profile)

        await provider.embed_document("hello")

        _text, kwargs = model.encode_calls[0]
        assert kwargs == {"normalize_embeddings": True}

    async def test_embed_query_uses_configured_prompt_name(self):
        model = StubModel(prompts={"query": "q: "})
        profile = ModelRuntimeProfile(query_prompt_name="query", normalize=False)
        provider, _calls = make_provider(model, profile)

        await provider.embed_query("hello")

        _text, kwargs = model.encode_calls[0]
        assert kwargs == {"normalize_embeddings": False, "prompt_name": "query"}

    async def test_embed_query_without_configured_prompt_has_no_prompt_name(self):
        model = StubModel(prompts={"query": "q: "})
        profile = ModelRuntimeProfile(query_prompt_name=None)
        provider, _calls = make_provider(model, profile)

        await provider.embed_query("hello")

        _text, kwargs = model.encode_calls[0]
        assert kwargs == {"normalize_embeddings": True}


class TestPromptValidation:
    async def test_missing_configured_prompt_raises_config_error(self):
        model = StubModel(prompts={"query": "q: "})
        profile = ModelRuntimeProfile(query_prompt_name="missing")
        provider, _calls = make_provider(model, profile)

        with pytest.raises(ConfigError):
            await provider.embed_query("hello")


class TestOutputValidation:
    async def test_wrong_vector_length_raises(self):
        model = StubModel(vector=[1, 2, 3])  # DIM is 4
        provider, _calls = make_provider(model)

        with pytest.raises(ValidationError):
            await provider.embed_document("hello")

    async def test_values_converted_to_float(self):
        model = StubModel(vector=[1, 2, 3, 4])
        provider, _calls = make_provider(model)

        vector = await provider.embed_document("hello")

        assert all(isinstance(v, float) for v in vector.values)
        assert vector.values == (1.0, 2.0, 3.0, 4.0)
