"""Entrypoint for the `another-brain-server` command.

    python src/main.py model plan
    python src/main.py model pull
    python src/main.py model status
    python src/main.py serve        [--transport stdio|http]
    python src/main.py recent       --scope ... [--days N] [--limit N]
    python src/main.py admin        restore|hard-delete <memory_id>

`serve` runs the MCP server; the model subcommands manage the local model cache
(Step 03 "Proposed Commands"). Both honor `.env` (loaded in main()).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from app import (
    build_installer,
    load_env_file,
    profile_for,
    provider_for,
    resolve_spec,
)
from config import AppConfig
from errors import ConfigError
from models.registry import KIND_EMBEDDING


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_model(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    installer = build_installer(config)

    provider = provider_for(config)
    try:
        spec = resolve_spec(config)
    except ConfigError as exc:
        if args.command == "status":
            _emit({"kind": KIND_EMBEDDING, "provider": provider, "error": str(exc)})
            return 0
        raise

    if args.command == "plan":
        if provider != "local":
            _emit({"kind": KIND_EMBEDDING, "provider": provider,
                   "note": "external provider — nothing to download"})
        else:
            _emit(installer.plan(spec))
    elif args.command == "pull":
        if provider != "local":
            _emit({"kind": KIND_EMBEDDING, "provider": provider,
                   "note": "external provider — nothing to pull"})
        else:
            path = installer.pull(spec)
            _emit({"kind": KIND_EMBEDDING, "model": spec.name, "installed": str(path)})
    else:  # status
        profile = profile_for(config, spec)
        _emit(installer.status(spec, provider=provider, profile=profile).to_dict())
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from server.http import run_http
    from server.stdio import run_stdio

    config = AppConfig.from_env()
    runner = run_http if args.transport == "http" else run_stdio
    asyncio.run(runner(config))
    return 0


def format_recent_lines(records: list) -> str:
    """One preview line per memory, for shell/hook consumption (context
    injection) — compact text, not JSON."""
    return "\n".join(
        f"- [{r.timeline_day}] {r.topic} (i{r.importance}): {r.summary}"
        for r in records
    )


async def _run_recent(args: argparse.Namespace) -> str:
    from app import build_service

    config = AppConfig.from_env()
    service, redis = await build_service(config)
    try:
        records = await service.recent(
            scope=args.scope, scope_id=args.scope_id,
            days=args.days, limit=args.limit,
        )
        if not records:
            return ""
        header = (
            f"Recent another-brain memories "
            f"(scope={args.scope}:{args.scope_id or 'global'}, last {args.days}d):"
        )
        return header + "\n" + format_recent_lines(records)
    finally:
        await redis.aclose()


def _cmd_recent(args: argparse.Namespace) -> int:
    out = asyncio.run(_run_recent(args))
    if out:
        print(out)
    return 0


async def _run_admin(args: argparse.Namespace) -> dict[str, object]:
    from app import build_service

    config = AppConfig.from_env()
    service, redis = await build_service(config)
    try:
        if args.command == "restore":
            detail = await service.restore(args.memory_id, agent_id="admin-cli")
            return {"command": "restore", "memory_id": args.memory_id,
                    "restored": detail is not None}
        # hard-delete
        deleted = await service.hard_delete(args.memory_id, agent_id="admin-cli")
        return {"command": "hard-delete", "memory_id": args.memory_id,
                "deleted": deleted}
    finally:
        await redis.aclose()


def _cmd_admin(args: argparse.Namespace) -> int:
    _emit(asyncio.run(_run_admin(args)))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env_file()
    parser = argparse.ArgumentParser(prog="another-brain-server")
    subparsers = parser.add_subparsers(dest="group", required=True)

    model = subparsers.add_parser("model", help="local model management (Step 03)")
    model.add_argument("command", choices=["plan", "pull", "status"])

    serve = subparsers.add_parser("serve", help="run the MCP server")
    serve.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="MCP transport (default: stdio)",
    )

    admin = subparsers.add_parser(
        "admin", help="admin memory operations (restore, hard-delete)"
    )
    admin.add_argument("command", choices=["restore", "hard-delete"])
    admin.add_argument("memory_id")

    recent = subparsers.add_parser(
        "recent", help="print recent memories as text lines (for hooks/scripts)"
    )
    recent.add_argument("--scope", required=True, choices=["user", "project", "global"])
    recent.add_argument("--scope-id", default="")
    recent.add_argument("--days", type=int, default=3)
    recent.add_argument("--limit", type=int, default=10)

    args = parser.parse_args(argv)
    try:
        if args.group == "serve":
            return _cmd_serve(args)
        if args.group == "admin":
            return _cmd_admin(args)
        if args.group == "recent":
            return _cmd_recent(args)
        return _cmd_model(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
