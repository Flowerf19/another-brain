from __future__ import annotations

from pathlib import Path

import pytest

import another_brain.config as config_module
from another_brain.config import AppConfig
from another_brain.errors import ConfigError


def test_native_default_paths_are_platformdirs_paths(monkeypatch, tmp_path):
    data = tmp_path / "platform-data"
    cache = tmp_path / "platform-cache"
    monkeypatch.setattr(config_module, "user_data_path", lambda name: data)
    monkeypatch.setattr(config_module, "user_cache_path", lambda name: cache)
    config = AppConfig.from_env({})
    assert config.database_path == data / "brain.sqlite3"
    assert config.model_dir == cache / "models"


def test_explicit_database_path_wins_over_data_directory(tmp_path):
    explicit = tmp_path / "custom" / "memory.db"
    config = AppConfig.from_env(
        {
            "ANOTHER_BRAIN_DATA_DIR": str(tmp_path / "ignored"),
            "ANOTHER_BRAIN_DATABASE": str(explicit),
            "ANOTHER_BRAIN_MODEL_DIR": str(tmp_path / "model"),
        }
    )
    assert config.database_path == explicit


def test_create_directories_creates_database_parent_and_model_dir(tmp_path):
    config = AppConfig.from_env(
        {
            "ANOTHER_BRAIN_DATABASE": str(tmp_path / "nested" / "brain.db"),
            "ANOTHER_BRAIN_MODEL_DIR": str(tmp_path / "cache" / "model"),
        }
    )
    config.create_directories()
    assert config.database_path.parent.is_dir()
    assert config.model_dir.is_dir()


@pytest.mark.parametrize("env,match", [
    ({"BRAIN_ID": "bad:id"}, "BRAIN_ID"),
    ({"TIMELINE_TIMEZONE": "Missing/Timezone"}, "TIMELINE_TIMEZONE"),
    ({"MCP_HTTP_PORT": "zero"}, "integer"),
    ({"MCP_HTTP_PORT": "0"}, "positive"),
    ({"MCP_HTTP_PORT": "65536"}, "<= 65535"),
    ({"AUDIT_RETENTION_DAYS": "-1"}, "positive"),
    ({"FORGET_GRACE_SECONDS": "0"}, "positive"),
])
def test_invalid_environment_is_rejected(env, match):
    with pytest.raises(ConfigError, match=match):
        AppConfig.from_env(env)


def test_blank_environment_values_fall_back_to_defaults(tmp_path):
    config = AppConfig.from_env(
        {
            "BRAIN_ID": "   ",
            "ANOTHER_BRAIN_DATA_DIR": str(tmp_path),
            "ANOTHER_BRAIN_MODEL_DIR": str(tmp_path / "model"),
        }
    )
    assert config.brain_id == "default"
