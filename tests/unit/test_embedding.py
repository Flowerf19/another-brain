"""TASK-017: ONNX provider — payloads, lazy serialized load, graph-shape
assertion against the manifest, health states, and output validation.

Uses fake tokenizer/session stubs; the real pinned model runs in the
GOAL-001 permanent slow tests (TASK-019)."""
from __future__ import annotations

import threading
import types
from pathlib import Path

import numpy as np
import pytest

import another_brain.services.embedding.provider as embedding_mod
from another_brain.domain.models import EmbeddingVector
from another_brain.services.embedding.provider import ONNXEmbeddingProvider
from another_brain.errors import (
    EmbeddingLoadError,
    EmbeddingOutputError,
    ModelNotInstalledError,
    ValidationError,
)
from another_brain.services.embedding.model_manifest import QUERY_PROMPT, manifest_digest
from another_brain.protocols import EmbeddingHealth
from tests.unit.test_model_installer import _make_manifest

DIM = 640
UNIT = 1.0 / np.sqrt(DIM)


class FakeTokenizer:
    from_file_calls: list[str] = []
    encoded_texts: list[str] = []

    def __init__(self) -> None:
        pass

    @classmethod
    def from_file(cls, path: str) -> "FakeTokenizer":
        cls.from_file_calls.append(str(path))
        return cls()

    def encode_batch(self, texts: list[str]):
        type(self).encoded_texts = list(texts)
        return [
            types.SimpleNamespace(ids=[101, 1, 2, 102], attention_mask=[1, 1, 1, 1], type_ids=[0, 0, 0, 0])
            for _ in texts
        ]


class FakeSession:
    created = 0
    shape: list | tuple = ("batch", DIM)
    dim = DIM
    output_fn = None  # feed -> np.ndarray override

    def __init__(self, path: str, providers=None) -> None:
        type(self).created += 1
        self.path = path

    def get_inputs(self):
        return [
            types.SimpleNamespace(name="input_ids"),
            types.SimpleNamespace(name="attention_mask"),
        ]

    def get_outputs(self):
        return [types.SimpleNamespace(name="sentence_embedding", shape=list(type(self).shape))]

    def run(self, names, feed):
        if type(self).output_fn is not None:
            return [type(self).output_fn(feed)]
        n = feed["input_ids"].shape[0]
        dim = type(self).dim
        return [np.full((n, dim), 1.0 / np.sqrt(dim), dtype=np.float32)]


@pytest.fixture(autouse=True)
def _stubs(monkeypatch, tmp_path):
    """Fake tokenizer/session + installed marker for the fake manifest."""
    FakeTokenizer.from_file_calls = []
    FakeTokenizer.encoded_texts = []
    FakeSession.created = 0
    FakeSession.shape = ("batch", DIM)
    FakeSession.dim = DIM
    FakeSession.output_fn = None
    monkeypatch.setattr(embedding_mod.Tokenizer, "from_file", FakeTokenizer.from_file)
    monkeypatch.setattr(embedding_mod.ort, "InferenceSession", FakeSession)

    manifest = _make_manifest(
        {"onnx/model_q4.onnx": b"g", "tokenizer.json": b"t"}, dimensions=DIM
    )
    profile = tmp_path / "profile"
    profile.mkdir()
    marker = {
        "profile": manifest.profile,
        "revision": manifest.revision,
        "manifest_digest": manifest_digest(manifest),
        "installed_at_ms": 1,
    }
    (profile / ".installed.json").write_text(
        __import__("json").dumps(marker), encoding="utf-8"
    )
    return tmp_path, manifest


@pytest.fixture
def provider(tmp_path, _stubs):
    _ = _stubs
    return ONNXEmbeddingProvider(tmp_path / "profile", manifest=_stubs[1])


class TestLazyLoadAndHealth:
    def test_health_not_loaded_before_any_call(self, provider):
        assert provider.health() == EmbeddingHealth.NOT_LOADED
        assert provider.load_error() is None
        assert FakeSession.created == 0  # nothing loaded

    def test_health_never_loads_the_model(self, provider):
        assert provider.health() == EmbeddingHealth.NOT_LOADED
        assert FakeSession.created == 0
        assert provider.health() == EmbeddingHealth.NOT_LOADED

    def test_lazy_single_session(self, provider):
        provider.embed_document(topic="t", summary="s")
        assert FakeSession.created == 1
        assert provider.health() == EmbeddingHealth.READY
        provider.embed_query("q")
        assert FakeSession.created == 1  # same session reused


class TestPayloads:
    def test_document_payload_exact(self, provider):
        provider.embed_document(topic="my-topic", summary="  summary text  ")
        assert FakeTokenizer.encoded_texts == ["my topic\nsummary text"]

    def test_query_payload_exact_and_empty_rejected(self, provider):
        provider.embed_query("  hello world  ")
        assert FakeTokenizer.encoded_texts == [QUERY_PROMPT + "hello world"]
        with pytest.raises(ValidationError):
            provider.embed_query("   ")

    def test_query_empty_does_not_load_model(self, provider):
        with pytest.raises(ValidationError):
            provider.embed_query("  ")
        assert FakeSession.created == 0
        assert provider.health() == EmbeddingHealth.NOT_LOADED

    def test_returns_validated_embedding_vector(self, provider):
        vector = provider.embed_document(topic="t", summary="s")
        assert isinstance(vector, EmbeddingVector)
        assert vector.values.dtype == np.float32
        assert vector.values.shape == (DIM,)
        assert np.isclose(np.linalg.norm(vector.values), 1.0, atol=1e-3)


class TestConcurrentFirstLoad:
    def test_threads_serialize_first_load(self, provider):
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            try:
                provider.embed_document(topic="t", summary="s")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        assert FakeSession.created == 1
        assert provider.health() == EmbeddingHealth.READY


class TestClose:
    def test_close_is_idempotent_and_drops_references(self, provider):
        provider.embed_document(topic="t", summary="s")
        assert provider.health() == EmbeddingHealth.READY
        provider.close()
        provider.close()  # idempotent
        assert provider.health() == EmbeddingHealth.NOT_LOADED
        assert provider.load_error() is None

    def test_embed_after_close_reloads_lazily(self, provider):
        provider.embed_document(topic="t", summary="s")
        assert FakeSession.created == 1
        provider.close()
        provider.embed_document(topic="t", summary="s")
        assert FakeSession.created == 2  # new session, still one per load
        assert provider.health() == EmbeddingHealth.READY

    def test_close_resets_error_state_and_retries(self, provider):
        FakeSession.shape = ("batch", 512)
        with pytest.raises(EmbeddingLoadError):
            provider.embed_document(topic="t", summary="s")
        assert provider.health() == EmbeddingHealth.ERROR
        provider.close()  # reset: next embed retries the load
        FakeSession.shape = ("batch", DIM)
        provider.embed_document(topic="t", summary="s")
        assert provider.health() == EmbeddingHealth.READY
        assert FakeSession.created == 2


class TestGraphContract:
    def test_dim_mismatch_fails_load_and_sticks(self, provider):
        FakeSession.shape = ("batch", 512)
        with pytest.raises(EmbeddingLoadError, match=r"512 != manifest 640"):
            provider.embed_document(topic="t", summary="s")
        assert provider.health() == EmbeddingHealth.ERROR
        assert "512 != manifest 640" in provider.load_error()
        with pytest.raises(EmbeddingLoadError):
            provider.embed_document(topic="t", summary="s")  # no retry, recorded error
        assert FakeSession.created == 1

    def test_missing_sentence_embedding_output(self, provider):
        original = FakeSession.get_outputs

        def get_outputs(self):
            return [types.SimpleNamespace(name="logits", shape=["batch", 640])]

        FakeSession.get_outputs = get_outputs
        try:
            with pytest.raises(EmbeddingLoadError, match="sentence_embedding output missing"):
                provider.embed_document(topic="t", summary="s")
            assert provider.health() == EmbeddingHealth.ERROR
        finally:
            FakeSession.get_outputs = original

    def test_missing_marker_is_typed_not_installed(self, tmp_path, _stubs):
        manifest = _stubs[1]
        empty = tmp_path / "empty"
        empty.mkdir()
        provider = ONNXEmbeddingProvider(empty, manifest=manifest)
        with pytest.raises(ModelNotInstalledError, match="model pull"):
            provider.embed_query("hello")
        assert provider.health() == EmbeddingHealth.ERROR
        assert FakeSession.created == 0

    def test_marker_digest_drift_is_not_installed(self, tmp_path, _stubs):
        manifest = _stubs[1]
        profile = tmp_path / "profile"
        import json

        marker = json.loads((profile / ".installed.json").read_text())
        marker["manifest_digest"] = "0" * 64
        (profile / ".installed.json").write_text(json.dumps(marker))
        provider = ONNXEmbeddingProvider(profile, manifest=manifest)
        with pytest.raises(ModelNotInstalledError):
            provider.embed_document(topic="t", summary="s")
        assert provider.health() == EmbeddingHealth.ERROR


class TestOutputValidation:
    def test_non_finite_output(self, provider):
        FakeSession.output_fn = lambda feed: np.full((1, DIM), np.nan, dtype=np.float32)
        with pytest.raises(EmbeddingOutputError, match="non-finite"):
            provider.embed_document(topic="t", summary="s")
        assert provider.health() == EmbeddingHealth.READY  # session itself is fine

    def test_not_unit_norm(self, provider):
        FakeSession.output_fn = lambda feed: np.zeros((1, DIM), dtype=np.float32)
        with pytest.raises(EmbeddingOutputError, match="unit norm"):
            provider.embed_document(topic="t", summary="s")

    def test_wrong_dtype(self, provider):
        FakeSession.output_fn = lambda feed: np.full((1, DIM), 0.1, dtype=np.float64)
        with pytest.raises(EmbeddingOutputError, match="FLOAT32"):
            provider.embed_document(topic="t", summary="s")

    def test_wrong_ndim(self, provider):
        FakeSession.output_fn = lambda feed: np.zeros((1, DIM, 1), dtype=np.float32)
        with pytest.raises(EmbeddingOutputError, match=r"\[batch, 640\]"):
            provider.embed_document(topic="t", summary="s")
