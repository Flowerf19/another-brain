"""TASK-038/039: config validation matrix and platformdirs path resolution."""
import stat

import pytest

from another_brain.config import (
    APP_NAME,
    AUDIT_RETENTION_DAYS,
    BM25_WEIGHTS,
    BUDGET_CONTENT_TOKENS,
    BUDGET_DOCUMENT_TOKENS,
    BUDGET_QUERY_TOKENS,
    BUDGET_TOPIC_TOKENS,
    CANDIDATE_LIMIT,
    COSINE_FLOOR_MICRO,
    DATABASE_FILENAME,
    DISABLE_SQLITE_VEC_ENV,
    FORGET_GRACE_DAYS,
    RRF_K,
    TOP_K,
    TTL_DAYS_BY_IMPORTANCE,
    AppConfig,
    HttpConfig,
)
from another_brain.errors import ConfigError


class TestHttpBindValidation:
    @pytest.mark.parametrize("host", ["127.0.0.1", "127.1.2.3", "127.255.255.254", "::1"])
    def test_numeric_loopback_accepted(self, host):
        assert HttpConfig(host=host, port=1905).host == host

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",        # hostname, even though it resolves to loopback
            "example.com",      # hostname
            "0.0.0.0",          # wildcard
            "::",               # wildcard
            "192.168.1.10",     # LAN
            "10.0.0.5",         # LAN
            "172.16.0.2",       # LAN
            "8.8.8.8",          # public
            "169.254.1.1",      # link-local
            "fe80::1",          # link-local v6
            "224.0.0.1",        # multicast
            "",                 # empty
            "127.0.0.1:1905",   # host:port is not a host
        ],
    )
    def test_non_loopback_rejected(self, host):
        with pytest.raises(ConfigError):
            HttpConfig(host=host, port=1905)

    @pytest.mark.parametrize("port", [0, -1, 65536, "abc", ""])
    def test_invalid_ports_rejected(self, port):
        with pytest.raises(ConfigError):
            HttpConfig(host="127.0.0.1", port=port)

    def test_env_host_port(self):
        cfg = AppConfig.from_env(
            {"MCP_HTTP_HOST": "127.0.0.2", "MCP_HTTP_PORT": "2905", **BASE_ENV}
        )
        assert cfg.http.host == "127.0.0.2"
        assert cfg.http.port == 2905

    def test_env_rejects_hostname(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"MCP_HTTP_HOST": "localhost", **BASE_ENV})


BASE_ENV = {"BRAIN_DATA_DIR": "/tmp/ab-data", "BRAIN_MODEL_CACHE_DIR": "/tmp/ab-models"}


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig.from_env(BASE_ENV)
        assert cfg.brain_id == "default"
        assert cfg.timeline_timezone == "UTC"
        assert cfg.http == HttpConfig(host="127.0.0.1", port=1905)
        assert cfg.database_path.name == DATABASE_FILENAME
        assert cfg.database_path.parent == cfg.data_dir
        assert cfg.disable_sqlite_vec is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", "on", " ON "])
    def test_disable_sqlite_vec_truthy(self, value):
        cfg = AppConfig.from_env({DISABLE_SQLITE_VEC_ENV: value, **BASE_ENV})
        assert cfg.disable_sqlite_vec is True

    @pytest.mark.parametrize(
        "value", ["", "0", "false", "no", "off", "2", "enabled", "anything"]
    )
    def test_disable_sqlite_vec_falsy(self, value):
        cfg = AppConfig.from_env({DISABLE_SQLITE_VEC_ENV: value, **BASE_ENV})
        assert cfg.disable_sqlite_vec is False

    def test_brain_id_colon_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"BRAIN_ID": "a:b", **BASE_ENV})

    def test_invalid_timezone_rejected(self):
        with pytest.raises(ConfigError):
            AppConfig.from_env({"TIMELINE_TIMEZONE": "Not/AZone", **BASE_ENV})

    def test_valid_timezone_accepted(self):
        cfg = AppConfig.from_env({"TIMELINE_TIMEZONE": "Asia/Ho_Chi_Minh", **BASE_ENV})
        assert cfg.timeline_timezone == "Asia/Ho_Chi_Minh"

    def test_dir_overrides(self, tmp_path):
        data = tmp_path / "data"
        models = tmp_path / "models"
        cfg = AppConfig.from_env(
            {"BRAIN_DATA_DIR": str(data), "BRAIN_MODEL_CACHE_DIR": str(models)}
        )
        assert cfg.data_dir == data
        assert cfg.model_cache_dir == models

    def test_platformdirs_defaults(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
        cfg = AppConfig.from_env({})
        assert str(cfg.data_dir).startswith(str(tmp_path / "xdg-data"))
        assert APP_NAME in str(cfg.data_dir)
        assert str(cfg.model_cache_dir).startswith(str(tmp_path / "xdg-cache"))
        assert "models" in str(cfg.model_cache_dir)

    def test_ensure_directories_user_only(self, tmp_path):
        cfg = AppConfig.from_env(
            {
                "BRAIN_DATA_DIR": str(tmp_path / "d"),
                "BRAIN_MODEL_CACHE_DIR": str(tmp_path / "m"),
            }
        )
        cfg.ensure_directories()
        for directory in (cfg.data_dir, cfg.model_cache_dir):
            assert directory.is_dir()
            mode = stat.S_IMODE(directory.stat().st_mode)
            assert mode == 0o700


class TestFixedContracts:
    """The locked product contracts exist as constants, not env knobs."""

    def test_retrieval_contract(self):
        assert (TOP_K, CANDIDATE_LIMIT, COSINE_FLOOR_MICRO, RRF_K) == (5, 50, 300_000, 60)
        assert BM25_WEIGHTS == (5, 3, 1)

    def test_token_budgets(self):
        assert (BUDGET_TOPIC_TOKENS, BUDGET_DOCUMENT_TOKENS) == (12, 256)
        assert (BUDGET_QUERY_TOKENS, BUDGET_CONTENT_TOKENS) == (128, 1024)

    def test_retention_contract(self):
        assert TTL_DAYS_BY_IMPORTANCE == {5: 365, 4: 180, 3: 90, 2: 30, 1: 7}
        assert FORGET_GRACE_DAYS == 30
        assert AUDIT_RETENTION_DAYS == 90
