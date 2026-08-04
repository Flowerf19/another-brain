"""Console entry point and command parser (TASK-040).

Transport contract: bare ``another-brain`` is always the MCP stdio server —
stdout is reserved exclusively for MCP frames; all logs, progress, and
diagnostics go to stderr. HTTP is opt-in via ``serve --http`` with bind
precedence CLI ``--host/--port`` > ``MCP_HTTP_HOST``/``MCP_HTTP_PORT`` >
``127.0.0.1:1905``, numeric loopback only (validated by
:mod:`another_brain.config`).

Subsystems land in later phases (embedding GOAL-010, storage GOAL-011,
service/MCP GOAL-013, import GOAL-014). Until then the corresponding commands
validate their arguments, then exit ``EXIT_UNAVAILABLE`` with a typed
not-yet-available message on stderr.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from another_brain.config import AppConfig, parse_loopback_host, parse_port
from another_brain.errors import ConfigError

PROG = "another-brain"
VERSION = "0.11.0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_CONFIG = 4


class _NotAvailable(Exception):
    """The command is valid but its subsystem has not landed yet."""

    def __init__(self, command: str, phase: str) -> None:
        super().__init__(command)
        self.command = command
        self.phase = phase


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Shared long-term memory for MCP agents. Bare invocation starts the "
            "MCP stdio server (stdout carries MCP frames only)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {VERSION}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser(
        "serve", help="start the MCP server (stdio default, --http opt-in)"
    )
    serve.add_argument(
        "--http",
        action="store_true",
        help="serve Streamable HTTP on a numeric loopback address instead of stdio",
    )
    serve.add_argument("--host", default=None, help="numeric loopback IP (127.0.0.0/8 or ::1)")
    serve.add_argument("--port", default=None, type=str, help="TCP port 1..65535")

    model = sub.add_parser("model", help="manage the pinned embedding model")
    model_sub = model.add_subparsers(dest="model_command")
    model_sub.add_parser("pull", help="download and verify the pinned q4 artifacts")
    model_sub.add_parser("status", help="show install/load state without loading the model")

    sub.add_parser("doctor", help="verify install, model, and database health")
    sub.add_parser("recent", help="print recent memories (uses the configured data dir)")

    admin = sub.add_parser("admin", help="administrative lifecycle operations")
    admin_sub = admin.add_subparsers(dest="admin_command")
    restore = admin_sub.add_parser("restore", help="undo a soft delete inside its grace window")
    restore.add_argument("memory_id")
    hard = admin_sub.add_parser("hard-delete", help="permanently remove a memory")
    hard.add_argument("memory_id")

    imp = sub.add_parser("import-jsonl", help="import a another-brain-jsonl v1 artifact")
    imp.add_argument("path", help="path to the JSONL export artifact")
    return parser


def _err(message: str) -> None:
    print(f"{PROG}: {message}", file=sys.stderr)


def _resolve_http_bind(args: argparse.Namespace, config: AppConfig) -> tuple[str, int]:
    """CLI flag > environment > default, all numeric-loopback validated."""
    host = parse_loopback_host(args.host) if args.host is not None else config.http.host
    port = parse_port(args.port, source="--port") if args.port is not None else config.http.port
    return host, port


def _dispatch(args: argparse.Namespace, config: AppConfig) -> int:
    if args.command is None:
        raise _NotAvailable("stdio server", "GOAL-013")
    if args.command == "serve":
        if args.http:
            host, port = _resolve_http_bind(args, config)
            _err(f"loopback HTTP bind validated: {host}:{port}")
        raise _NotAvailable("serve", "GOAL-013")
    if args.command == "model":
        if args.model_command == "pull":
            raise _NotAvailable("model pull", "GOAL-010")
        if args.model_command == "status":
            raise _NotAvailable("model status", "GOAL-010")
    if args.command == "doctor":
        raise _NotAvailable("doctor", "GOAL-016")
    if args.command == "recent":
        raise _NotAvailable("recent", "GOAL-013")
    if args.command == "admin":
        if args.admin_command == "restore":
            raise _NotAvailable("admin restore", "GOAL-011/013")
        if args.admin_command == "hard-delete":
            raise _NotAvailable("admin hard-delete", "GOAL-011/013")
    if args.command == "import-jsonl":
        raise _NotAvailable("import-jsonl", "GOAL-014")
    return EXIT_USAGE


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = AppConfig.from_env()
        return _dispatch(args, config)
    except _NotAvailable as exc:
        _err(f"{exc.command!r} is not yet available in this build (lands in {exc.phase})")
        return EXIT_UNAVAILABLE
    except ConfigError as exc:
        _err(f"configuration error: {exc}")
        return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
