# Testing Guide

## Commands

Run from the repo root; tests use the uv-managed `.venv` (Python 3.14).

```bash
uv run pytest              # full suite (unit + integration)
uv run pytest tests/unit   # unit only, no Redis needed
```

- `pythonpath = ["src"]` and `asyncio_mode = "auto"` are set in
  `pyproject.toml`, so async tests need no marker and imports use the `src`
  layout (`from memory.service import ...`).

## Redis for integration tests

Integration tests (`tests/integration/`) need a Redis 8.4+ with the RediSearch
module that ships `FT.HYBRID` (native hybrid search). The dev instance is the
compose service `another-brain-redis` (image `redis:8.8`).

- Host port is `REDIS_PORT` from `.env` (**1905** here, to avoid a clash with a
  neighbouring redis-stack on 6379). `tests/conftest.py` reads that port and
  points `REDIS_TEST_URL` at it automatically, so a bare `uv run pytest` targets
  the right server.
- Override explicitly with `REDIS_TEST_URL=redis://host:port uv run pytest`
  (e.g. in CI).
- If the reachable Redis has a RediSearch older than 8.4 (redis-stack 7.2 loads
  the `search` module at ver `20828` but lacks `FT.HYBRID`), the integration
  fixture **skips cleanly** rather than failing — the module version, not just
  its presence, is the gate.

Bring the dev Redis up with:

```bash
docker compose -f docker/docker-compose.yml up -d
```

## Coverage Shape

Test coverage follows architecture risk:

- **Unit** — model validation, identity/scope pinning, embedding dimension
  checks, query-filter construction, the cosine gate (incl. NaN-safe gating and
  BM25-only docs), and MCP tool registration (names, required/optional params).
- **Integration** (real Redis) — index idempotency + meta, dim-mismatch refusal,
  store/get roundtrip + importance TTL, KNN filters + brain isolation, FT.HYBRID
  (text + vector branch, brain isolation, soft-delete exclusion), soft
  delete/restore, reinforce TTL re-arm, recent ordering, hard delete.

## Before Merge

Run `uv run pytest` and confirm the integration tests **ran** (not skipped)
against a Redis 8.4+ — a green suite with silently-skipped integration tests
does not prove the storage contract.
