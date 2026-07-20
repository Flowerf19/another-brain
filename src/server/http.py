"""Streamable HTTP transport adapter (optional remote mode). Host/port come
from MCP_HTTP_HOST / MCP_HTTP_PORT, applied when the server is built."""
from __future__ import annotations

from config import AppConfig
from app import build_server


async def run_http(config: AppConfig) -> None:
    server, redis = await build_server(config)
    try:
        await server.run_streamable_http_async()
    finally:
        await redis.aclose()
