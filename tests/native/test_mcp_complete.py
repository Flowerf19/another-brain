from __future__ import annotations

import pytest
from mcp import Client
from mcp.types import Implementation

from another_brain.mcp.server import build_mcp_server


@pytest.mark.asyncio
async def test_tool_schemas_are_self_contained_and_do_not_expose_bound_identity(memory_service):
    async with Client(build_mcp_server(memory_service)) as client:
        result = await client.list_tools()
    tools = {tool.name: tool.model_dump(by_alias=True) for tool in result.tools}
    remember = tools["brain_remember"]["inputSchema"]
    assert set(remember["required"]) >= {"topic", "summary", "scope"}
    assert "brain_id" not in remember["properties"]
    assert "agent_id" not in remember["properties"]
    assert all(tool.get("description") for tool in tools.values())


@pytest.mark.asyncio
async def test_all_eight_mcp_tools_execute_complete_lifecycle(memory_service):
    server = build_mcp_server(memory_service)
    client_info = Implementation(name="pytest-mcp-client", version="1.0")
    async with Client(server, client_info=client_info) as client:
        remembered = await client.call_tool(
            "brain_remember",
            {
                "topic": "mcp-complete",
                "summary": "All MCP tools execute through the SDK client.",
                "scope": "project",
                "scope_id": "another-brain",
                "catalog": "test",
                "content": "MCP-COMPLETE-MARKER",
                "importance": 4,
                "metadata": {"suite": "complete"},
            },
        )
        assert not remembered.is_error
        memory_id = remembered.structured_content["memory_id"]

        searched = await client.call_tool(
            "brain_search",
            {"query": "MCP-COMPLETE-MARKER", "scope": "project", "scope_id": "another-brain"},
        )
        assert searched.structured_content["count"] == 1
        preview = searched.structured_content["results"][0]
        assert preview["memory_id"] == memory_id
        assert "content" not in preview
        assert "embedding" not in preview

        recent = await client.call_tool(
            "brain_recent", {"scope": "project", "scope_id": "another-brain", "limit": 10}
        )
        assert recent.structured_content["count"] == 1

        detail = await client.call_tool("brain_get", {"memory_id": memory_id})
        assert detail.structured_content["content"] == "MCP-COMPLETE-MARKER"
        assert detail.structured_content["metadata"] == {"suite": "complete"}

        reinforced = await client.call_tool("brain_reinforce", {"memory_id": memory_id})
        assert reinforced.structured_content["ok"] is True

        health = await client.call_tool("brain_health", {})
        assert health.structured_content["status"] == "ok"
        assert health.structured_content["agent_id"]

        audit = await client.call_tool("brain_audit", {"limit": 100})
        assert {event["action"] for event in audit.structured_content["events"]} >= {
            "remember", "reinforce"
        }

        forgotten = await client.call_tool("brain_forget", {"memory_id": memory_id})
        assert forgotten.structured_content["ok"] is True
        hidden = await client.call_tool("brain_get", {"memory_id": memory_id})
        assert hidden.structured_content == {"found": False, "memory_id": memory_id}


@pytest.mark.xfail(strict=True, reason="MCP v2 clientInfo is not currently extracted by _agent_id")
@pytest.mark.asyncio
async def test_mcp_client_info_is_bound_as_agent_provenance(memory_service):
    client_info = Implementation(name="pytest-mcp-client", version="1.0")
    async with Client(build_mcp_server(memory_service), client_info=client_info) as client:
        health = await client.call_tool("brain_health", {})
    assert health.structured_content["agent_id"] == "pytest-mcp-client"


@pytest.mark.asyncio
async def test_mcp_validation_errors_are_tool_errors_and_session_survives(memory_service):
    async with Client(build_mcp_server(memory_service)) as client:
        invalid_schema = await client.call_tool("brain_remember", {"scope": "global"})
        assert invalid_schema.is_error
        invalid_value = await client.call_tool(
            "brain_remember",
            {"topic": "Bad Topic", "summary": "Invalid.", "scope": "global"},
        )
        assert invalid_value.is_error
        health = await client.call_tool("brain_health", {})
        assert not health.is_error


@pytest.mark.asyncio
async def test_missing_ids_return_stable_non_error_shapes(memory_service):
    async with Client(build_mcp_server(memory_service)) as client:
        detail = await client.call_tool("brain_get", {"memory_id": "missing"})
        reinforced = await client.call_tool("brain_reinforce", {"memory_id": "missing"})
        forgotten = await client.call_tool("brain_forget", {"memory_id": "missing"})
    assert detail.structured_content == {"found": False, "memory_id": "missing"}
    assert reinforced.structured_content == {"ok": False, "memory_id": "missing"}
    assert forgotten.structured_content == {"ok": False, "memory_id": "missing"}
