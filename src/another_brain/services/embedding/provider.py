"""Harrier q4 raw ONNX Runtime CPU provider (TASK-017).

Direct ``sentence_embedding`` — no SentenceTransformers, no Torch. Payloads
are locked input version 2: documents are exactly
``topic.replace("-", " ") + "\\n" + summary.strip()`` with no prompt; queries
are exactly ``QUERY_PROMPT + query.strip()`` (empty stripped queries are
rejected). The graph emits L2-normalized FLOAT32 ``[batch, 640]`` directly;
every batch is validated for dtype/shape/finiteness/unit norm before it
becomes an :class:`EmbeddingVector`.

Load is lazy and serialized: the first call initializes the tokenizer and
session under a lock; concurrent callers block until it finishes and reuse
the one session (TASK-044). The manifest's 640 is the contract — at load the
provider asserts the graph's ``sentence_embedding`` output shape against it,
so a foreign graph fails loudly into ``EmbeddingHealth.ERROR`` instead of
silently producing mismatched vectors. ``health()`` never loads the model.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from another_brain.domain.models import EmbeddingVector
from another_brain.errors import (
    EmbeddingLoadError,
    EmbeddingOutputError,
    ModelNotInstalledError,
)
from another_brain.services.embedding.model_manifest import (
    MODEL_MANIFEST,
    ModelManifest,
    manifest_digest,
)
from another_brain.services.embedding.payloads import document_payload, query_payload
from another_brain.protocols import EmbeddingHealth

MARKER_NAME = ".installed.json"
NORM_TOLERANCE = 1e-3
NOT_INSTALLED_MESSAGE = "model profile not installed; run `another-brain model pull`"


class ONNXEmbeddingProvider:
    """One lazy, thread-safe, process-local embedding session."""

    def __init__(
        self,
        model_dir: Path,
        *,
        manifest: ModelManifest = MODEL_MANIFEST,
    ) -> None:
        self._model_dir = Path(model_dir)
        self._manifest = manifest
        self._session: ort.InferenceSession | None = None
        self._tokenizer: Tokenizer | None = None
        self._state = EmbeddingHealth.NOT_LOADED
        self._error: str | None = None
        self._failure: tuple[type, str] | None = None  # (exc_type, message) — sticky
        self._load_lock = threading.Lock()

    # -- health (never loads) ----------------------------------------------

    def health(self) -> EmbeddingHealth:
        """Current load state; never triggers a model load."""
        return self._state

    def load_error(self) -> str | None:
        """Detail behind ``ERROR`` state, if any."""
        return self._error

    def close(self) -> None:
        """Drop session/tokenizer references (MCP shutdown path, TASK-044).

        Idempotent. State returns to ``NOT_LOADED`` and a later embed lazily
        re-loads; call only when no embed is in flight. RSS may not drop
        until process exit because ORT keeps its allocation arena cached.
        """
        with self._load_lock:
            self._session = None
            self._tokenizer = None
            self._state = EmbeddingHealth.NOT_LOADED
            self._error = None
            self._failure = None

    # -- payloads (locked input version 2) ----------------------------------

    def embed_document(self, *, topic: str, summary: str) -> EmbeddingVector:
        """Embed one document payload (no prompt)."""
        return self._embed([document_payload(topic, summary)])[0]

    def embed_query(self, query: str) -> EmbeddingVector:
        """Embed one prompted query; empty stripped queries are rejected."""
        return self._embed([query_payload(query)])[0]

    # -- internals ----------------------------------------------------------

    def _marker_ok(self) -> bool:
        try:
            payload = json.loads(
                (self._model_dir / MARKER_NAME).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return False
        return payload.get("manifest_digest") == manifest_digest(self._manifest)

    def _ensure_loaded(self) -> tuple[Tokenizer, ort.InferenceSession]:
        if self._session is not None:
            return self._tokenizer, self._session  # type: ignore[return-value]
        if self._failure is not None:  # sticky: no retry after a load error
            exc_type, message = self._failure
            raise exc_type(message)
        with self._load_lock:  # serialize the first load
            if self._session is None and self._failure is None:
                self._load()
            if self._failure is not None:
                exc_type, message = self._failure
                raise exc_type(message)
            return self._tokenizer, self._session  # type: ignore[return-value]

    def _fail(self, exc: EmbeddingError) -> None:
        """Record the load failure (health ERROR, sticky) and raise."""
        self._state = EmbeddingHealth.ERROR
        self._error = str(exc)
        self._failure = (type(exc), str(exc))
        raise exc

    def _load(self) -> None:
        if not self._marker_ok():
            self._fail(ModelNotInstalledError(NOT_INSTALLED_MESSAGE))
        onnx_rel = next(
            (
                name
                for name, _ in self._manifest.files
                if name.endswith(".onnx") and not name.endswith(".onnx_data")
            ),
            None,
        )
        if onnx_rel is None:
            self._fail(
                EmbeddingLoadError(f"no .onnx graph in manifest {self._manifest.profile}")
            )
        try:
            tokenizer = Tokenizer.from_file(str(self._model_dir / "tokenizer.json"))
            session = ort.InferenceSession(
                str(self._model_dir / onnx_rel),
                providers=["CPUExecutionProvider"],
            )
            self._assert_graph_shape(session)
        except EmbeddingLoadError as exc:
            self._fail(exc)
        except Exception as exc:  # noqa: BLE001 - any init failure is a load error
            self._fail(EmbeddingLoadError(f"model load failed: {exc}"))
        self._tokenizer = tokenizer
        self._session = session
        self._state = EmbeddingHealth.READY

    def _assert_graph_shape(self, session: ort.InferenceSession) -> None:
        """The graph is the runtime truth: its output must match the manifest."""
        outputs = session.get_outputs()
        names = [o.name for o in outputs]
        match = next((o for o in outputs if o.name == "sentence_embedding"), None)
        if match is None:
            raise EmbeddingLoadError(
                f"sentence_embedding output missing from graph; got {names}"
            )
        shape = list(match.shape)
        dim = shape[1] if len(shape) == 2 else None
        if dim != self._manifest.dimensions:
            raise EmbeddingLoadError(
                f"graph sentence_embedding dim {dim} != manifest "
                f"{self._manifest.dimensions} (shape {shape}); refusing to embed"
            )

    def _embed(self, texts: list[str]) -> list[EmbeddingVector]:
        tokenizer, session = self._ensure_loaded()
        try:
            encodings = tokenizer.encode_batch(texts)
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingOutputError(f"tokenizer failed: {exc}") from exc
        max_len = max((len(e.ids) for e in encodings), default=0)

        def padded(values: list[list[int]]) -> np.ndarray:
            return np.array(
                [v + [0] * (max_len - len(v)) for v in values], dtype=np.int64
            )

        input_names = {i.name for i in session.get_inputs()}
        feed: dict[str, np.ndarray] = {"input_ids": padded([e.ids for e in encodings])}
        if "attention_mask" in input_names:
            feed["attention_mask"] = padded([e.attention_mask for e in encodings])
        if "token_type_ids" in input_names:
            feed["token_type_ids"] = padded([e.type_ids for e in encodings])
        try:
            outputs = session.run(["sentence_embedding"], feed)[0]
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingOutputError(f"onnx run failed: {exc}") from exc
        return _validate_outputs(outputs, self._manifest.dimensions)


def _validate_outputs(outputs: object, dimensions: int) -> list[EmbeddingVector]:
    if not isinstance(outputs, np.ndarray) or outputs.dtype != np.float32:
        raise EmbeddingOutputError(
            f"expected FLOAT32 output, got {getattr(outputs, 'dtype', type(outputs).__name__)}"
        )
    if outputs.ndim != 2 or outputs.shape[1] != dimensions:
        raise EmbeddingOutputError(
            f"expected [batch, {dimensions}], got {outputs.shape}"
        )
    if not np.all(np.isfinite(outputs)):
        raise EmbeddingOutputError("non-finite embedding output")
    norms = np.linalg.norm(outputs, axis=1)
    if not np.allclose(norms, 1.0, atol=NORM_TOLERANCE):
        raise EmbeddingOutputError(f"output not unit norm: norms={norms}")
    return [EmbeddingVector(values=outputs[i].copy()) for i in range(outputs.shape[0])]
