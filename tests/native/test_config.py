from pathlib import Path

import pytest

from another_brain.config import AppConfig, validate_loopback_host
from another_brain.errors import ConfigError


def test_windows_timezone_dependency_and_path_overrides(tmp_path):
    config = AppConfig.from_env(
        {
            "ANOTHER_BRAIN_DATA_DIR": str(tmp_path / "data"),
            "ANOTHER_BRAIN_MODEL_DIR": str(tmp_path / "models"),
            "TIMELINE_TIMEZONE": "Asia/Ho_Chi_Minh",
        }
    )
    assert config.database_path == tmp_path / "data" / "brain.sqlite3"
    assert config.model_dir == tmp_path / "models"


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "192.168.1.2"])
def test_http_rejects_non_numeric_or_non_loopback_hosts(host):
    with pytest.raises(ConfigError):
        validate_loopback_host(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.10.20.30", "::1"])
def test_http_accepts_numeric_loopback_hosts(host):
    assert validate_loopback_host(host) == host
