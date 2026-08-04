from __future__ import annotations

from dataclasses import dataclass

import pytest

from another_brain.config import AppConfig
from another_brain.retrieval.service import HybridRetriever
from another_brain.service import MemoryService
from another_brain.storage.repository import SQLiteRepository


def unit_vector(axis: int = 0) -> tuple[float, ...]:
    values = [0.0] * 640
    values[axis] = 1.0
    return tuple(values)


@dataclass
class FakeEmbedder:
    document_axis: int = 0
    query_axis: int = 0
    model_name: str = "fake-harrier"
    dim: int = 640
    is_loaded: bool = True
    load_error: str | None = None

    def __post_init__(self) -> None:
        self.documents: list[tuple[str, str]] = []
        self.queries: list[str] = []
        self.validated_topics: list[str] = []
        self.validated_content: list[str] = []

    def validate_topic(self, topic: str) -> None:
        self.validated_topics.append(topic)

    def validate_content(self, content: str) -> None:
        self.validated_content.append(content)

    async def embed_document(self, topic: str, summary: str) -> tuple[float, ...]:
        self.documents.append((topic, summary))
        return unit_vector(self.document_axis)

    async def embed_query(self, query: str) -> tuple[float, ...]:
        self.queries.append(query)
        return unit_vector(self.query_axis)


@pytest.fixture
def app_config(tmp_path) -> AppConfig:
    return AppConfig.from_env(
        {
            "BRAIN_ID": "test-brain",
            "ANOTHER_BRAIN_DATA_DIR": str(tmp_path / "data"),
            "ANOTHER_BRAIN_MODEL_DIR": str(tmp_path / "model"),
            "TIMELINE_TIMEZONE": "Asia/Ho_Chi_Minh",
        }
    )


@pytest.fixture
def repository(app_config: AppConfig) -> SQLiteRepository:
    return SQLiteRepository(
        app_config.database_path,
        timezone=app_config.timeline_timezone,
        audit_retention_days=app_config.audit_retention_days,
    )


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def memory_service(
    app_config: AppConfig,
    repository: SQLiteRepository,
    fake_embedder: FakeEmbedder,
) -> MemoryService:
    return MemoryService(
        app_config,
        repository,
        HybridRetriever(repository),
        fake_embedder,
    )
