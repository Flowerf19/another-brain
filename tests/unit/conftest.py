"""Shared SQL fixtures for the services/sql unit tests."""
from __future__ import annotations

import math

import numpy as np
import pytest

from another_brain.config import AppConfig
from another_brain.domain.models import EmbeddingVector
from another_brain.errors import ValidationError
from another_brain.protocols import EmbeddingHealth
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.migrations import migrate

PROFILE_SQL = (
    "INSERT INTO embedding_profiles(profile_id, model_repo, model_revision,"
    " variant, dimension, dtype, normalized, tokenizer_sha256, config_sha256,"
    " prompt_utf8_sha256, query_prompt, input_version, created_at_ms)"
    " VALUES ('q4', 'repo', 'rev', 'q4', 640, 'float32', 1, ?, ?, ?, 'q', 2, 1)"
)


@pytest.fixture
def sql_factory(tmp_path) -> SQLiteConnectionFactory:
    """Bootstrapped, migrated v1 database with the locked q4 profile row."""
    factory = SQLiteConnectionFactory(tmp_path / "brain.sqlite3")
    factory.bootstrap()
    migrate(factory.db_path)
    with factory.connect() as con:
        con.connection.execute(PROFILE_SQL, ("a" * 64, "a" * 64, "a" * 64))
        con.connection.commit()
    return factory


# ---------------------------------------------------------------------------
# Service/tool-level fixtures (TASK-068): a MemoryService over temp SQLite
# with deterministic fakes at the Protocol seams.
#
# The fake profile row above doubles as the runtime row here: its profile_id
# IS MODEL_MANIFEST.profile ("q4"), so the FK on memories.profile_id and the
# health probe's manifest match both pass. Its junk hashes are invisible —
# nothing on the read path compares them.
# ---------------------------------------------------------------------------

BASE_MS = 1_752_200_000_000
"""Fixed wall clock for service tests; advance it, never sleep."""


class FakeClock:
    """Controllable epoch-ms clock injected into service/repository/retriever."""

    def __init__(self, start_ms: int = BASE_MS) -> None:
        self._now = start_ms

    def __call__(self) -> int:
        return self._now

    def advance_ms(self, delta_ms: int) -> None:
        self._now += delta_ms

    def advance_days(self, days: float) -> None:
        self._now += int(days * 86_400_000)


def unit_vector(dim: int = 640) -> EmbeddingVector:
    """The e1 basis vector — the fake embedder's default for every input."""
    values = np.zeros(dim, dtype=np.float32)
    values[0] = 1.0
    return EmbeddingVector(values=values)


def basis_vector(index: int, dim: int = 640) -> EmbeddingVector:
    """Any basis vector; orthogonal to unit_vector() when index != 0."""
    values = np.zeros(dim, dtype=np.float32)
    values[index] = 1.0
    return EmbeddingVector(values=values)


def cosine_vector(cosine: float, dim: int = 640) -> EmbeddingVector:
    """Unit vector whose cosine to unit_vector() is ``cosine`` (float32-stable)."""
    values = np.zeros(dim, dtype=np.float64)
    values[0] = cosine
    values[1] = math.sqrt(1.0 - cosine * cosine)
    return EmbeddingVector(values=values.astype(np.float32))


class FakeEmbedder:
    """Deterministic EmbeddingProvider: e1 for everything, per-input overrides.

    Default cosine between any doc and any query is 1.0. Register a
    basis_vector(1) query to force cosine 0.0 (lexical-only retrieval) or a
    cosine_vector(x) to land an exact micro-cosine.
    """

    def __init__(self) -> None:
        self.default = unit_vector()
        self.documents: dict[tuple[str, str], EmbeddingVector] = {}
        self.queries: dict[str, EmbeddingVector] = {}

    def set_document(self, topic: str, summary: str, vector: EmbeddingVector) -> None:
        self.documents[(topic, summary)] = vector

    def set_query(self, query: str, vector: EmbeddingVector) -> None:
        self.queries[query] = vector

    def embed_document(self, *, topic: str, summary: str) -> EmbeddingVector:
        return self.documents.get((topic, summary), self.default)

    def embed_query(self, query: str) -> EmbeddingVector:
        return self.queries.get(query, self.default)

    def health(self) -> EmbeddingHealth:
        return EmbeddingHealth.NOT_LOADED


class FakeBudgets:
    """No-op token budgets; keeps only the real empty-query rejection.

    Real budget limits live in TokenBudgetValidator and are covered by
    test_budgets.py; service/tool tests must not depend on tokenizer counts.
    """

    def validate_remember(self, *, topic: str, summary: str, content: str) -> None:
        pass

    def validate_query(self, query: str) -> None:
        if not query.strip():
            raise ValidationError("query must not be empty")


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def make_service(sql_factory, tmp_path, fake_clock, fake_embedder):
    """MemoryService factory: ``make_service(brain_id=...)`` binds one brain.

    Two instances over the same sql_factory give cross-brain isolation tests.
    """
    from another_brain.retrieval.service import HybridMemoryRetriever
    from another_brain.services.memory_service import MemoryService
    from another_brain.services.sql.audit import SQLiteAuditRepository
    from another_brain.services.sql.health import SQLiteHealthProbe
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    def _make(brain_id: str = "test-brain") -> MemoryService:
        config = AppConfig(
            brain_id=brain_id,
            timeline_timezone="UTC",
            data_dir=tmp_path,
            model_cache_dir=tmp_path,
        )
        return MemoryService(
            repository=SQLiteMemoryRepository(
                sql_factory, brain_id=brain_id, clock=fake_clock
            ),
            retriever=HybridMemoryRetriever(
                sql_factory, brain_id=brain_id, clock=fake_clock
            ),
            audit=SQLiteAuditRepository(sql_factory, brain_id=brain_id, clock=fake_clock),
            embedder=fake_embedder,
            budgets=FakeBudgets(),
            storage=SQLiteHealthProbe(sql_factory),
            config=config,
            clock=fake_clock,
        )

    return _make


@pytest.fixture
def service(make_service):
    """A MemoryService bound to ``test-brain`` over migrated temp SQLite."""
    return make_service()


@pytest.fixture
def mcp_server(service):
    """The real MCPServer with all eight tools over the service fixture."""
    from mcp.server import MCPServer

    from another_brain.mcp.server import INSTRUCTIONS, SERVER_NAME
    from another_brain.mcp.tools import register_tools

    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    register_tools(server, service)
    return server
