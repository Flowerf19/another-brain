# Test Report — Per-process embedding memory (TASK-044)

- **Date:** 2026-08-04 · **Method:** product `ONNXEmbeddingProvider` in one
  fresh process; RSS/PSS from `/proc/self/smaps_rollup`; real pinned q4 files
  (revision `d59c919d…`) via symlinked profile + real marker.
- **Stack:** Python 3.14, onnxruntime 1.28.0 (CPUExecutionProvider),
  tokenizers 0.23.1, q4 Harrier 270M.
- **Runner:** `benchmarks/measure_embedding_memory.py` (raw JSON:
  `benchmarks/evidence/embedding-memory-2026-08-04.json`).

## Measurements

| Point | RSS MiB | PSS MiB |
|-------|--------:|--------:|
| interpreter baseline | 52.4 | 40.3 |
| provider constructed (NOT_LOADED) | 52.4 | 40.3 |
| loaded, first embed | 374.7 | 361.7 |
| warm embed | 375.1 | 362.1 |
| after `close()` | 354.9 | 342.7 |

## Readings

1. **Constructing the provider loads nothing** — 52.4 MiB both before and
   after; matches the TASK-046 "status never loads" contract.
2. **One session costs ~322 MiB RSS / ~321 MiB PSS** on the reference
   machine. This is the per-process budget to publish at the release gate
   (TASK-087), consistent with the GOAL-001 resource gate (steady RSS 412 MiB
   measured in the spike harness, which also holds the ONNX session plus
   eval scaffolding).
3. **`close()` releases only ~20 MiB** — onnxruntime keeps its allocation
   arena; real reclamation happens at process exit. Documented in the
   provider docstring; the MCP shutdown path still closes references so the
   session object graph is collectable.
4. **No hidden embedding daemon** — embeddings run in-process, one lazy
   session per MCP process with serialized first load; no background service,
   no shared memory, no extra process. The daemon option stays out of the MVP
   by design (memory above is per-process and bounded).
