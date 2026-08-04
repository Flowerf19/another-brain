"""Raw ONNX Runtime CPU embedding provider."""
from __future__ import annotations

import asyncio
import math
import threading
from pathlib import Path
from typing import Any

import numpy as np

from ..errors import ConfigError, ValidationError
from .installer import require_model
from .manifest import MODEL_DIMENSION, MODEL_REPOSITORY
from .payload import document_payload, query_payload


class OnnxEmbeddingProvider:
    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)
        self.model_name = MODEL_REPOSITORY
        self.dim = MODEL_DIMENSION
        self._session: Any = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()
        self._load_error: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self._session is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    async def embed_document(self, topic: str, summary: str) -> tuple[float, ...]:
        return await asyncio.to_thread(self._encode, document_payload(topic, summary), 256)

    async def embed_query(self, query: str) -> tuple[float, ...]:
        return await asyncio.to_thread(self._encode, query_payload(query), 128)

    def validate_content(self, content: str) -> None:
        self._load()
        count = len(self._tokenizer.encode(content, add_special_tokens=False).ids)
        if count > 1_024:
            raise ValidationError(f"content has {count} tokens; allowed maximum is 1024")

    def validate_topic(self, topic: str) -> None:
        self._load()
        humanized = topic.replace("-", " ")
        count = len(self._tokenizer.encode(humanized, add_special_tokens=False).ids)
        if count > 12:
            raise ValidationError(f"topic has {count} tokens; allowed maximum is 12")

    def _load(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            try:
                require_model(self.model_dir)
                from onnxruntime import InferenceSession, SessionOptions
                from tokenizers import Tokenizer

                options = SessionOptions()
                options.intra_op_num_threads = max(1, min(4, (os_cpu_count() or 1)))
                self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
                self._session = InferenceSession(
                    str(self.model_dir / "onnx" / "model_q4.onnx"),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                self._load_error = None
            except Exception as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"
                self._session = None
                self._tokenizer = None
                raise

    def _encode(self, text: str, maximum: int) -> tuple[float, ...]:
        self._load()
        encoded = self._tokenizer.encode(text, add_special_tokens=True)
        if len(encoded.ids) > maximum:
            raise ValidationError(
                f"embedding input has {len(encoded.ids)} tokens; allowed maximum is {maximum}"
            )
        arrays = {
            "input_ids": np.asarray([encoded.ids], dtype=np.int64),
            "attention_mask": np.asarray([encoded.attention_mask], dtype=np.int64),
            "token_type_ids": np.asarray([encoded.type_ids], dtype=np.int64),
        }
        input_names = {item.name for item in self._session.get_inputs()}
        feed = {name: value for name, value in arrays.items() if name in input_names}
        output_names = {item.name for item in self._session.get_outputs()}
        wanted = "sentence_embedding" if "sentence_embedding" in output_names else None
        raw = self._session.run([wanted] if wanted else None, feed)[0]
        vector = np.asarray(raw, dtype=np.float32).reshape(-1)
        if vector.size != MODEL_DIMENSION or not np.isfinite(vector).all():
            raise ValidationError(
                f"model output must be finite FLOAT32[{MODEL_DIMENSION}], got {vector.shape}"
            )
        norm = float(np.linalg.norm(vector))
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-3):
            raise ValidationError(f"model output is not unit normalized (norm={norm})")
        return tuple(float(value) for value in vector)


def os_cpu_count() -> int | None:
    import os

    return os.cpu_count()
