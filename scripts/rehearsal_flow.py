"""TASK-090 release rehearsal: the MCP half, driven as a real client would.

Spawns the INSTALLED ``another-brain`` console script over stdio and walks
the operator-visible lifecycle once: remember -> search -> get -> reinforce
-> forget, then re-spawns a second process over the same data directory to
prove the store survives a restart and the forgotten entry stays gone.

Deliberately imports nothing from ``another_brain``: it runs under the
installed tool's own venv interpreter, so the only libraries in play are the
ones the wheel actually declares (``mcp``). That is the point — the
rehearsal must prove the shipped artifact works, not that the checkout does.

    <tool-venv>/bin/python scripts/rehearsal_flow.py <console-script>

Environment: ``BRAIN_DATA_DIR``, ``BRAIN_MODEL_CACHE_DIR``, ``BRAIN_ID``
are passed through to the spawned server. Exits nonzero on the first
failed step, with the step name.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp_types import Implementation

CLIENT_NAME = "release-rehearsal"
TOPIC = "release-rehearsal-checkpoint"
PHRASE = "rehearsal-marker-0p11p0"
SUMMARY = (
    f"TASK-090 rehearsal: the {PHRASE} was written from an empty profile "
    "with only uv installed."
)
IMPORTANCE = 1
READ_TIMEOUT = 300.0  # the first remember pays the cold ONNX load

LOCKED_TOOLS = [
    "remember", "search", "recent", "get",
    "reinforce", "forget", "health", "audit",
]


def step(message: str) -> None:
    print(f"  . {message}", flush=True)


def fail(message: str) -> None:
    print(f"REHEARSAL FAILED: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def params(script: str) -> StdioServerParameters:
    return StdioServerParameters(command=script, args=[], env=dict(os.environ))


async def result(raw: Any, name: str) -> dict[str, Any]:
    if getattr(raw, "is_error", False) or getattr(raw, "structured_content", None) is None:
        text = "".join(
            c.text for c in raw.content if getattr(c, "type", None) == "text"
        )
        fail(f"tool {name} returned an error: {text or raw}")
    return raw.structured_content


def epoch_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


async def session_for(script: str):
    return stdio_client(params(script))


async def main() -> None:
    script = sys.argv[1]
    client_info = Implementation(name=CLIENT_NAME, version="0.11.0")

    # ---- session 1: the full lifecycle on a fresh store -------------------
    async with stdio_client(params(script)) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=READ_TIMEOUT, client_info=client_info
        ) as session:
            init = await session.initialize()
            if not (init.instructions and init.instructions.strip()):
                fail("initialize carried no server instructions")
            step("initialize: server instructions present")

            tools = await session.list_tools()
            got = sorted(t.name for t in tools.tools)
            if got != sorted(LOCKED_TOOLS):
                fail(f"expected the 8 locked tools, got {got}")
            step(f"list_tools: {len(got)} locked tools")

            health = await result(
                await session.call_tool("health"), "health"
            )
            if health["status"] != "ok":
                fail(f"health status {health['status']!r}")
            if health["embedding_state"] != "not_loaded":
                fail("health forced an embedding load")
            step(f"health: ok, agent_id={health['agent_id']}")

            remembered = await result(
                await session.call_tool(
                    "remember",
                    {"topic": TOPIC, "summary": SUMMARY, "catalog": "note",
                     "importance": IMPORTANCE},
                ),
                "remember",
            )
            memory_id = remembered["memory_id"]
            expires_first = epoch_ms(remembered["expires_at"])
            step(f"remember: {memory_id} (day {remembered['timeline_day']})")

            found = await result(
                await session.call_tool("search", {"query": PHRASE}),
                "search",
            )
            hits = [r["memory_id"] for r in found["results"]]
            if memory_id not in hits:
                fail(f"search did not return {memory_id}; got {hits}")
            step(f"search: found it among {found['count']} result(s)")

            record = await result(
                await session.call_tool("get", {"memory_id": memory_id}),
                "get",
            )
            if not record["found"] or record["summary"] != SUMMARY:
                fail("get did not return the stored summary verbatim")
            step("get: summary returned verbatim")

            reinforced = await result(
                await session.call_tool("reinforce", {"memory_id": memory_id}),
                "reinforce",
            )
            if not reinforced["ok"]:
                fail("reinforce reported not ok")
            if epoch_ms(reinforced["expires_at"]) < expires_first:
                fail("reinforce did not extend the expiry")
            step("reinforce: expiry extended")

            forgotten = await result(
                await session.call_tool("forget", {"memory_id": memory_id}),
                "forget",
            )
            if not forgotten["ok"]:
                fail("forget reported not ok")
            step("forget: ok")

    # ---- session 2: a brand-new process over the same data dir -----------
    async with stdio_client(params(script)) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=READ_TIMEOUT, client_info=client_info
        ) as session:
            await session.initialize()
            step("restart: second process opened the same store")

            gone = await result(
                await session.call_tool("get", {"memory_id": memory_id}),
                "get",
            )
            if gone["found"]:
                fail("the forgotten memory came back after restart")
            step("restart: the forgotten memory is still gone")

            audit = await result(
                await session.call_tool("audit", {"limit": 20}), "audit"
            )
            actions = [e["action"] for e in audit["events"]]
            for expected in ("remember", "reinforce", "forget"):
                if expected not in actions:
                    fail(f"audit trail is missing {expected!r}: {actions}")
            step(f"restart: audit carries {'/'.join(actions[:6])}")

    print("MCP flow OK", flush=True)


if __name__ == "__main__":
    anyio.run(main)
