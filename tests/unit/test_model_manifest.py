"""TASK-042: the immutable model manifest must not drift from locked values."""
import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from another_brain.model_manifest import (
    DIMENSIONS,
    DOCUMENT_TEMPLATE,
    DTYPE,
    INPUT_VERSION,
    MODEL_MANIFEST,
    NORMALIZATION,
    QUERY_PROMPT,
    QUERY_PROMPT_UTF8_SHA256,
    REVISION,
    manifest_digest,
    manifest_json,
)


def test_locked_identity():
    assert MODEL_MANIFEST.profile == "q4"
    assert MODEL_MANIFEST.repo == "onnx-community/harrier-oss-v1-270m-ONNX"
    assert MODEL_MANIFEST.revision == REVISION == "d59c919d0159aea2c19ed7d04288fcdd048d0f9c"


def test_five_pinned_files_with_locked_hashes():
    files = dict(MODEL_MANIFEST.files)
    assert len(files) == 5
    assert files == {
        "onnx/model_q4.onnx": "228dca2603b907d673dd99cf89c309c0ca68baeed127416a5e027a48e62b0f49",
        "onnx/model_q4.onnx_data": "b5a15487360f5341659480ae4b5ad60028d5f865bd329196ec8d5708bbed3118",
        "config.json": "5366f9919a82aaeceb6707bf218c5769f414d60f5dbaf781fa07e5465487fd7c",
        "tokenizer.json": "ec95be298bea26f90370854faa650744c9fb0a04ca5e5ff95dd3913393ac5e45",
        "tokenizer_config.json": "135405f3479eaebc473e2e78593f2195c7598948a215ee748758def426b30f59",
    }


def test_query_prompt_hash_matches_locked_value():
    assert hashlib.sha256(QUERY_PROMPT.encode("utf-8")).hexdigest() == QUERY_PROMPT_UTF8_SHA256
    assert QUERY_PROMPT_UTF8_SHA256 == "df4b2898bf22e00bacddddd489243a3f8793730e38b842ec10161cebd94d36d6"
    assert MODEL_MANIFEST.query_prompt == QUERY_PROMPT
    # byte-exact contract with the spike payloads module
    assert QUERY_PROMPT == (
        "Instruct: Given a web search query, retrieve relevant passages that answer the query"
        "\nQuery: "
    )


def test_semantics_dims_normalization_input_version():
    assert MODEL_MANIFEST.input_version == INPUT_VERSION == 2
    assert MODEL_MANIFEST.dimensions == DIMENSIONS == 640
    assert MODEL_MANIFEST.dtype == DTYPE == "float32"
    assert MODEL_MANIFEST.normalization == NORMALIZATION == "unit_l2"
    assert MODEL_MANIFEST.document_template == DOCUMENT_TEMPLATE
    assert DOCUMENT_TEMPLATE == 'topic.replace("-", " ") + "\\n" + summary.strip()'


def test_manifest_is_immutable():
    with pytest.raises(FrozenInstanceError):
        MODEL_MANIFEST.profile = "nope"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        MODEL_MANIFEST.files = ()  # type: ignore[misc]


def test_canonical_json_is_deterministic_and_complete():
    import dataclasses

    first = manifest_json()
    assert manifest_json() == first  # deterministic
    payload = json.loads(first)
    # the digest covers every dataclass field — no silent omissions
    assert set(payload) == {f.name for f in dataclasses.fields(MODEL_MANIFEST)}
    assert len(payload["files"]) == 5
    assert payload["query_prompt"] == QUERY_PROMPT
    digest = manifest_digest()
    assert isinstance(digest, str) and len(digest) == 64
    assert manifest_digest() == digest
