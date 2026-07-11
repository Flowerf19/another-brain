"""Unit tests for AppConfig.from_env (Step 04 section 7, Step 03 providers)."""
import pytest

from config import AppConfig
from errors import ConfigError
from memory.retention import DEFAULT_TTL_BY_IMPORTANCE
from models.policy import ModelInstallPolicy


class TestDefaults:
    def test_all_defaults(self):
        config = AppConfig.from_env({})
        assert config.brain_id == "default"
        assert config.agent_id == "default"
        assert config.redis.url == "redis://localhost:6379"
        assert config.redis.key_prefix == "ab"
        assert config.redis.index_name == "ab:idx:memory"
        assert config.redis.vector_dtype == "FLOAT32"
        assert config.redis.distance_metric == "COSINE"
        assert config.redis.index_mode == "HNSW"
        assert config.embedding.provider == "local"
        assert config.embedding.dim == 640
        assert config.embedding.normalize is True
        assert config.search.top_k == 20
        assert config.search.fusion_k == 60
        assert config.search.min_cosine == 0.30
        assert config.ttl_by_importance == DEFAULT_TTL_BY_IMPORTANCE
        assert config.audit_retention_days == 90
        assert config.content_max_chars == 4000
        assert config.forget_grace_seconds == 2_592_000
        assert config.timeline_timezone == "Asia/Ho_Chi_Minh"
        assert config.schema_version == 1

    def test_index_name_follows_custom_prefix(self):
        config = AppConfig.from_env({"REDIS_KEY_PREFIX": "xx"})
        assert config.redis.index_name == "xx:idx:memory"


class TestTtlOverrides:
    FULL = {f"TTL_IMPORTANCE_{i}": str(i * 100) for i in (1, 2, 3, 4, 5)}

    def test_full_override_accepted(self):
        config = AppConfig.from_env(self.FULL)
        assert config.ttl_by_importance == {1: 100, 2: 200, 3: 300, 4: 400, 5: 500}

    def test_partial_override_rejected(self):
        partial = dict(self.FULL)
        del partial["TTL_IMPORTANCE_3"]
        with pytest.raises(ConfigError, match="all-or-none"):
            AppConfig.from_env(partial)

    def test_non_positive_override_rejected(self):
        bad = dict(self.FULL, TTL_IMPORTANCE_1="0")
        with pytest.raises(ConfigError):
            AppConfig.from_env(bad)


class TestValidation:
    def test_brain_id_with_colon_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"BRAIN_ID": "a:b"})

    def test_non_float32_dtype_rejected(self):
        with pytest.raises(ConfigError, match="FLOAT32"):
            AppConfig.from_env({"REDIS_VECTOR_DTYPE": "INT8"})

    def test_non_cosine_metric_rejected(self):
        with pytest.raises(ConfigError, match="COSINE"):
            AppConfig.from_env({"REDIS_DISTANCE_METRIC": "L2"})

    def test_flat_index_mode_allowed(self):
        config = AppConfig.from_env({"REDIS_VECTOR_INDEX_MODE": "FLAT"})
        assert config.redis.index_mode == "FLAT"

    def test_unknown_index_mode_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"REDIS_VECTOR_INDEX_MODE": "IVF"})

    def test_min_cosine_out_of_range_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"SEARCH_MIN_COSINE": "1.5"})

    def test_unknown_provider_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"EMBEDDING_PROVIDER": "bedrock"})
        with pytest.raises(ConfigError):
            AppConfig.from_env({"MEMORY_MODEL_PROVIDER": "bedrock"})

    def test_bad_timezone_rejected(self):
        with pytest.raises(ConfigError, match="timezone"):
            AppConfig.from_env({"TIMELINE_TIMEZONE": "Mars/Olympus"})

    def test_bad_int_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"SEARCH_TOP_K": "twenty"})

    def test_bool_parsing(self):
        assert AppConfig.from_env({"NORMALIZE_EMBEDDINGS": "false"}).embedding.normalize is False
        with pytest.raises(ConfigError):
            AppConfig.from_env({"NORMALIZE_EMBEDDINGS": "maybe"})


class TestModelInstall:
    def test_defaults(self):
        config = AppConfig.from_env({})
        assert config.model_install.download_policy is ModelInstallPolicy.MANUAL
        assert config.model_install.cache_dir == ".cache/another-brain/models"
        assert config.model_install.allow_network is False
        assert config.model_install.pinned_revision == ""
        assert config.model_install.weight_precision == "auto"
        assert config.model_install.output_precision == "float32"
        assert config.embedding.query_prompt_name == ""

    def test_download_policy_parsed(self):
        config = AppConfig.from_env({"MODEL_DOWNLOAD_POLICY": "lazy"})
        assert config.model_install.download_policy is ModelInstallPolicy.LAZY

    def test_invalid_download_policy_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"MODEL_DOWNLOAD_POLICY": "sometimes"})

    def test_postponed_weight_precision_rejected(self):
        with pytest.raises(ConfigError, match="postponed"):
            AppConfig.from_env({"MODEL_WEIGHT_PRECISION": "q4"})

    def test_unknown_weight_precision_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"MODEL_WEIGHT_PRECISION": "xxx"})

    def test_non_float32_output_precision_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"EMBEDDING_OUTPUT_PRECISION": "int8"})

    def test_allow_network_parsed(self):
        config = AppConfig.from_env({"MODEL_ALLOW_NETWORK": "true"})
        assert config.model_install.allow_network is True
