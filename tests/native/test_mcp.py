import pytest
from mcp import Client

from another_brain.config import AppConfig
from another_brain.mcp.server import build_mcp_server
from another_brain.retrieval.service import HybridRetriever
from another_brain.service import MemoryService
from another_brain.storage.repository import SQLiteRepository


class FakeEmbedder:
    model_name = "fake"
    dim = 640
    is_loaded = False
    load_error = None

    def validate_topic(self, topic):
        return None

    def validate_content(self, content):
        return None

    async def embed_document(self, topic, summary):
        return (1.0,) + (0.0,) * 639

    async def embed_query(self, query):
        return (1.0,) + (0.0,) * 639


@pytest.mark.asyncio
async def test_all_tools_register_on_mcp_v2(tmp_path):
    config = AppConfig.from_env(
        {
            "ANOTHER_BRAIN_DATA_DIR": str(tmp_path),
            "ANOTHER_BRAIN_MODEL_DIR": str(tmp_path / "models"),
        }
    )
    repository = SQLiteRepository(config.database_path, timezone=config.timeline_timezone)
    service = MemoryService(config, repository, HybridRetriever(repository), FakeEmbedder())
    server = build_mcp_server(service)
    async with Client(server) as client:
        result = await client.list_tools()
    assert {tool.name for tool in result.tools} == {
        "brain_remember",
        "brain_search",
        "brain_recent",
        "brain_get",
        "brain_reinforce",
        "brain_forget",
        "brain_health",
        "brain_audit",
    }
