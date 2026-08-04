"""Installed native command for Windows and Ubuntu."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .app import build_service
from .config import AppConfig
from .embedding.installer import install_model, model_ready
from .errors import AnotherBrainError


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="another-brain")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="run the MCP server")
    serve.add_argument("--http", action="store_true", help="use loopback HTTP")
    model = commands.add_parser("model", help="manage the pinned ONNX model")
    model.add_argument("action", choices=["pull", "status"])
    commands.add_parser("doctor", help="verify native paths, SQLite and model")
    recent = commands.add_parser("recent", help="print recent memory previews")
    recent.add_argument("--scope", required=True, choices=["user", "project", "global"])
    recent.add_argument("--scope-id", default="")
    recent.add_argument("--days", type=int, default=3)
    recent.add_argument("--limit", type=int, default=10)
    admin = commands.add_parser("admin", help="restore or permanently delete a memory")
    admin.add_argument("action", choices=["restore", "hard-delete"])
    admin.add_argument("memory_id")
    return parser


def _serve(config: AppConfig, *, http: bool) -> int:
    from .mcp.server import build_mcp_server

    server = build_mcp_server(build_service(config))
    if http:
        server.run(
            transport="streamable-http", host=config.http_host, port=config.http_port
        )
    else:
        server.run()
    return 0


async def _recent(service, args) -> int:
    records = await service.recent(
        scope=args.scope,
        scope_id=args.scope_id,
        days=args.days,
        limit=args.limit,
    )
    for record in records:
        print(
            f"- [{record.timeline_day}] {record.topic} "
            f"(i{record.importance}): {record.summary}"
        )
    return 0


async def _admin(service, args) -> int:
    if args.action == "restore":
        record = await service.restore(args.memory_id, agent_id="admin-cli")
        _json({"restored": record is not None, "memory_id": args.memory_id})
    else:
        deleted = await service.hard_delete(args.memory_id, agent_id="admin-cli")
        _json({"deleted": deleted, "memory_id": args.memory_id})
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    args = _parser().parse_args(argv)
    try:
        config = AppConfig.from_env()
        if args.command is None or args.command == "serve":
            return _serve(config, http=bool(getattr(args, "http", False)))
        if args.command == "model":
            if args.action == "pull":
                path = install_model(config.model_dir)
                _json({"installed": True, "path": str(path)})
            else:
                _json({"installed": model_ready(config.model_dir), "path": str(config.model_dir)})
            return 0
        service = build_service(config)
        if args.command == "doctor":
            storage = service.repository.health()
            _json(
                {
                    "status": "ok" if storage["integrity"] == "ok" else "degraded",
                    "version": __version__,
                    "database": storage,
                    "model_installed": model_ready(config.model_dir),
                    "model_dir": str(config.model_dir),
                    "platform_native": True,
                }
            )
            return 0
        if args.command == "recent":
            return asyncio.run(_recent(service, args))
        if args.command == "admin":
            return asyncio.run(_admin(service, args))
        return 2
    except AnotherBrainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
