# Test Report — Permanent q4 embedding gate (TASK-019)

- **Date:** 2026-08-04 · **Verdict: PASS**
- **Artifact:** `tests/integration/test_q4_embedding_gate.py` (marked `slow`;
  skips when the pinned profile is not installed).
- **Run:** `BRAIN_MODEL_CACHE_DIR=… uv run pytest tests/integration/test_q4_embedding_gate.py -m slow`
  against the real pinned q4 profile (revision `d59c919d…`, onnxruntime
  1.28.0, tokenizers 0.23.1, Python 3.14). Duration 22.8 s (600 docs + 120
  queries, single-item encodes).

## Measured vs locked thresholds

| Metric | Measured | Locked (rev 2026-08-04) | Verdict |
|--------|---------:|------------------------:|---------|
| macro Recall@5 | 0.9317 | ≥ 0.90 | PASS |
| macro MRR | 0.9431 | ≥ 0.80 | PASS |
| macro nDCG@10 | 0.8380 | ≥ 0.83 | PASS |

The measured values reproduce the GOAL-001 gate manifest
(`q4gate-20260804T075759Z`) **exactly** (0.9317 / 0.9431 / 0.8380) — the
product provider, corpus, and metric formulas are byte-consistent with the
spike evidence pipeline.

## Scope notes

- The paired fp32 cosine thresholds stay evaluation-only: Torch lives in
  `spikes/fp32/` and is absent from the wheel and the final lockfile; only
  the q4 profile is asserted permanently.
- The 24-case behavior partition is enforced at the GOAL-012 retrieval gate
  (TASK-062), not here (approved revision 2026-08-04).
