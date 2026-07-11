"""Entrypoint for the `another-brain-server` command.

Currently implements the model management CLI (Step 03 "Proposed Commands"):

    python src/main.py model plan   [--kind embedding|memory]
    python src/main.py model pull   [--kind embedding|memory]
    python src/main.py model status [--kind embedding|memory]

The MCP server entrypoint lands with the service/tools slice.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from config import AppConfig
from errors import ConfigError
from models.cache import ModelCache
from models.installer import ModelInstaller
from models.registry import KIND_EMBEDDING, KIND_MEMORY, ModelRegistry, ModelSpec
from models.runtime import ModelRuntimeProfile


def _resolve_spec(config: AppConfig, kind: str) -> ModelSpec:
    registry = ModelRegistry()
    if kind == KIND_EMBEDDING:
        return registry.resolve(
            config.embedding.model_name, kind,
            configured_dim=config.embedding.dim,
        )
    return registry.resolve(config.memory_model.model_name, kind)


def _provider_for(config: AppConfig, kind: str) -> str:
    if kind == KIND_EMBEDDING:
        return config.embedding.provider
    return config.memory_model.provider


def _build_installer(config: AppConfig) -> ModelInstaller:
    return ModelInstaller(
        ModelCache(config.model_install.cache_dir),
        config.model_install.download_policy,
        allow_network=config.model_install.allow_network,
        pinned_revision=config.model_install.pinned_revision,
    )


def _profile_for(config: AppConfig, spec: ModelSpec) -> ModelRuntimeProfile:
    return ModelRuntimeProfile(
        weight_precision=config.model_install.weight_precision,
        output_precision=config.model_install.output_precision,
        vector_dtype=config.redis.vector_dtype,
        normalize=config.embedding.normalize,
        query_prompt_name=(
            config.embedding.query_prompt_name or spec.query_prompt_name
        ),
    )


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_model(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    installer = _build_installer(config)
    kinds = [args.kind] if args.kind else [KIND_EMBEDDING, KIND_MEMORY]

    for kind in kinds:
        provider = _provider_for(config, kind)
        try:
            spec = _resolve_spec(config, kind)
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
            profile = _profile_for(config, spec)
            _emit(installer.status(spec, provider=provider, profile=profile).to_dict())
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="another-brain-server")
    subparsers = parser.add_subparsers(dest="group", required=True)

    model = subparsers.add_parser("model", help="local model management (Step 03)")
    model.add_argument("command", choices=["plan", "pull", "status"])
    model.add_argument("--kind", choices=[KIND_EMBEDDING, KIND_MEMORY], default=None)

    args = parser.parse_args(argv)
    try:
        return _cmd_model(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
