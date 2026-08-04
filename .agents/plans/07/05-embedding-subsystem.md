---
status: draft
created: 2026-08-04
last_updated: 2026-08-04
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-005, GOAL-010
---

# Sub-plan 07.05 — Embedding subsystem (GOAL-005 + GOAL-010)

## Summary

Implement the locked embedding subsystem: immutable model manifest, verified
cross-process-safe installer, raw ONNX Runtime CPU provider, versioned
payload/prompt builder, and tokenizer-based budget validation. Requires the
package shell (07.02) and deletion (07.03). Module targets: `embedding/
manifest.py`, `installer.py`, `provider.py`, `payload.py`, `budgets.py`.

The pinned repository/revision, five filenames/SHA-256, `QUERY_PROMPT` and its
hash, dimension 640, normalization, and input version 2 are locked in the
master plan's "Locked product decisions" 5–9 and are copied verbatim into the
manifest module — nowhere else.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-042 | Encode the locked repository `onnx-community/harrier-oss-v1-270m-ONNX` @ `d59c919d...f9c`, five files/hashes, prompt/hash, dims, normalization, input version in one immutable manifest consumed by installer/provider/schema. | ✅ | 2026-08-04 |
| TASK-018 | Download exactly the five pinned files: temp files, resume/progress on stderr, SHA-256 before rename, atomic publish, per-OS cache (platformdirs), one cross-process lock per manifest. | ✅ | 2026-08-04 |
| TASK-043 | Idempotent crash-safe install: stale temp cleanup, no partially installed profile visible to another process; concurrent installers converge. | ✅ | 2026-08-04 |
| TASK-017 | Raw ONNX Runtime CPU provider: direct `sentence_embedding`, FLOAT32 `[batch,640]` finite/unit-norm validation, query-only prompt, lazy load, thread-safe single initialization, health/load-error state. | | |
| TASK-044 | One lazy session per MCP process; serialize first load; close on shutdown; record measured per-process memory for the release metric (no hidden daemon). | | |
| TASK-027 | Versioned payload builder: document = `topic.replace("-"," ") + "\n" + summary.strip()`; query = `QUERY_PROMPT + query.strip()`; reject empty stripped query. Profile/input-version validation blocks mixed search until re-embedding completes. | | |
| TASK-029 | One tokenizer budget validator: topic ≤12 (no specials), document ≤256 (with specials), prompted query ≤128 (with specials), content ≤1024 (no specials). Reject limit+1 with actual/allowed counts; delete `CONTENT_MAX_CHARS`; never truncate/chunk. | | |
| TASK-028 | Update `brain_remember` description, MCP instructions, schema docs, tests for stable reusable topics (target 3–8, hard max 12 tokens; no catalog duplication/workflow labels/keyword stuffing). | | |
| TASK-019 | Turn GOAL-001 q4 assertions into permanent slow tests; Torch/ST stay evaluation-only, absent from wheel and final lockfile. | | |
| TASK-045 | Unit-test boundaries: token counts at every limit ±1, VI/EN input, query/document asymmetry, output norm, corrupt/missing external data, hash mismatch, interrupted download, concurrent installers. | | |
| TASK-046 | Expose profile/load state via health and `model status` without loading the model to answer status. | ⏳ (status wired; load state pending provider) | 2026-08-04 |

## Test Plan

- Unit: TASK-045 matrix plus payload byte-exactness against pinned vectors.
- Integration: fresh cache install, interrupted-download recovery, two
  concurrent installers, provider parity vs recorded q4 outputs.
- Slow (gated): pinned-artifact q4 quality tests from sub-plan 07.04 corpus.
- No network in tests except explicitly marked installer-download tests.

## Assumptions

- Hugging Face hub is reached via plain HTTPS download of pinned files; no
  `huggingface_hub` runtime dependency is added unless file-lock/resume needs
  force it (record the decision if so).
- `tokenizers` loads `tokenizer.json` directly; no transformers dependency.
