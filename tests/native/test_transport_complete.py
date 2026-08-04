from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client


def child_environment(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["ANOTHER_BRAIN_DATA_DIR"] = str(tmp_path / "data")
    env["ANOTHER_BRAIN_MODEL_DIR"] = str(tmp_path / "model")
    env["BRAIN_ID"] = "transport-test"
    return env


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_stdio_process_lists_tools_health_and_missing_model_error(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "another_brain.cli"],
        env=child_environment(tmp_path),
    )
    async with Client(stdio_client(params), read_timeout_seconds=20) as client:
        tools = await client.list_tools()
        health = await client.call_tool("brain_health", {})
        failed = await client.call_tool(
            "brain_remember",
            {
                "topic": "missing-model",
                "summary": "Missing model is actionable.",
                "scope": "global",
            },
        )
        survived = await client.call_tool("brain_health", {})
    assert len(tools.tools) == 8
    assert health.structured_content["status"] == "ok"
    assert failed.is_error
    assert "another-brain model pull" in failed.content[0].text
    assert survived.structured_content["embedding_error"]


def free_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def wait_for_port(port: int, process: subprocess.Popen) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise AssertionError(f"HTTP server exited early with {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            await asyncio.sleep(0.05)
    raise AssertionError("HTTP server did not bind within five seconds")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_loopback_http_process_serves_mcp(tmp_path):
    port = free_loopback_port()
    env = child_environment(tmp_path)
    env["MCP_HTTP_PORT"] = str(port)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [sys.executable, "-m", "another_brain.cli", "serve", "--http"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        await wait_for_port(port, process)
        async with Client(f"http://127.0.0.1:{port}/mcp", read_timeout_seconds=20) as client:
            tools = await client.list_tools()
            health = await client.call_tool("brain_health", {})
        assert len(tools.tools) == 8
        assert health.structured_content["status"] == "ok"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
