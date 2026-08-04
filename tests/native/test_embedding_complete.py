from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

import another_brain.embedding.installer as installer
from another_brain.embedding.installer import READY_FILE, install_model, model_ready, require_model
from another_brain.embedding.manifest import FILES, MODEL_DIMENSION, MODEL_REVISION, QUERY_PROMPT
from another_brain.embedding.payload import document_payload, query_payload
from another_brain.embedding.provider import OnnxEmbeddingProvider
from another_brain.errors import ConfigError, ModelNotInstalledError, ValidationError


class Encoding:
    def __init__(self, size: int):
        self.ids = list(range(size))
        self.attention_mask = [1] * size
        self.type_ids = [0] * size


class DummyTokenizer:
    def __init__(self, size: int = 4):
        self.size = size
        self.seen: list[tuple[str, bool]] = []

    def encode(self, text: str, add_special_tokens: bool):
        self.seen.append((text, add_special_tokens))
        return Encoding(self.size)


class Named:
    def __init__(self, name: str):
        self.name = name


class DummySession:
    def __init__(self, output):
        self.output = output
        self.feeds = []

    def get_inputs(self):
        return [Named("input_ids"), Named("attention_mask")]

    def get_outputs(self):
        return [Named("sentence_embedding")]

    def run(self, names, feed):
        self.feeds.append((names, feed))
        return [self.output]


def provider_with_output(output, *, token_count=4):
    provider = OnnxEmbeddingProvider(Path("unused"))
    provider._tokenizer = DummyTokenizer(token_count)
    provider._session = DummySession(output)
    return provider


def test_payload_bytes_are_stable_and_query_only_is_prompted():
    assert document_payload("native-windows", " summary ") == "native windows\nsummary"
    assert query_payload(" tìm bộ nhớ ") == QUERY_PROMPT + "tìm bộ nhớ"
    with pytest.raises(ValidationError, match="non-empty"):
        query_payload("   ")


def test_manifest_is_pinned_and_all_hashes_are_sha256():
    assert len(MODEL_REVISION) == 40
    assert MODEL_DIMENSION == 640
    assert set(FILES) == {
        "onnx/model_q4.onnx",
        "onnx/model_q4.onnx_data",
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
    assert all(len(value) == 64 and int(value, 16) >= 0 for value in FILES.values())


def test_model_ready_requires_marker_revision_and_every_file(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    assert not model_ready(model)
    (model / READY_FILE).write_text(json.dumps({"revision": "wrong"}), encoding="utf-8")
    assert not model_ready(model)
    (model / READY_FILE).write_text(json.dumps({"revision": MODEL_REVISION}), encoding="utf-8")
    assert not model_ready(model)
    for relative in FILES:
        path = model / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"present")
    assert model_ready(model)
    assert require_model(model) == model


def test_require_model_has_actionable_error(tmp_path):
    with pytest.raises(ModelNotInstalledError, match="another-brain model pull"):
        require_model(tmp_path / "missing")


def test_installer_verifies_hash_and_publishes_complete_directory(monkeypatch, tmp_path):
    payload = b"verified payload"
    expected = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(installer, "FILES", {"artifact.bin": expected})

    def download(url, target):
        Path(target).write_bytes(payload)

    monkeypatch.setattr(installer.urllib.request, "urlretrieve", download)
    model = install_model(tmp_path / "model")
    assert (model / "artifact.bin").read_bytes() == payload
    assert json.loads((model / READY_FILE).read_text(encoding="utf-8"))["revision"] == MODEL_REVISION


def test_installer_rejects_hash_mismatch_without_publishing(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "FILES", {"artifact.bin": "0" * 64})
    monkeypatch.setattr(
        installer.urllib.request,
        "urlretrieve",
        lambda url, target: Path(target).write_bytes(b"wrong"),
    )
    model = tmp_path / "model"
    with pytest.raises(ConfigError, match="SHA-256 mismatch"):
        install_model(model)
    assert not model.exists()
    assert not list(tmp_path.glob("another-brain-model-*"))


@pytest.mark.xfail(strict=True, reason="model_ready checks presence/revision but not hashes")
def test_model_ready_rejects_tampered_installed_artifacts(tmp_path):
    model = tmp_path / "model"
    for relative in FILES:
        path = model / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tampered")
    (model / READY_FILE).write_text(json.dumps({"revision": MODEL_REVISION}), encoding="utf-8")
    assert not model_ready(model)


@pytest.mark.asyncio
async def test_provider_returns_finite_unit_float32_vector_and_exact_feeds():
    output = np.zeros((1, 640), dtype=np.float32)
    output[0, 0] = 1.0
    provider = provider_with_output(output)
    vector = await provider.embed_document("native-windows", "Works.")
    assert len(vector) == 640
    assert math.isclose(sum(value * value for value in vector), 1.0, abs_tol=1e-6)
    names, feed = provider._session.feeds[0]
    assert names == ["sentence_embedding"]
    assert set(feed) == {"input_ids", "attention_mask"}
    assert feed["input_ids"].dtype == np.int64


@pytest.mark.asyncio
@pytest.mark.parametrize("output,match", [
    (np.zeros((1, 639), dtype=np.float32), "FLOAT32\\[640\\]"),
    (np.full((1, 640), np.nan, dtype=np.float32), "finite"),
    (np.ones((1, 640), dtype=np.float32), "unit normalized"),
])
async def test_provider_rejects_invalid_model_output(output, match):
    provider = provider_with_output(output)
    with pytest.raises(ValidationError, match=match):
        await provider.embed_query("query")


def test_provider_enforces_topic_content_and_embedding_budgets():
    provider = provider_with_output(np.eye(1, 640, dtype=np.float32), token_count=13)
    with pytest.raises(ValidationError, match="topic has 13"):
        provider.validate_topic("too-many-tokens")
    provider._tokenizer.size = 1_025
    with pytest.raises(ValidationError, match="content has 1025"):
        provider.validate_content("too much content")
    provider._tokenizer.size = 257
    with pytest.raises(ValidationError, match="allowed maximum is 256"):
        provider._encode("too long", 256)


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("ANOTHER_BRAIN_TEST_MODEL_DIR"),
    reason="set ANOTHER_BRAIN_TEST_MODEL_DIR to run the pinned ONNX artifact gate",
)
@pytest.mark.asyncio
async def test_real_pinned_onnx_document_and_query_outputs():
    provider = OnnxEmbeddingProvider(Path(os.environ["ANOTHER_BRAIN_TEST_MODEL_DIR"]))
    document = await provider.embed_document("native-windows", "Native execution works.")
    query = await provider.embed_query("native execution")
    for vector in (document, query):
        assert len(vector) == 640
        assert all(math.isfinite(value) for value in vector)
        assert math.isclose(sum(value * value for value in vector), 1.0, abs_tol=2e-3)
