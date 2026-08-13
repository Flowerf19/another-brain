"""Runtime assembly and transports (TASK-067).

Builds the one object graph the server runs on — connection factory, migrated
schema, registered profile, repository, retriever, audit, health probe,
embedding provider, budgets, service, tools — and runs it over stdio or
loopback HTTP.

Two rules shape startup. Storage opens eagerly, because a broken database
should fail at launch rather than on an agent's first tool call. The embedding
model stays lazy: loading it costs seconds and hundreds of MiB, `health`
must answer without it, and a host that spawns a stdio server per session would
otherwise pay that cost for sessions that never search.

In stdio mode stdout carries MCP frames only; logging is pinned to stderr here
rather than left to whatever a library configures at import.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from another_brain.config import AppConfig, parse_loopback_host
from another_brain.errors import ModelNotInstalledError
from another_brain.mcp.tools import register_tools
from another_brain.retrieval.service import HybridMemoryRetriever
from another_brain.services.embedding.budgets import TokenBudgetValidator
from another_brain.services.embedding.model_installer import profile_dir
from another_brain.services.embedding.provider import (
    NOT_INSTALLED_MESSAGE,
    ONNXEmbeddingProvider,
)
from another_brain.services.memory_service import MemoryService
from another_brain.services.sql.audit import SQLiteAuditRepository
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.health import SQLiteHealthProbe
from another_brain.services.sql.migrations import migrate
from another_brain.services.sql.profile import register_profile
from another_brain.services.sql.repository import SQLiteMemoryRepository

SERVER_NAME = "another-brain"
HTTP_PATH = "/mcp"

INSTRUCTIONS = """\
Another Brain is your long-term memory across sessions: a timeline (diary) of \
what was learned, decided, and preferred, stored locally and shared by the \
agents that connect to this server.

Search it before acting when past context could matter — a prior decision, a \
known bug, a user preference. Store a memory when you learn something worth \
recalling later, using a stable reusable topic so the same subject accumulates \
over time.

Memories are claims, not facts: current code and live state win over anything \
recorded here. Entries expire on a schedule set by importance, so after you \
actually use one, close the loop — reinforce it if it proved right (the only \
way an entry's life is extended), forget it if it proved wrong.

Search and recent return previews; fetch full detail by id with get.\
"""


@dataclass
class Runtime:
    """The assembled object graph plus the resources that must be released."""

    server: MCPServer
    service: MemoryService
    embedder: ONNXEmbeddingProvider

    def close(self) -> None:
        """Release the embedding session. SQLite holds no long-lived handle."""
        self.embedder.close()


def build_runtime(config: AppConfig) -> Runtime:
    """Open storage, wire the graph, register the tools. Never loads the model."""
    config.ensure_directories()
    factory = SQLiteConnectionFactory(
        config.database_path, disable_vec=config.disable_sqlite_vec
    )
    factory.bootstrap()
    migrate(config.database_path)
    register_profile(factory)

    model_dir = profile_dir(config.model_cache_dir)
    embedder = ONNXEmbeddingProvider(model_dir)
    service = MemoryService(
        repository=SQLiteMemoryRepository(factory, brain_id=config.brain_id),
        retriever=HybridMemoryRetriever(factory, brain_id=config.brain_id),
        audit=SQLiteAuditRepository(factory, brain_id=config.brain_id),
        embedder=embedder,
        budgets=_budgets(model_dir),
        storage=SQLiteHealthProbe(factory),
        config=config,
    )
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    register_tools(server, service)
    return Runtime(server=server, service=service, embedder=embedder)


def _budgets(model_dir: Path) -> TokenBudgetValidator:
    """Load the tokenizer alone — a few MB of vocabulary, not the ONNX graph.

    Budgets are checked before every embed, including the first, so this is the
    one piece of the model that cannot be deferred. It is also why an
    uninstalled profile is caught at startup with the same actionable message
    the provider would raise later.
    """
    from tokenizers import Tokenizer

    path = model_dir / "tokenizer.json"
    if not path.exists():
        raise ModelNotInstalledError(NOT_INSTALLED_MESSAGE)
    return TokenBudgetValidator(Tokenizer.from_file(str(path)))


def _configure_logging() -> None:
    """Pin logs to stderr: stdout belongs to the MCP framing in stdio mode."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)


def serve_stdio(config: AppConfig) -> None:
    """The default transport. Runs until the host closes stdin."""
    _configure_logging()
    runtime = build_runtime(config)
    try:
        runtime.server.run("stdio")
    except KeyboardInterrupt:
        pass
    finally:
        runtime.close()


def transport_security(host: str, port: int) -> TransportSecuritySettings:
    """Allowlists pinned to the exact bound address — no wildcard, no port glob.

    The SDK auto-enables a loopback default, but it accepts ``localhost`` and
    any port (``127.0.0.1:*``). ``localhost`` is a name, and a name is what a
    DNS rebinding attack controls, so the locked policy is narrower: the exact
    numeric host and the exact port this process bound, with the bracketed form
    for IPv6 as it appears in a Host header.
    """
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[authority],
        allowed_origins=[f"http://{authority}"],
    )


def serve_http(config: AppConfig, *, host: str, port: int) -> None:
    """Opt-in loopback HTTP. Re-validates the bind before it is used."""
    _configure_logging()
    # The caller already parsed this, but the value reaches a socket here:
    # re-checking costs nothing and keeps the guarantee at the bind site.
    host = parse_loopback_host(host)
    runtime = build_runtime(config)
    try:
        runtime.server.run(
            "streamable-http",
            host=host,
            port=port,
            streamable_http_path=HTTP_PATH,
            transport_security=transport_security(host, port),
        )
    except KeyboardInterrupt:
        pass
    finally:
        runtime.close()
