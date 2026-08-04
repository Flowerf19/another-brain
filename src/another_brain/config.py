"""Cross-platform configuration with native user-data/cache paths."""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from platformdirs import user_cache_path, user_data_path

from .errors import ConfigError


def _value(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name, "").strip()
    return value or default


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _value(env, name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value


def _identity(env: Mapping[str, str], name: str, default: str) -> str:
    value = _value(env, name, default)
    if ":" in value or not value:
        raise ConfigError(f"{name} must be non-empty and must not contain ':'")
    return value


def validate_loopback_host(host: str) -> str:
    """Accept numeric loopback literals only; no DNS or wildcard fallback."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ConfigError(
            "MCP_HTTP_HOST must be a numeric loopback address "
            f"(127.0.0.1 or ::1), got {host!r}"
        ) from None
    if not address.is_loopback:
        raise ConfigError(f"MCP_HTTP_HOST must be loopback-only, got {host!r}")
    return host


@dataclass(frozen=True)
class AppConfig:
    brain_id: str
    database_path: Path
    model_dir: Path
    timeline_timezone: str
    http_host: str
    http_port: int
    audit_retention_days: int = 90
    forget_grace_seconds: int = 2_592_000

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AppConfig":
        values = dict(os.environ) if env is None else dict(env)
        data_dir = Path(
            _value(values, "ANOTHER_BRAIN_DATA_DIR", str(user_data_path("another-brain")))
        ).expanduser()
        model_dir = Path(
            _value(
                values,
                "ANOTHER_BRAIN_MODEL_DIR",
                str(user_cache_path("another-brain") / "models"),
            )
        ).expanduser()
        database_path = Path(
            _value(values, "ANOTHER_BRAIN_DATABASE", str(data_dir / "brain.sqlite3"))
        ).expanduser()
        timezone = _value(values, "TIMELINE_TIMEZONE", "Asia/Ho_Chi_Minh")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            raise ConfigError(
                f"TIMELINE_TIMEZONE is unavailable: {timezone!r}; reinstall the "
                "package so the Windows tzdata dependency is present"
            ) from None
        host = validate_loopback_host(_value(values, "MCP_HTTP_HOST", "127.0.0.1"))
        port = _positive_int(values, "MCP_HTTP_PORT", 1905)
        if port > 65_535:
            raise ConfigError(f"MCP_HTTP_PORT must be <= 65535, got {port}")
        return cls(
            brain_id=_identity(values, "BRAIN_ID", "default"),
            database_path=database_path,
            model_dir=model_dir,
            timeline_timezone=timezone,
            http_host=host,
            http_port=port,
            audit_retention_days=_positive_int(values, "AUDIT_RETENTION_DAYS", 90),
            forget_grace_seconds=_positive_int(
                values, "FORGET_GRACE_SECONDS", 2_592_000
            ),
        )

    def create_directories(self) -> None:
        for directory in (self.database_path.parent, self.model_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
