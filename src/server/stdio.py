"""MCP stdio transport adapter. Builds the server graph and serves it over
stdio (the shape an MCP host launches as a subprocess)."""
from __future__ import annotations

from config import AppConfig
from app import build_server


async def run_stdio(config: AppConfig) -> None:
    server, redis = await build_server(config)
    try:
        await server.run_stdio_async()
    finally:
        await redis.aclose()
