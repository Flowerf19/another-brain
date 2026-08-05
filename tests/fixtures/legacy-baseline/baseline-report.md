# Legacy oracle baseline (TASK-031) — 2026-08-04

Recorded from the pinned external oracle per Plan 07 GOAL-008:

- commit: `edc0e573a10bb8ea9148c9830cf19fe15f757972` (`main@edc0e57`),
  accessed via `git worktree add ../another-brain-main edc0e57…` — no
  Redis/Docker/Torch code entered `v0.11.0`;
- environment: Redis 8.8 (`redis:8.8` docker image, host port 1906),
  legacy venv (`uv sync --extra local`): torch 2.13.0+cpu,
  sentence-transformers 5.6.0, redis-py 8.0.1, Python 3.14.6;
- baseline suite: `REDIS_TEST_URL=redis://localhost:1906 uv run pytest`
  - `tests/unit`: **190 passed**
  - `tests/integration`: **11 passed, 3 skipped** (model-download tests skip
    without network-pulled artifacts);
- behavior fixtures: 12 deterministic fake-vector scenarios exported by
  `export_legacy_fixtures.py` (kept in the worktree, not committed) into
  `tests/fixtures/legacy-baseline/behavior-v1.json`; structure locked by
  `tests/unit/test_legacy_baseline_fixture.py`.

Notable oracle facts the clean branch preserves: TTL table
365/180/90/30/7 days; reinforce re-arms the full importance TTL; forget
clamps to the grace window without extending; restore re-arms; audit is
secret-free and survives hard-delete; previews never carry content; health
reports embedding state without forcing a load.

Recorded intentional differences (detailed in TASK-008): the legacy
universal cosine gate drops content-only lexical matches
(`content_match_returned: false`); legacy `recent` ties have no stable
`memory_id` tie-break; legacy embeds summary-only while the clean branch
embeds topic+summary (input version 2).
