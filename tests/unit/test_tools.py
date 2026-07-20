"""BrainTools registration contract: the 8 brain_* tools exist with the
expected required/optional params — guards the LLM-facing schema against
silent renames or dropped parameters."""
from mcp.server.fastmcp import FastMCP

from server.tools import register_tools


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