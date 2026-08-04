"""Native composition root."""
from __future__ import annotations

from .config import AppConfig
from .embedding.provider import OnnxEmbeddingProvider
from .retrieval.service import HybridRetriever
from .service import MemoryService
from .storage.repository import SQLiteRepository


def build_service(config: AppConfig | None = None) -> MemoryService:
    resolved = config or AppConfig.from_env()
    resolved.create_directories()
    repository = SQLiteRepository(
        resolved.database_path,
        timezone=resolved.timeline_timezone,
        audit_retention_days=resolved.audit_retention_days,
    )
    return MemoryService(
        resolved,
        repository,
        HybridRetriever(repository),
        OnnxEmbeddingProvider(resolved.model_dir),
    )
