"""EmbeddingProvider — embeds the canonical summary and search queries.

This module stays focused on embedding calls after a provider is ready
(Step 03): download/install logic lives in src/models/, and the provider
receives an already-resolved local model path.

Documents (memory summaries) and queries are embedded asymmetrically: Harrier
defines a query prompt, so queries go through the configured prompt name while
documents are embedded as plain passages.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Protocol

from errors import ConfigError
from memory.models import EmbeddingVector
from models.runtime import ModelRuntimeProfile

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    """What MemoryService needs from any embedding backend."""

    @property
    def model_name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed_document(self, text: str) -> EmbeddingVector: ...

    async def embed_query(self, text: str) -> EmbeddingVector: ...


class LocalEmbeddingProvider:
    """SentenceTransformers-backed provider (Step 03 Harrier profile).

    The model loads lazily on the first embed call — startup stays fast and
    `model status`/health never force a multi-hundred-MB load. Loading and
    encoding run in a worker thread; the event loop is never blocked.
    """

    def __init__(
        self,
        model_path: Path | str,
        *,
        model_name: str,
        dim: int,
        profile: ModelRuntimeProfile,
        model_factory: Callable[[], Any] | None = None,
    ):
        self._model_path = str(model_path)
        self._model_name = model_name
        self._dim = dim
        self._profile = profile
        self._model_factory = model_factory
        self._model: Any = None
        self._query_prompt: str | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    async def embed_document(self, text: str) -> EmbeddingVector:
        return await asyncio.to_thread(self._encode, text, False)

    async def embed_query(self, text: str) -> EmbeddingVector:
        return await asyncio.to_thread(self._encode, text, True)

    # ---------------------------------------------------------------- loading

    def _load(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            if self._model_factory is not None:
                model = self._model_factory()
            else:
                model = self._load_sentence_transformer()
            self._query_prompt = self._resolve_query_prompt(model)
            self._model = model
            return model

    def _load_sentence_transformer(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ConfigError(
                "EMBEDDING_PROVIDER=local requires the 'local' extra: "
                "uv sync --extra local"
            ) from None
        device = None if self._profile.device == "auto" else self._profile.device
        model_kwargs: dict[str, Any] = {}
        dtype_name = self._profile.torch_dtype_name()
        if dtype_name is not None:
            import torch

            model_kwargs["torch_dtype"] = getattr(torch, dtype_name)
        logger.info(
            "Loading embedding model %s from %s (device=%s, weights=%s)",
            self._model_name, self._model_path,
            device or "auto", self._profile.weight_precision,
        )
        return SentenceTransformer(
            self._model_path,
            device=device,
            local_files_only=True,
            model_kwargs=model_kwargs or None,
        )

    def _resolve_query_prompt(self, model: Any) -> str | None:
        """Validate the configured query prompt against the model's prompt
        table — a missing prompt is a config error, not a silent fallback to
        unprompted passage embedding (Step 03 Harrier notes)."""
        wanted = self._profile.query_prompt_name
        if not wanted:
            return None
        prompts = getattr(model, "prompts", None) or {}
        if wanted not in prompts:
            raise ConfigError(
                f"model {self._model_name!r} does not define query prompt "
                f"{wanted!r}; available prompts: {sorted(prompts) or 'none'}"
            )
        return wanted

    # --------------------------------------------------------------- encoding

    def _encode(self, text: str, is_query: bool) -> EmbeddingVector:
        model = self._model if self._model is not None else self._load()
        kwargs: dict[str, Any] = {"normalize_embeddings": self._profile.normalize}
        if is_query and self._query_prompt is not None:
            kwargs["prompt_name"] = self._query_prompt
        raw = model.encode(text, **kwargs)
        values = [float(v) for v in raw]
        return EmbeddingVector.from_list(values, self._dim)
