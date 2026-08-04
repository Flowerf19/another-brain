import pytest

from another_brain.config import AppConfig
from another_brain.retrieval.service import HybridRetriever
from another_brain.service import MemoryService
from another_brain.storage.repository import SQLiteRepository


def vec(axis=0):
    values = [0.0] * 640
    values[axis] = 1.0
    return tuple(values)


class FakeEmbedder:
    model_name = "fake"
    dim = 640
    is_loaded = True
    load_error = None

    def validate_topic(self, topic):
        return None

    def validate_content(self, content):
        return None

    async def embed_document(self, topic, summary):
        return vec(0)

    async def embed_query(self, query):
        return vec(0)


@pytest.mark.asyncio
async def test_remember_search_get_round_trip(tmp_path):
    config = AppConfig.from_env(
        {
            "BRAIN_ID": "native-test",
            "ANOTHER_BRAIN_DATA_DIR": str(tmp_path),
            "ANOTHER_BRAIN_MODEL_DIR": str(tmp_path / "models"),
        }
    )
    repository = SQLiteRepository(config.database_path, timezone=config.timeline_timezone)
    service = MemoryService(config, repository, HybridRetriever(repository), FakeEmbedder())
    remembered = await service.remember(
        "native-windows",
        "The same wheel runs on Windows and Ubuntu.",
        agent_id="pytest",
        scope="project",
        scope_id="another-brain",
        content="marker CROSSPLATFORM-1",
    )
    found = await service.search(
        "CROSSPLATFORM-1", scope="project", scope_id="another-brain"
    )
    assert [result.memory_id for result in found] == [remembered.memory_id]
    detail = await service.get(remembered.memory_id)
    assert detail is not None
    assert detail.content == "marker CROSSPLATFORM-1"
