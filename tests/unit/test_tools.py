"""BrainTools registration contract: the 8 brain_* tools exist with the
expected required/optional params — guards the LLM-facing schema against
silent renames or dropped parameters."""
import pytest

# TODO(TASK-076): legacy FastMCP (pre-2.0 SDK) module — deleted in GOAL-015.
pytest.skip(
    "legacy mcp.server.fastmcp removed in MCP SDK 2.0; module deleted in TASK-076",
    allow_module_level=True,
)

from mcp.server.fastmcp import FastMCP

from app import _SERVER_INSTRUCTIONS
from server.tools import DEFAULT_AGENT_ID, _client_agent_id, register_tools


class FakeService:
    timezone = "Asia/Ho_Chi_Minh"


EXPECTED = {
    "brain_remember": {"required": {"topic", "summary", "scope"},
                       "optional": {"scope_id", "catalog", "content",
                                    "importance", "metadata"}},
    "brain_search": {"required": {"query", "scope"},
                     "optional": {"scope_id", "topic", "catalog",
                                  "timeline_day", "min_importance", "days"}},
    "brain_recent": {"required": {"scope"},
                     "optional": {"scope_id", "topic", "catalog",
                                  "timeline_day", "min_importance", "days",
                                  "limit"}},
    "brain_get": {"required": {"memory_id"}, "optional": set()},
    "brain_reinforce": {"required": {"memory_id"}, "optional": set()},
    "brain_forget": {"required": {"memory_id"}, "optional": set()},
    "brain_health": {"required": set(), "optional": set()},
    "brain_audit": {"required": set(), "optional": {"day", "limit"}},
}


async def test_registers_all_brain_tools():
    server = FastMCP("brain-test")
    register_tools(server, FakeService())
    tools = {t.name: t for t in await server.list_tools()}
    assert set(tools) == set(EXPECTED)

    for name, spec in EXPECTED.items():
        schema = tools[name].inputSchema
        required = set(schema.get("required", []))
        props = set(schema.get("properties", {}))
        optional = props - required
        assert required == spec["required"], f"{name}: required {required}"
        assert optional == spec["optional"], f"{name}: optional {optional}"
        # description is the LLM contract — must be non-empty.
        assert tools[name].description and tools[name].description.strip()


def test_server_instructions_carry_the_recall_loop():
    """_SERVER_INSTRUCTIONS is the short contract sent at MCP handshake
    (Step 06, GOAL-002) — hosts that surface it must see the full loop, so
    the verbs cannot silently rot."""
    for verb in ("brain_search", "brain_remember", "brain_reinforce",
                 "brain_forget"):
        assert verb in _SERVER_INSTRUCTIONS


def _fake_ctx(client_name):
    class _Node:
        pass

    ctx = _Node()
    ctx.request_context = _Node()
    ctx.request_context.session = _Node()
    if client_name is None:
        ctx.request_context.session.client_params = None
    else:
        ctx.request_context.session.client_params = _Node()
        ctx.request_context.session.client_params.clientInfo = _Node()
        ctx.request_context.session.client_params.clientInfo.name = client_name
    return ctx


def test_client_agent_id_comes_from_handshake():
    """agent_id provenance is the client's declared clientInfo name
    (spec-required), with a neutral fallback — never config, never tool input."""
    assert _client_agent_id(_fake_ctx("claude-code")) == "claude-code"
    assert _client_agent_id(_fake_ctx("pi-mcp-another-brain")) == "pi-mcp-another-brain"
    assert _client_agent_id(_fake_ctx("  ")) == DEFAULT_AGENT_ID
    assert _client_agent_id(_fake_ctx(None)) == DEFAULT_AGENT_ID