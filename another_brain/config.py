"""Embedded runtime configuration (TASK-038/039).

Everything loads from environment via :meth:`AppConfig.from_env`; every
inconsistency raises :class:`ConfigError` at startup, never later.

Fixed product contracts (Plan 07 "Locked product decisions") live here as
frozen constants — retrieval shape, token budgets, and retention tables are
*not* environment-tunable:

- retrieval: ``top_k=5``, ``candidate_limit=50``, cosine floor micro 300000,
  RRF ``k=60``, BM25 weights 5:3:1;
- token budgets: topic 12 / document 256 / query 128 / content 1024;
- retention: importance 5..1 → 365/180/90/30/7 days, forget grace 30 days,
  audit retention 90 days.

Environment variables:

- ``BRAIN_ID`` — process-bound isolation namespace (default ``default``).
- ``TIMELINE_TIMEZONE`` — IANA name for ``timeline_day`` (default ``UTC``).
- ``BRAIN_DATA_DIR`` / ``BRAIN_MODEL_CACHE_DIR`` — override the platformdirs
  per-user data/cache locations.
- ``BRAIN_DISABLE_SQLITE_VEC`` — force the NumPy vector fallback: the
  sqlite-vec extension is never loaded, so every retrieval/doctor/health
  consumer reports the same capability a machine without the wheel has
  (truthy: ``1``/``true``/``yes``/``on`` case-insensitive).
- ``MCP_HTTP_HOST`` / ``MCP_HTTP_PORT`` — loopback HTTP bind (opt-in via
  ``serve --http``; bare invocation is always stdio). Numeric loopback IP
  literals only: hostnames (including ``localhost``), wildcard, LAN, public,
  and link-local addresses are rejected, as are invalid ports and port zero
  (port zero is test-harness-only and never comes from configuration).
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from platformdirs import user_cache_path, user_data_path

from another_brain.errors import ConfigError

APP_NAME = "another-brain"
DATABASE_FILENAME = "brain.sqlite3"

DEFAULT_BRAIN_ID = "default"
DEFAULT_TIMELINE_TIMEZONE = "UTC"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 1905

#: ``BRAIN_DISABLE_SQLITE_VEC`` env var name (recorded in the vec probe
#: reason when the switch forces the NumPy fallback, TASK-085).
DISABLE_SQLITE_VEC_ENV = "BRAIN_DISABLE_SQLITE_VEC"

TTL_DAYS_BY_IMPORTANCE: dict[int, int] = {5: 365, 4: 180, 3: 90, 2: 30, 1: 7}
FORGET_GRACE_DAYS = 30
AUDIT_RETENTION_DAYS = 90

TOP_K = 5
CANDIDATE_LIMIT = 50
COSINE_FLOOR_MICRO = 300_000
RRF_K = 60
BM25_WEIGHTS = (5, 3, 1)  # topic : summary : content

BUDGET_TOPIC_TOKENS = 12        # humanized topic, no special tokens
BUDGET_DOCUMENT_TOKENS = 256    # topic+summary payload, with special tokens
BUDGET_QUERY_TOKENS = 128       # prompted query, with special tokens
BUDGET_CONTENT_TOKENS = 1024    # lexical-only content, no special tokens

SQLITE_PAGE_SIZE = 16384        # set before the first schema object
SQLITE_BUSY_TIMEOUT_MS = 5000   # locked busy envelope per attempt
SQLITE_SYNCHRONOUS = "NORMAL"    # WAL durability level


def _str(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key, "").strip()
    return value if value else default


def _identity(env: Mapping[str, str], key: str, default: str) -> str:
    value = _str(env, key, default)
    if ":" in value:
        raise ConfigError(f"{key} must not contain ':', got {value!r}")
    return value


def parse_bool(raw: str) -> bool:
    """Truthy env value: 1/true/yes/on, case-insensitive; everything else false."""
    return raw.strip().lower() in ("1", "true", "yes", "on")


def parse_loopback_host(raw: str) -> str:
    """Accept a numeric loopback IP literal; reject everything else.

    Rejected: hostnames (including ``localhost``), wildcard (``0.0.0.0``,
    ``::``), LAN/private, public, link-local, and multicast addresses.
    """
    try:
        address = ipaddress.ip_address(raw.strip())
    except ValueError:
        raise ConfigError(
            f"MCP_HTTP_HOST must be a numeric loopback IP literal"
            f" (127.0.0.0/8 or ::1), not a hostname: {raw!r}"
        ) from None
    if not address.is_loopback:
        raise ConfigError(
            f"MCP_HTTP_HOST must be loopback (127.0.0.0/8 or ::1);"
            f" {raw!r} is not loopback"
        )
    return str(address)


def parse_port(raw: str, *, source: str = "MCP_HTTP_PORT") -> int:
    try:
        port = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{source} must be an integer port, got {raw!r}") from None
    if not 1 <= port <= 65535:
        raise ConfigError(
            f"{source} must be in 1..65535 (port zero is test-harness-only), got {port}"
        )
    return port


@dataclass(frozen=True)
class HttpConfig:
    """Loopback HTTP bind. Only used by ``serve --http``; stdio ignores it."""

    host: str = DEFAULT_HTTP_HOST
    port: int = DEFAULT_HTTP_PORT

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", parse_loopback_host(self.host))
        object.__setattr__(self, "port", parse_port(str(self.port)))


@dataclass(frozen=True)
class AppConfig:
    brain_id: str
    timeline_timezone: str
    data_dir: Path
    model_cache_dir: Path
    http: HttpConfig = field(default_factory=HttpConfig)
    #: Force the NumPy vector fallback (TASK-085): the sqlite-vec extension
    #: is never loaded, so retrieval/doctor/health see the same capability a
    #: machine without the wheel has.
    disable_sqlite_vec: bool = False

    @property
    def database_path(self) -> Path:
        return self.data_dir / DATABASE_FILENAME

    def ensure_directories(self) -> None:
        """Create data/cache dirs with user-only permissions where supported."""
        for directory in (self.data_dir, self.model_cache_dir):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                directory.chmod(0o700)
            except OSError:
                pass  # best effort on non-POSIX filesystems

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AppConfig":
        env = dict(os.environ) if env is None else env

        brain_id = _identity(env, "BRAIN_ID", DEFAULT_BRAIN_ID)

        timeline_timezone = _str(env, "TIMELINE_TIMEZONE", DEFAULT_TIMELINE_TIMEZONE)
        try:
            ZoneInfo(timeline_timezone)
        except Exception:
            raise ConfigError(
                f"TIMELINE_TIMEZONE is not a valid IANA timezone: {timeline_timezone!r}"
            ) from None

        data_dir = Path(_str(env, "BRAIN_DATA_DIR", str(user_data_path(APP_NAME))))
        model_cache_dir = Path(
            _str(env, "BRAIN_MODEL_CACHE_DIR", str(user_cache_path(APP_NAME) / "models"))
        )

        http = HttpConfig(
            host=_str(env, "MCP_HTTP_HOST", DEFAULT_HTTP_HOST),
            port=parse_port(_str(env, "MCP_HTTP_PORT", str(DEFAULT_HTTP_PORT))),
        )
        disable_sqlite_vec = parse_bool(_str(env, DISABLE_SQLITE_VEC_ENV, ""))

        return cls(
            brain_id=brain_id,
            timeline_timezone=timeline_timezone,
            data_dir=data_dir,
            model_cache_dir=model_cache_dir,
            http=http,
            disable_sqlite_vec=disable_sqlite_vec,
        )
