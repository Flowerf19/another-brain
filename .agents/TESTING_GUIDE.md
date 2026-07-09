# Testing Guide

## Current Status

No implementation or test suite exists yet.

There is currently no verified command for:

- unit tests;
- integration tests;
- type checking;
- linting;
- Docker smoke tests.

Do not claim a command is supported until the corresponding manifest or script
exists in the repo.

## Expected Test Shape

When implementation starts, test coverage should follow the architecture risk:

- **Unit tests** for model validation, identity normalization, language
  normalization, embedding dimension checks, and query filter construction.
- **Search tests** for Redis `FT.SEARCH` vector KNN, Redis `FT.SEARCH` BM25,
  vector/BM25 fusion, time filters, widening behavior, and relevance-floor
  filtering.
- **Storage tests** for Redis HASH shape, packed FLOAT32 embedding storage,
  key format, index creation, soft delete, merge behavior, importance-derived
  TTL/retention, and migration/reindex failure modes.
- **MCP tests** for tool schemas, required/optional parameters, server-filled
  `brain_id`/`agent_id`, and secret-free health output.
- **Integration tests** with Redis Stack for index and query behavior that mocks
  cannot prove.
- **Packaging tests** for Docker Compose startup and npm launcher/proxy behavior
  once those files exist.

## Minimum Verification Before Merge

Until concrete commands exist, every implementation change should at least state
which verification was possible and which commands are still missing.

Once commands are added, record them here with exact invocations.
