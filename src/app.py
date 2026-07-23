"""Composition root. Wires config, model install, embedding provider, Redis
storage, search engine, MemoryService, and the FastMCP tool surface. No
business logic lives here — every collaborator is constructed from its existing
class and handed its dependencies.

`.env` is loaded before config so a bare `uv run python src/main.py serve` picks
up local REDIS_URL/identity without a dotenv dependency; real environment
variables always win over the file.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import redis.asyncio as aioredis

from audit.service import AuditService
from config import AppConfig
from errors import ConfigError
from memory.embeddings import EmbeddingProvider, LocalEmbeddingProvider
from memory.search import MemorySearchEngine
from memory.service import MemoryService
from models.cache import ModelCache
from models.installer import ModelInstaller
from models.policy import TRIGGER_STARTUP
from models.registry import KIND_EMBEDDING, ModelRegistry, ModelSpec
from models.runtime import ModelRuntimeProfile
from server.tools import register_tools
from storage.redis_index import RedisIndexManager
from storage.redis_keys import RedisKeyBuilder
from storage.redis_repository import RedisMemoryRepository
from memory.retention import RetentionPolicy

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - mcp is a core dependency
    FastMCP = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_SERVER_INSTRUCTIONS = (
    "Shared long-term memory for all agents on this brain. Search before "
    "answering (brain_search/brain_recent) — do not re-ask what memory knows. "
    "Store decisions, fixes, preferences, and open tasks as diary entries with "
    "brain_remember (topic slug + 1-2 sentence summary; scope=project with the "
    "project slug by default, scope=user for personal facts, scope=global for "
    "cross-project knowledge). After using a memory, close the loop: "
    "brain_reinforce if it proved correct, brain_forget if it proved wrong."
)


# --------------------------------------------------------------------- .env

def load_env_file(path: str | os.PathLike[str] = ".env") -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing vars.

    Deliberately tiny (no python-dotenv dependency): blank lines and `#`
    comments are skipped, surrounding quotes on the value are stripped, and a
    key already present in the environment is left untouched so a real export
    always beats the file.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# --------------------------------------------------------------- model helpers
# Shared by the `model` CLI (main.py) and the embedding provider build below.

def resolve_spec(config: AppConfig, kind: str) -> ModelSpec:
    registry = ModelRegistry()
    if kind == KIND_EMBEDDING:
        return registry.resolve(
            config.embedding.model_name, kind,
            configured_dim=config.embedding.dim,
        )
    return registry.resolve(config.memory_model.model_name, kind)


def provider_for(config: AppConfig, kind: str) -> str:
    if kind == KIND_EMBEDDING:
        return config.embedding.provider
    return config.memory_model.provider


def build_installer(config: AppConfig) -> ModelInstaller:
    return ModelInstaller(
        ModelCache(config.model_install.cache_dir),
        config.model_install.download_policy,
        allow_network=config.model_install.allow_network,
        pinned_revision=config.model_install.pinned_revision,
    )


def profile_for(config: AppConfig, spec: ModelSpec) -> ModelRuntimeProfile:
    return ModelRuntimeProfile(
        weight_precision=config.model_install.weight_precision,
        output_precision=config.model_install.output_precision,
        vector_dtype=config.redis.vector_dtype,
        normalize=config.embedding.normalize,
        query_prompt_name=(
            config.embedding.query_prompt_name or spec.query_prompt_name
        ),
    )


def build_embedder(config: AppConfig) -> EmbeddingProvider:
    """Resolve + locate the embedding model and wrap it in a provider. Only the
    local provider is implemented today; an external provider is a config error
    until its adapter lands."""
    if config.embedding.provider != "local":
        raise ConfigError(
            f"EMBEDDING_PROVIDER={config.embedding.provider!r} is not implemented "
            f"yet — only 'local' is supported. Set EMBEDDING_PROVIDER=local."
        )
    spec = resolve_spec(config, KIND_EMBEDDING)
    installer = build_installer(config)
    # MANUAL policy won't download on startup — it returns the cached dir or
    # raises with an actionable `model pull` hint if the model is missing.
    model_path = installer.ensure(spec, TRIGGER_STARTUP)
    return LocalEmbeddingProvider(
        model_path,
        model_name=spec.name,
        dim=config.embedding.dim,
        profile=profile_for(config, spec),
    )


# ------------------------------------------------------------------ assembly

async def build_service(
    config: AppConfig,
) -> tuple[MemoryService, aioredis.Redis]:
    """Construct the full service graph against a live Redis and run the
    startup index check (Step 04 §5.6). Returns the service plus the Redis
    client so the caller owns its lifetime (close it on shutdown)."""
    keys = RedisKeyBuilder(config.redis.key_prefix)
    redis = aioredis.from_url(config.redis.url)

    index = RedisIndexManager(
        redis, keys,
        embedding_dim=config.embedding.dim,
        embedding_model=config.embedding.model_name,
        vector_dtype=config.redis.vector_dtype,
        distance_metric=config.redis.distance_metric,
        index_mode=config.redis.index_mode,
    )
    await index.ensure()

    repo = RedisMemoryRepository(
        redis, keys,
        RetentionPolicy(config.ttl_by_importance),
        grace_seconds=config.forget_grace_seconds,
        embedding_dim=config.embedding.dim,
    )
    audit = AuditService(
        redis, keys,
        retention_days=config.audit_retention_days,
        tz_name=config.timeline_timezone,
    )
    engine = MemorySearchEngine(repo, config.search)
    service = MemoryService(
        repo, engine, build_embedder(config), config, index=index, audit=audit,
    )
    logger.info(
        "Service wired: brain_id=%s redis=%s index=%s dim=%d",
        config.brain_id, config.redis.url, keys.index_name, config.embedding.dim,
    )
    return service, redis


async def build_server(config: AppConfig) -> tuple[FastMCP, aioredis.Redis]:
    """Build the FastMCP server with the 7 brain_* tools attached, plus the
    Redis client to close on shutdown."""
    if FastMCP is None:  # pragma: no cover
        raise ConfigError("the 'mcp' package is required to run the server")
    service, redis = await build_service(config)
    server = FastMCP(
        "another-brain",
        instructions=_SERVER_INSTRUCTIONS,
        host=os.environ.get("MCP_HTTP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_HTTP_PORT", "8000")),
    )
    register_tools(server, service)
    return server, redis
