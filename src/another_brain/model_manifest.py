"""The one immutable embedding-model manifest (TASK-042).

Single source of truth for the locked q4 profile — repository, revision, the
exact five runtime files and their SHA-256 hashes, the byte-exact query
prompt and its hash, payload semantics, dimensions, normalization, and input
version. Consumed by the installer (TASK-043), the ONNX provider (TASK-017),
and the schema/profile gate (TASK-027/048).

Pure standard library: no onnxruntime import here, so the spike evaluation
environment can import it for cross-checks without pulling production
dependencies.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

# --- locked upstream identity -------------------------------------------------
REPO = "onnx-community/harrier-oss-v1-270m-ONNX"
REVISION = "d59c919d0159aea2c19ed7d04288fcdd048d0f9c"

# The exact five runtime files: graph + external weights + tokenizer/config.
FILES_SHA256: dict[str, str] = {
    "onnx/model_q4.onnx": "228dca2603b907d673dd99cf89c309c0ca68baeed127416a5e027a48e62b0f49",
    "onnx/model_q4.onnx_data": "b5a15487360f5341659480ae4b5ad60028d5f865bd329196ec8d5708bbed3118",
    "config.json": "5366f9919a82aaeceb6707bf218c5769f414d60f5dbaf781fa07e5465487fd7c",
    "tokenizer.json": "ec95be298bea26f90370854faa650744c9fb0a04ca5e5ff95dd3913393ac5e45",
    "tokenizer_config.json": "135405f3479eaebc473e2e78593f2195c7598948a215ee748758def426b30f59",
}

# Byte-exact query prompt (locked hash below). Documents are prompted with
# nothing; the provider prepends this prompt to user queries only.
QUERY_PROMPT = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query"
    "\nQuery: "
)

QUERY_PROMPT_UTF8_SHA256 = "df4b2898bf22e00bacddddd489243a3f8793730e38b842ec10161cebd94d36d6"

# Payload semantics shared by both encode paths (input version 2).
DOCUMENT_TEMPLATE = 'topic.replace("-", " ") + "\\n" + summary.strip()'

INPUT_VERSION = 2
DIMENSIONS = 640
DTYPE = "float32"
# The graph emits an L2-normalized sentence_embedding directly; the provider
# still validates unit norm on every batch (finite, |norm-1| <= 1e-3).
NORMALIZATION = "unit_l2"


@dataclass(frozen=True)
class ModelManifest:
    """Immutable profile descriptor; consumers receive this, never the raw dict."""

    profile: str
    repo: str
    revision: str
    files: tuple[tuple[str, str], ...]  # (relative_path, sha256), ordered
    query_prompt: str
    query_prompt_utf8_sha256: str
    document_template: str
    input_version: int
    dimensions: int
    dtype: str
    normalization: str


def _ordered_files() -> tuple[tuple[str, str], ...]:
    return tuple(sorted(FILES_SHA256.items()))


def _build() -> ModelManifest:
    manifest = ModelManifest(
        profile="q4",
        repo=REPO,
        revision=REVISION,
        files=_ordered_files(),
        query_prompt=QUERY_PROMPT,
        query_prompt_utf8_sha256=QUERY_PROMPT_UTF8_SHA256,
        document_template=DOCUMENT_TEMPLATE,
        input_version=INPUT_VERSION,
        dimensions=DIMENSIONS,
        dtype=DTYPE,
        normalization=NORMALIZATION,
    )
    # Self-consistency: the manifest must not drift from its own locked values.
    assert len(manifest.files) == 5, "exactly five pinned runtime files"
    assert manifest.query_prompt_utf8_sha256 == hashlib.sha256(
        manifest.query_prompt.encode("utf-8")
    ).hexdigest(), "query prompt hash drift"
    return manifest


MODEL_MANIFEST: ModelManifest = _build()


def manifest_json() -> str:
    """Deterministic canonical JSON of the manifest (for evidence manifests)."""
    payload = {
        "profile": MODEL_MANIFEST.profile,
        "repo": MODEL_MANIFEST.repo,
        "revision": MODEL_MANIFEST.revision,
        "files": dict(MODEL_MANIFEST.files),
        "query_prompt_utf8_sha256": MODEL_MANIFEST.query_prompt_utf8_sha256,
        "document_template": MODEL_MANIFEST.document_template,
        "input_version": MODEL_MANIFEST.input_version,
        "dimensions": MODEL_MANIFEST.dimensions,
        "dtype": MODEL_MANIFEST.dtype,
        "normalization": MODEL_MANIFEST.normalization,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def manifest_digest() -> str:
    """SHA-256 of the canonical JSON; stable across runs and consumers."""
    return hashlib.sha256(manifest_json().encode("utf-8")).hexdigest()
