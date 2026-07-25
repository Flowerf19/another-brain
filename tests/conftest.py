"""Shared pytest bootstrap.

Integration tests read REDIS_TEST_URL (default redis://localhost:1905,
matching the compose default REDIS_PORT). If .env overrides REDIS_PORT,
derive the URL from that port so a bare `uv run pytest` targets the right
server instead of anything else on the default port — without overriding an
explicit REDIS_TEST_URL a CI run may set.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _redis_port_from_dotenv() -> str | None:
    if not _ENV_FILE.is_file():
        return None
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("REDIS_PORT="):
            return line.split("=", 1)[1].strip() or None
    return None


if "REDIS_TEST_URL" not in os.environ:
    port = os.environ.get("REDIS_PORT") or _redis_port_from_dotenv()
    if port:
        os.environ["REDIS_TEST_URL"] = f"redis://localhost:{port}"
