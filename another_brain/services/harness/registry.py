"""Harness registry: the data-driven ``harnesses.yaml`` rows, plus the pure
data records (TASK-001, plan 08).

The YAML file is the single editable source — adding a harness means adding a
row there, never touching code. It is bundled in the wheel as package data and
read via :mod:`importlib.resources`, so the installed tool alone can set up
any harness. Loaded once at import; :data:`_HARNESSES` keeps file order.
"""
from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass, field

import yaml

SERVER_NAME = "another-brain"

# A harness's mcpServers entry is always the STDIO form: the bare command is
# the MCP stdio server, so the entry needs no type key and no args. Never the
# old HTTP url form — there is no server to point at.
SERVER_ENTRY: dict[str, object] = {"command": SERVER_NAME}

# What a user must paste when we cannot write their config for them. The
# payload is the same stdio entry in both cases; only the file's language
# differs, so the snippet is per-harness rather than global.
MANUAL_JSON = json.dumps({"mcpServers": {SERVER_NAME: SERVER_ENTRY}})


@dataclass(frozen=True)
class Harness:
    """Registry row for one harness — pure data, no behavior.

    ``detect_dir`` (the ``~/.<name>`` dotdir) and ``skill_dir`` are
    home-relative path segments; ``mcp_file`` is the registration target.
    ``cli`` is the optional native CLI that owns the harness's MCP config.
    ``manual_snippet`` must be written in ``mcp_file``'s own language — see
    :data:`MANUAL_TOML`.
    """

    name: str
    detect_dir: str
    skill_dir: str
    mcp_file: str
    cli: str | None = None
    manual_snippet: str = MANUAL_JSON
    server_entry: dict[str, object] = field(
        default_factory=lambda: dict(SERVER_ENTRY)
    )


@dataclass(frozen=True)
class HarnessResult:
    """Outcome of connecting one harness.

    ``registered`` is ``"cli"`` (native CLI wrote the config), ``"json"``
    (JSON upsert), or ``"manual"`` (no CLI found — the caller must register
    by hand using :attr:`snippet`). ``skill_installed`` and ``skill_path``
    describe the skill write; ``messages`` carries the per-step lines the
    CLI prints.
    """

    name: str
    registered: str
    snippet: str = ""
    skill_installed: bool = False
    skill_path: str = ""
    messages: list[str] = field(default_factory=list)


def _registry_yaml_text() -> str:
    """Read harnesses.yaml: wheel resource first, source tree fallback."""
    try:
        return (
            importlib.resources.files("another_brain")
            .joinpath("services/harness/harnesses.yaml")
            .read_text(encoding="utf-8")
        )
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        return (
            __file__.resolve().parent / "harnesses.yaml"
        ).read_text(encoding="utf-8")


def _harness_from_row(row: dict) -> Harness:
    """Build one Harness from a YAML row, applying shared defaults."""
    kwargs: dict[str, object] = {
        "name": row["name"],
        "detect_dir": row["detect_dir"],
        "skill_dir": row["skill_dir"],
        "mcp_file": row["mcp_file"],
        "cli": row.get("cli"),
        "manual_snippet": (row.get("manual") or MANUAL_JSON).strip(),
    }
    if row.get("server_entry") is not None:
        kwargs["server_entry"] = dict(row["server_entry"])
    return Harness(**kwargs)


_HARNESSES: tuple[Harness, ...] = tuple(
    _harness_from_row(row)
    for row in yaml.safe_load(_registry_yaml_text())["harnesses"]
)
_BY_NAME: dict[str, Harness] = {h.name: h for h in _HARNESSES}

# codex's config.toml is TOML: an mcpServers JSON object pasted there is a
# syntax error, not a config. The table key is ``mcp_servers`` (snake_case)
# and a hyphenated server name is a legal TOML bare key. Single source of
# truth is the codex row above; this constant exists for callers and tests.
MANUAL_TOML = _BY_NAME["codex"].manual_snippet
