"""TASK-069: end-to-end stdio round trip against the installed console script.

Drives the installed ``another-brain`` console script over the MCP stdio
transport with an isolated data home (``tmp_path``), using the pinned q4
model from the shared default cache. Proves the real product path end to end:

1. fresh data home: initialize carries server instructions; the eight locked
   tool names are listed; ``brain_health`` answers without loading the
   embedding model; remember → expires_at = created_at + 7d (importance 1);
   search finds the phrase; get returns the full record; reinforce pushes
   expires_at later; forget returns ok.
2. a second remember of identical input yields a *different* id (append-only).
3. restart in a brand-new subprocess over the same data dir: the forgotten id
   is gone from get/search/recent, and the audit trail for the remember day
   carries remember/reinforce/forget attributed to the declared client name.

Skips when the console script is not on PATH (or beside ``sys.executable``)
or the pinned q4 profile is missing from the default model cache — run
``another-brain model pull`` first. Marked ``slow``; CI runs the fast suite.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp_types import Implementation

from another_brain.config import AppConfig
from another_brain.services.embedding.model_installer import is_installed

pytestmark = pytest.mark.slow

CLIENT_NAME = "e2e-agent"
TOPIC = "e2e-wal-checkpoint"
DISTINCTIVE_PHRASE = "distinctive-e2e-marker"
SUMMARY = (
    f"TASK-069 checkpoint: the {DISTINCTIVE_PHRASE} was captured by the "
    "e2e stdio round trip."
)
IMPORTANCE = 1
TTL_DAYS = 7

READ_TIMEOUT_SECONDS = 180.0  # the first remember pays the cold ONNX load


def _skip_if_unavailable() -> tuple[Path, AppConfig]:
    """Console script + pinned q4 profile, or skip with the repo's message."""
    config = AppConfig.from_env()
    if not is_installed(config.model_cache_dir, verify_files=False):
        pytest.skip("the pinned q4 profile is not installed; run `another-brain model pull`")
    script = shutil.which("another-brain")
    if script is None:
        script = str(Path(sys.executable).parent / "another-brain")
    if not os.path.exists(script):
        pytest.skip("the `another-brain` console script was not found on PATH")
    return Path(script), config


def _iso_to_epoch_ms(value: str) -> int:
    """ISO 8601 (offset-aware) → epoch milliseconds."""
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _spawn(
    script: Path,
    config: AppConfig,
    data_dir: Path,
) -> tuple[StdioServerParameters, dict[str, str]]:
    """Stdio parameters for an isolated data home + shared read-only model cache."""
    env = dict(os.environ)
    env["BRAIN_DATA_DIR"] = str(data_dir)
    env["BRAIN_MODEL_CACHE_DIR"] = str(config.model_cache_dir)
    env["BRAIN_ID"] = "e2e-brain"
    env["TIMELINE_TIMEZONE"] = "UTC"
    return (
        StdioServerParameters(command=str(script), args=[], env=env),
        env,
    )


async def _result(r: Any, name: str) -> dict[str, Any]:
    """Structured tool result (snake_case in v2), else the text fallback."""
    if getattr(r, "is_error", False) or getattr(r, "structured_content", None) is None:
        text = "".join(c.text for c in r.content if getattr(c, "type", None) == "text")
        raise AssertionError(f"tool {name} failed: {text or r}")
    return r.structured_content


async def test_e2e_stdio_round_trip(tmp_path):
    script, config = _skip_if_unavailable()
    data_dir = tmp_path / "data"
    params, _env = _spawn(script, config, data_dir)
    client_info = Implementation(name=CLIENT_NAME, version="1.0.0")

    # -- session 1: fresh data home -----------------------------------------
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=READ_TIMEOUT_SECONDS, client_info=client_info
        ) as session:
            init = await session.initialize()
            assert init.instructions and init.instructions.strip(), (
                "initialize must carry non-empty server instructions"
            )

            tools = await session.list_tools()
            locked = [
                "brain_remember", "brain_search", "brain_recent", "brain_get",
                "brain_reinforce", "brain_forget", "brain_health", "brain_audit",
            ]
            assert sorted(t.name for t in tools.tools) == sorted(locked), (
                f"expected exactly the {len(locked)} locked tools, got "
                f"{sorted(t.name for t in tools.tools)}"
            )

            health = await _result(await session.call_tool("brain_health"), "brain_health")
            assert health["status"] == "ok"
            assert health["brain_id"] == "e2e-brain"
            assert health["agent_id"] == CLIENT_NAME  # client_info from the handshake
            assert health["embedding_state"] == "not_loaded"  # health never forces a load

            remember = await _result(
                await session.call_tool(
                    "brain_remember",
                    {
                        "topic": TOPIC,
                        "summary": SUMMARY,
                        "catalog": "note",
                        "importance": IMPORTANCE,
                    },
                ),
                "brain_remember",
            )
            memory_id = remember["memory_id"]
            assert remember["timeline_day"]

            # Second remember of identical input is append-only: a different id.
            second = await _result(
                await session.call_tool(
                    "brain_remember",
                    {
                        "topic": TOPIC,
                        "summary": SUMMARY,
                        "catalog": "note",
                        "importance": IMPORTANCE,
                    },
                ),
                "brain_remember",
            )
            assert second["memory_id"] != memory_id

            found = await _result(
                await session.call_tool("brain_search", {"query": DISTINCTIVE_PHRASE}),
                "brain_search",
            )
            assert found["count"] >= 1
            assert any(r["memory_id"] == memory_id for r in found["results"]), (
                f"search must return {memory_id}, got {found['results']}"
            )

            record = await _result(
                await session.call_tool("brain_get", {"memory_id": memory_id}),
                "brain_get",
            )
            assert record["found"] is True
            assert record["topic"] == TOPIC
            assert record["summary"] == SUMMARY
            assert record["importance"] == IMPORTANCE
            assert record["agent_id"] == CLIENT_NAME

            created_ms = _iso_to_epoch_ms(record["created_at"])
            expires_ms = _iso_to_epoch_ms(record["expires_at"])
            expected_ms = created_ms + TTL_DAYS * 86_400_000
            assert abs(expires_ms - expected_ms) < 5_000, (
                f"importance {IMPORTANCE} must expire exactly "
                f"{TTL_DAYS} days after creation: created={record['created_at']} "
                f"expires={record['expires_at']}"
            )

            reinforced = await _result(
                await session.call_tool(
                    "brain_reinforce", {"memory_id": memory_id}
                ),
                "brain_reinforce",
            )
            assert reinforced["ok"] is True
            assert reinforced["expires_at"] > record["expires_at"], (
                "reinforce must move expires_at later"
            )

            forgotten = await _result(
                await session.call_tool("brain_forget", {"memory_id": memory_id}),
                "brain_forget",
            )
            assert forgotten["ok"] is True

            remember_day = remember["timeline_day"]
    # session closed → server process exits

    # -- session 2: restart over the same data dir ---------------------------
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=READ_TIMEOUT_SECONDS, client_info=client_info
        ) as session:
            await session.initialize()

            gone = await _result(
                await session.call_tool("brain_get", {"memory_id": memory_id}),
                "brain_get",
            )
            assert gone["found"] is False  # forget persisted across restart

            no_hits = await _result(
                await session.call_tool("brain_search", {"query": DISTINCTIVE_PHRASE}),
                "brain_search",
            )
            # The append-only twin shares the phrase, so count > 0 is expected;
            # the contract is that the *forgotten* id is gone from search.
            assert all(r["memory_id"] != memory_id for r in no_hits["results"]), (
                f"forgotten memory must not be searchable: {no_hits}"
            )

            recent = await _result(
                await session.call_tool("brain_recent", {"limit": 100}),
                "brain_recent",
            )
            assert all(r["memory_id"] != memory_id for r in recent["results"]), (
                "forgotten memory must not appear in recent"
            )
            # The append-only twin from session 1 is still live.
            assert any(r["memory_id"] == second["memory_id"] for r in recent["results"])

            audit = await _result(
                await session.call_tool("brain_audit", {"day": remember_day}),
                "brain_audit",
            )
            by_action = {e["action"] for e in audit["events"] if e["memory_id"] == memory_id}
            assert {"remember", "reinforce", "forget"} <= by_action, (
                f"audit must carry remember/reinforce/forget for {memory_id}, got "
                f"{[e for e in audit['events'] if e['memory_id'] == memory_id]}"
            )
            assert all(
                e["agent_id"] == CLIENT_NAME
                for e in audit["events"]
                if e["memory_id"] == memory_id
            ), "audit events must be attributed to the declared client name"

            health2 = await _result(await session.call_tool("brain_health"), "brain_health")
            assert health2["status"] == "ok"
