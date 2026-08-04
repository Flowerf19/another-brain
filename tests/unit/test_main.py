"""Unit test for the recent CLI line formatter (hook/context consumption)."""
import pytest

# TODO(TASK-076): legacy FastMCP (pre-2.0 SDK) module — deleted in GOAL-015.
pytest.skip(
    "legacy mcp.server.fastmcp removed in MCP SDK 2.0; module deleted in TASK-076",
    allow_module_level=True,
)

from main import format_recent_lines


class FakeRecord:
    def __init__(self, day, topic, importance, summary):
        self.timeline_day = day
        self.topic = topic
        self.importance = importance
        self.summary = summary


def test_format_recent_lines_one_line_per_memory():
    records = [
        FakeRecord("2026-07-22", "no-auth-layer", 4, "Không có auth layer."),
        FakeRecord("2026-07-21", "redis-upgrade", 3, "Nâng Redis 8.8."),
    ]
    out = format_recent_lines(records)
    assert out.splitlines() == [
        "- [2026-07-22] no-auth-layer (i4): Không có auth layer.",
        "- [2026-07-21] redis-upgrade (i3): Nâng Redis 8.8.",
    ]


def test_format_recent_lines_empty():
    assert format_recent_lines([]) == ""
