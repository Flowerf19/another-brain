"""Unit tests for ModelRegistry (Step 03)."""
import pytest

from errors import ConfigError
from models.registry import KIND_EMBEDDING, ModelRegistry

HARRIER = "microsoft/harrier-oss-v1-270m"


class TestKnownModel:
    def test_harrier_resolves_dim_and_prompt(self):
        spec = ModelRegistry().resolve(HARRIER, KIND_EMBEDDING)
        assert spec.expected_dim == 640
        assert spec.query_prompt_name == "web_search_query"
        assert spec.name == HARRIER
        assert spec.kind == KIND_EMBEDDING

    def test_matching_configured_dim_accepted(self):
        spec = ModelRegistry().resolve(HARRIER, KIND_EMBEDDING, configured_dim=640)
        assert spec.expected_dim == 640

    def test_conflicting_configured_dim_rejected(self):
        with pytest.raises(ConfigError):
            ModelRegistry().resolve(HARRIER, KIND_EMBEDDING, configured_dim=999)

    def test_no_configured_dim_uses_known_dim(self):
        spec = ModelRegistry().resolve(HARRIER, KIND_EMBEDDING, configured_dim=None)
        assert spec.expected_dim == 640


class TestUnknownModel:
    def test_configured_dim_passes_through(self):
        spec = ModelRegistry().resolve("some/unknown-model", KIND_EMBEDDING, configured_dim=512)
        assert spec.expected_dim == 512
        assert spec.query_prompt_name is None

    def test_no_configured_dim_is_none(self):
        spec = ModelRegistry().resolve("some/unknown-model", KIND_EMBEDDING)
        assert spec.expected_dim is None


class TestValidation:
    def test_empty_name_rejected(self):
        with pytest.raises(ConfigError, match="no embedding model configured"):
            ModelRegistry().resolve("", KIND_EMBEDDING)

    def test_whitespace_only_name_rejected(self):
        with pytest.raises(ConfigError, match="no embedding model configured"):
            ModelRegistry().resolve("   ", KIND_EMBEDDING)


class TestNonEmbeddingKind:
    def test_non_embedding_kind_rejected(self):
        with pytest.raises(ConfigError, match="unknown model kind"):
            ModelRegistry().resolve("some/model", "other")
