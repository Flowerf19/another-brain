"""Entrypoint for the `another-brain-server` command.

    python src/main.py model plan   [--kind embedding|memory]
    python src/main.py model pull   [--kind embedding|memory]
    python src/main.py model status [--kind embedding|memory]
    python src/main.py serve        [--transport stdio|http]

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
from models.registry import KIND_EMBEDDING, KIND_MEMORY


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_model(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    installer = build_installer(config)
    kinds = [args.kind] if args.kind else [KIND_EMBEDDING, KIND_MEMORY]

    for kind in kinds:
        provider = provider_for(config, kind)
        try:
            spec = resolve_spec(config, kind)
        except ConfigError as exc:
            if args.command == "status":
                _emit({"kind": kind, "provider": provider, "error": str(exc)})
                continue
            raise

        if args.command == "plan":
            if provider != "local":
                _emit({"kind": kind, "provider": provider,
                       "note": "external provider — nothing to download"})
                continue
            _emit(installer.plan(spec))
        elif args.command == "pull":
            if provider != "local":
                _emit({"kind": kind, "provider": provider,
                       "note": "external provider — nothing to pull"})
                continue
            path = installer.pull(spec)
            _emit({"kind": kind, "model": spec.name, "installed": str(path)})
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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env_file()
    parser = argparse.ArgumentParser(prog="another-brain-server")
    subparsers = parser.add_subparsers(dest="group", required=True)

    model = subparsers.add_parser("model", help="local model management (Step 03)")
    model.add_argument("command", choices=["plan", "pull", "status"])
    model.add_argument("--kind", choices=[KIND_EMBEDDING, KIND_MEMORY], default=None)

    serve = subparsers.add_parser("serve", help="run the MCP server")
    serve.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="MCP transport (default: stdio)",
    )

    args = parser.parse_args(argv)
    try:
        if args.group == "serve":
            return _cmd_serve(args)
        return _cmd_model(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
