"""TASK-068/TASK-091: initialize + tools/list metadata is sufficient for a
skill-less client.

The MCP server must teach a client with no skill installed everything it needs
to use the memory store correctly: what each of the eight tools is for, what
every argument means (TASK-091's 29/29 bug was bare field types), the numeric
bounds, and the house rules (TTL, append-only, claims-not-truth, no secrets).
The thin ``skills/another-brain/SKILL.md`` (TASK-091) must stay an adapter and
not re-ship those contracts.

These tests are deterministic: the ``mcp_server`` fixture runs the real server
in-process over temp SQLite with fake embeddings — no network, no wall clock,
no ONNX model.
"""
from __future__ import annotations

import re
from pathlib import Path

from another_brain.mcp.server import INSTRUCTIONS
from mcp.types import Tool

LOCKED_TOOL_NAMES = {
    "remember",
    "search",
    "recent",
    "get",
    "reinforce",
    "forget",
    "health",
    "audit",
}

# ---------------------------------------------------------------------------
# TASK-068: initialize + tools/list surface
# ---------------------------------------------------------------------------


def test_tools_list_exposes_exactly_the_eight_locked_names(mcp_server):
    """tools/list is the whole tool surface: exactly the eight locked tools."""
    import asyncio

    from mcp.client import Client

    async def _list() -> list[str]:
        async with Client(mcp_server) as client:
            return [t.name for t in (await client.list_tools()).tools]

    names = asyncio.run(_list())
    assert set(names) == LOCKED_TOOL_NAMES


def test_every_tool_has_a_substantive_description(mcp_server):
    """A skill-less client picks the right tool from the description alone."""
    import asyncio

    from mcp.client import Client

    async def _tools() -> list[Tool]:
        async with Client(mcp_server) as client:
            return (await client.list_tools()).tools

    for tool in asyncio.run(_tools()):
        assert tool.description, f"{tool.name} has an empty description"
        assert len(tool.description) >= 40, (
            f"{tool.name} description is suspiciously short "
            f"({len(tool.description)} chars)"
        )


def test_every_input_schema_field_carries_a_description(mcp_server):
    """TASK-091 bug: 29/29 fields were bare types. None may be bare again.

    The locked count is asserted on the *current* contract (23 fields — the
    plan's 29 predates the scope-partition removal, which dropped six
    ``scope``/``scope_id`` args) so a regression that adds a bare field fails
    both ways: wrong total, or a descriptionless property.
    """
    import asyncio

    from mcp.client import Client

    async def _tools() -> list[Tool]:
        async with Client(mcp_server) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(_tools())
    total_fields = 0
    for tool in tools:
        for name, spec in tool.input_schema.get("properties", {}).items():
            total_fields += 1
            assert spec.get("description"), (
                f"{tool.name}.{name} has no description: {spec!r}"
            )
    assert total_fields == 23, (
        f"expected 23 described input fields across the eight tools, "
        f"got {total_fields}"
    )


def test_no_input_field_is_named_brain_id_or_agent_id(mcp_server):
    """Identity is bound (brain from config, agent from handshake), never an arg."""
    import asyncio

    from mcp.client import Client

    async def _tools() -> list[Tool]:
        async with Client(mcp_server) as client:
            return (await client.list_tools()).tools

    for tool in asyncio.run(_tools()):
        for name in tool.input_schema.get("properties", {}):
            assert name not in {"brain_id", "agent_id"}, (
                f"{tool.name} takes {name!r}; identity is bound, not an argument"
            )


def test_numeric_bounds_are_encoded_in_the_schemas(mcp_server):
    """Clients must see the legal ranges without calling a tool."""
    import asyncio

    from mcp.client import Client

    async def _tools() -> dict[str, Tool]:
        async with Client(mcp_server) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    tools = asyncio.run(_tools())

    def _field(tool_name: str, field: str) -> dict:
        spec = tools[tool_name].input_schema["properties"][field]
        # Optional fields arrive as {"anyOf": [{"type": ...}, {"type": "null"}]};
        # unwrap to the innermost object that carries the bounds.
        for _ in range(4):
            if isinstance(spec, dict) and "anyOf" in spec:
                spec = spec["anyOf"][0]
        return spec

    importance = _field("remember", "importance")
    assert importance.get("minimum") == 1 and importance.get("maximum") == 5

    recent_limit = _field("recent", "limit")
    assert recent_limit.get("minimum") == 1 and recent_limit.get("maximum") == 100

    audit_limit = _field("audit", "limit")
    assert audit_limit.get("minimum") == 1 and audit_limit.get("maximum") == 500

    for tool_name in ("search", "recent"):
        min_importance = _field(tool_name, "min_importance")
        assert min_importance.get("minimum") == 1, tool_name
        assert min_importance.get("maximum") == 5, tool_name
        days = _field(tool_name, "days")
        assert days.get("minimum") == 1, tool_name


def test_server_instructions_ship_on_the_client_and_cover_the_loop(mcp_server):
    """In-process transport surfaces instructions on ``client.instructions``.

    Verified against MCP SDK 2.0.0: ``Client.instructions`` (client.py) reads
    ``session.instructions``, which returns the InitializeResult
    ``instructions`` field on legacy connections — the in-process transport
    carries it. This asserts the surfaced value, not just the constant.
    """
    import asyncio

    from mcp.client import Client

    async def _instructions() -> str | None:
        async with Client(mcp_server) as client:
            return client.instructions

    surfaced = asyncio.run(_instructions())
    assert surfaced is not None and surfaced.strip(), (
        "client.instructions is empty; the initialize handshake must carry "
        "the server instructions"
    )
    # It is the same text the server was built with — no drift between the
    # constant and what a client actually receives.
    assert surfaced == INSTRUCTIONS

    words = surfaced.split()
    assert len(words) > 0
    assert len(words) <= 200, f"instructions too long: {len(words)} words"
    lower = surfaced.lower()
    assert "search it before acting" in lower
    assert "close the loop" in lower
    assert "reinforce" in lower and "forget" in lower


def test_skill_less_client_can_learn_the_house_rules(mcp_server):
    """TASK-091 manual 17-fact audit, encoded as substring assertions.

    A client with no skill must be able to learn these from instructions +
    tool descriptions + field descriptions alone. Short substrings with
    flexible whitespace, case-insensitive.
    """
    import asyncio

    from mcp.client import Client

    async def _corpus() -> str:
        async with Client(mcp_server) as client:
            tools = (await client.list_tools()).tools
            chunks = [client.instructions or ""]
            chunks += [t.description or "" for t in tools]
            chunks += [
                p.get("description", "")
                for t in tools
                for p in t.input_schema.get("properties", {}).values()
            ]
            return " ".join(chunks)

    corpus = re.sub(r"\s+", " ", asyncio.run(_corpus()).lower())

    def _assert(substr: str, fact: str) -> None:
        assert substr in corpus, (
            f"skill-less client cannot learn: {fact}\n"
            f"missing substring {substr!r} in:\n{corpus}"
        )

    # (a) search/recent return previews; get fetches full detail.
    _assert("previews", "search/recent return previews")
    _assert("fetch full detail", "get fetches full detail")
    # (b) reinforce is the only way an entry's life is extended.
    _assert("only way", "reinforce is the only way to extend an entry's life")
    # (c) TTL mapping 365/180/90/30/7 days for importance 5..1.
    for days in ("365", "180", "90", "30", "7d"):
        _assert(days, f"TTL mapping mentions {days}")
    # (d) append-only store, no overwrite.
    _assert("append-only", "store is append-only")
    # (e) memories are claims, not verified truth.
    _assert("claims", "memories are claims, not truth")
    # (f) do not store secrets.
    _assert("secrets", "do not store secrets")
    # (g) topic is lowercase-kebab, 3-8 tokens, max 12.
    _assert("lowercase-kebab", "topic is lowercase-kebab")
    _assert("3-8 tokens", "topic is 3-8 tokens")
    _assert("at most 12", "topic max 12")
    # (h) content is never embedded; full-text searchable; fetched only via get.
    _assert("never embedded", "content is never embedded")
    _assert("full-text", "content is full-text searchable")
    # (i) forget hides immediately; admin can restore during a grace window.
    _assert("immediately", "forget hides immediately")
    _assert("grace window", "admin restore grace window")
    # (j) audit never carries memory text.
    _assert("memory text", "audit never carries memory text")
    # (k) embedding_state not_loaded is healthy.
    _assert("not_loaded", "not_loaded embedding state is healthy")
    # (l) expired entries are excluded.
    _assert("expired", "expired entries are excluded")
    # (m) agent identity comes from the handshake; brain is bound by server.
    _assert("handshake", "agent identity comes from the handshake")


def test_skill_md_stays_a_thin_adapter_without_duplicated_contracts():
    """TASK-091: the skill is an activation/scope/trust adapter (100-200 words).

    It must not re-ship numbers or schema names that live in the server
    surface — those would drift (they did before: 29 fields vs 5 copies).
    Resolve the file from this test file's parents (tests/unit -> repo root).
    """
    repo_root = Path(__file__).resolve().parents[2]
    skill_path = repo_root / "skills" / "another-brain" / "SKILL.md"
    assert skill_path.is_file(), f"missing {skill_path}"

    text = skill_path.read_text(encoding="utf-8")
    parts = text.split("---")
    assert len(parts) >= 3, "SKILL.md must open with YAML frontmatter"
    body = parts[2].strip()

    word_count = len(body.split())
    assert 100 <= word_count <= 200, (
        f"SKILL.md body is {word_count} words; TASK-091 locks it to 100-200"
    )

    # No locked contract numbers, and no schema column names.
    for forbidden in (
        "365",
        "1,024",
        "1024",
        "256",
        "300000",
        "expires_at_ms",
        "created_at_ms",
        "updated_at_ms",
        "memory_id",
        "timeline_day",
        "embedding_state",
    ):
        assert forbidden not in body, (
            f"SKILL.md body duplicates locked contract {forbidden!r}; "
            f"the server surface owns it (TASK-091)"
        )
