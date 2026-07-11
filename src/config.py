"""Validated runtime config: AppConfig, RedisConfig, MemoryModelConfig,
EmbeddingConfig, SearchConfig.

Config values: Step 04 section 7 plus the Step 03 provider/model settings.
All values load from environment variables via AppConfig.from_env; every
inconsistency raises ConfigError at startup, never later.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from zoneinfo import ZoneInfo

from errors import ConfigError
from memory.retention import DEFAULT_TTL_BY_IMPORTANCE

PROVIDERS = frozenset({"openai_compat", "ollama", "gemini", "local"})
VECTOR_INDEX_MODES = frozenset({"HNSW", "FLAT"})

_TTL_ENV_KEYS = {imp: f"TTL_IMPORTANCE_{imp}" for imp in (1, 2, 3, 4, 5)}


def _str(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key, "").strip()
    return value if value else default


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from None


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from None


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    raise ConfigError(f"{key} must be a boolean (true/false), got {raw!r}")


def _identity(env: Mapping[str, str], key: str, default: str) -> str:
    value = _str(env, key, default)
    if ":" in value:
        raise ConfigError(f"{key} must not contain ':', got {value!r}")
    return value


def _positive(name: str, value: int) -> int:
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value


@dataclass(frozen=True)
class RedisConfig:
    url: str
    key_prefix: str
    index_name: str
    vector_dtype: str
    distance_metric: str
    index_mode: str
    hnsw_m: int


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model_name: str
    dim: int
    normalize: bool


@dataclass(frozen=True)
class MemoryModelConfig:
    provider: str
    model_name: str


@dataclass(frozen=True)
class SearchConfig:
    top_k: int
    fusion_k: int
    min_cosine: float


@dataclass(frozen=True)
class AppConfig:
    brain_id: str
    agent_id: str
    redis: RedisConfig
    embedding: EmbeddingConfig
    memory_model: MemoryModelConfig
    search: SearchConfig
    ttl_by_importance: dict[int, int]
    audit_retention_days: int
    content_max_chars: int
    forget_grace_seconds: int
    timeline_timezone: str
    schema_version: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AppConfig":
        env = dict(os.environ) if env is None else env

        brain_id = _identity(env, "BRAIN_ID", "default")
        agent_id = _identity(env, "AGENT_ID", "default")

        key_prefix = _identity(env, "REDIS_KEY_PREFIX", "ab")
        vector_dtype = _str(env, "REDIS_VECTOR_DTYPE", "FLOAT32")
        if vector_dtype != "FLOAT32":
            raise ConfigError(
                f"REDIS_VECTOR_DTYPE must be FLOAT32 in MVP (Step 03 decision 9); "
                f"changing dtype is an index migration. Got {vector_dtype!r}"
            )
        distance_metric = _str(env, "REDIS_DISTANCE_METRIC", "COSINE")
        if distance_metric != "COSINE":
            raise ConfigError(
                f"REDIS_DISTANCE_METRIC must be COSINE (Step 04 contract); "
                f"changing it is an index migration. Got {distance_metric!r}"
            )
        index_mode = _str(env, "REDIS_VECTOR_INDEX_MODE", "HNSW").upper()
        if index_mode not in VECTOR_INDEX_MODES:
            raise ConfigError(
                f"REDIS_VECTOR_INDEX_MODE must be one of {sorted(VECTOR_INDEX_MODES)}, "
                f"got {index_mode!r}"
            )
        redis = RedisConfig(
            url=_str(env, "REDIS_URL", "redis://localhost:6379"),
            key_prefix=key_prefix,
            index_name=_str(env, "REDIS_INDEX_NAME", f"{key_prefix}:idx:memory"),
            vector_dtype=vector_dtype,
            distance_metric=distance_metric,
            index_mode=index_mode,
            hnsw_m=_positive("REDIS_HNSW_M", _int(env, "REDIS_HNSW_M", 6)),
        )

        embedding_provider = _str(env, "EMBEDDING_PROVIDER", "local")
        if embedding_provider not in PROVIDERS:
            raise ConfigError(
                f"EMBEDDING_PROVIDER must be one of {sorted(PROVIDERS)}, "
                f"got {embedding_provider!r}"
            )
        embedding = EmbeddingConfig(
            provider=embedding_provider,
            model_name=_str(env, "EMBEDDING_MODEL", "microsoft/harrier-oss-v1-270m"),
            dim=_positive("EMBEDDING_DIM", _int(env, "EMBEDDING_DIM", 640)),
            normalize=_bool(env, "NORMALIZE_EMBEDDINGS", True),
        )

        memory_model_provider = _str(env, "MEMORY_MODEL_PROVIDER", "local")
        if memory_model_provider not in PROVIDERS:
            raise ConfigError(
                f"MEMORY_MODEL_PROVIDER must be one of {sorted(PROVIDERS)}, "
                f"got {memory_model_provider!r}"
            )
        memory_model = MemoryModelConfig(
            provider=memory_model_provider,
            model_name=env.get("MEMORY_MODEL_NAME", "").strip(),
        )

        min_cosine = _float(env, "SEARCH_MIN_COSINE", 0.30)
        if not 0.0 <= min_cosine <= 1.0:
            raise ConfigError(f"SEARCH_MIN_COSINE must be in [0, 1], got {min_cosine}")
        search = SearchConfig(
            top_k=_positive("SEARCH_TOP_K", _int(env, "SEARCH_TOP_K", 20)),
            fusion_k=_positive("SEARCH_FUSION_K", _int(env, "SEARCH_FUSION_K", 60)),
            min_cosine=min_cosine,
        )

        # TTL overrides are all-or-none: a partial override silently mixing
        # defaults with custom values is almost always a deployment mistake.
        set_keys = [k for k in _TTL_ENV_KEYS.values() if env.get(k, "").strip()]
        if set_keys and len(set_keys) != len(_TTL_ENV_KEYS):
            missing = sorted(set(_TTL_ENV_KEYS.values()) - set(set_keys))
            raise ConfigError(
                f"TTL overrides are all-or-none: {sorted(set_keys)} set but "
                f"{missing} missing"
            )
        if set_keys:
            ttl_by_importance = {
                imp: _positive(key, _int(env, key, 0))
                for imp, key in _TTL_ENV_KEYS.items()
            }
        else:
            ttl_by_importance = dict(DEFAULT_TTL_BY_IMPORTANCE)

        timeline_timezone = _str(env, "TIMELINE_TIMEZONE", "Asia/Ho_Chi_Minh")
        try:
            ZoneInfo(timeline_timezone)
        except Exception:
            raise ConfigError(
                f"TIMELINE_TIMEZONE is not a valid IANA timezone: {timeline_timezone!r}"
            ) from None

        schema_version = _int(env, "SCHEMA_VERSION", 1)
        if schema_version < 1:
            raise ConfigError(f"SCHEMA_VERSION must be >= 1, got {schema_version}")

        return cls(
            brain_id=brain_id,
            agent_id=agent_id,
            redis=redis,
            embedding=embedding,
            memory_model=memory_model,
            search=search,
            ttl_by_importance=ttl_by_importance,
            audit_retention_days=_positive(
                "AUDIT_RETENTION_DAYS", _int(env, "AUDIT_RETENTION_DAYS", 90)
            ),
            content_max_chars=_positive(
                "CONTENT_MAX_CHARS", _int(env, "CONTENT_MAX_CHARS", 4000)
            ),
            forget_grace_seconds=_positive(
                "FORGET_GRACE_SECONDS", _int(env, "FORGET_GRACE_SECONDS", 2_592_000)
            ),
            timeline_timezone=timeline_timezone,
            schema_version=schema_version,
        )
