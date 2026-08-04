"""TASK-019: permanent q4 embedding gate (GOAL-001 assertions, slow).

Runs the judged corpus through the PRODUCT provider (raw ONNX q4, no Torch)
and enforces the locked q4-only thresholds from the Q4 gate revision
2026-08-04: macro Recall@5 >= 0.90, MRR >= 0.80, nDCG@10 >= 0.83.

The paired fp32 cosine thresholds stay evaluation-only (Torch lives in the
spike environment, absent from the wheel and the final lockfile); only the
q4 profile can be asserted permanently.

Skips when the pinned q4 profile is not installed — run
`another-brain model pull` first. Marked ``slow``; CI runs the fast suite.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from another_brain.config import AppConfig
from another_brain.services.embedding.provider import ONNXEmbeddingProvider
from another_brain.model_installer import is_installed, profile_dir

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "spikes" / "fp32" / "corpus" / "embedding-quality-v1.json"
N_SEMANTIC_DOCS = 600
TOP_K_RECALL = 5
NDCG_K = 10

# Locked q4-only thresholds (Plan 07 Q4 gate, revision 2026-08-04).
THRESHOLD_RECALL_AT_5 = 0.90
THRESHOLD_MRR = 0.80
THRESHOLD_NDCG_AT_10 = 0.83


def _skip_if_model_missing() -> AppConfig:
    config = AppConfig.from_env()
    if not is_installed(config.model_cache_dir, verify_files=False):
        pytest.skip("the pinned q4 profile is not installed; run `another-brain model pull`")
    return config


def _metrics(queries: list[dict], scores: np.ndarray, doc_ids: list[str]) -> tuple[float, float, float]:
    """Macro Recall@5, MRR, nDCG@10 — same formulas as the spike runner."""
    recalls, mrrs, ndcgs = [], [], []
    for qi, query in enumerate(queries):
        judgments = {j["doc_id"]: j["grade"] for j in query["judgments"]}
        relevant = {d for d, g in judgments.items() if g >= 1}
        order = np.argsort(-scores[qi], kind="stable")
        ranked_ids = [doc_ids[i] for i in order]
        top5 = set(ranked_ids[:TOP_K_RECALL])
        recalls.append(len(top5 & relevant) / min(TOP_K_RECALL, len(relevant)))
        first = next((rank for rank, did in enumerate(ranked_ids, 1) if did in relevant), None)
        mrrs.append(1.0 / first if first else 0.0)
        dcg = sum(
            judgments.get(did, 0) / np.log2(rank + 1)
            for rank, did in enumerate(ranked_ids[:NDCG_K], 1)
        )
        ideal = sorted(judgments.values(), reverse=True)[:NDCG_K]
        idcg = sum(g / np.log2(i + 1) for i, g in enumerate(ideal, 1))
        ndcgs.append(dcg / idcg if idcg else 0.0)
    return float(np.mean(recalls)), float(np.mean(mrrs)), float(np.mean(ndcgs))


def test_q4_gate_quality_metrics():
    config = _skip_if_model_missing()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    docs = corpus["documents"][:N_SEMANTIC_DOCS]
    queries = corpus["queries"]
    doc_ids = [d["doc_id"] for d in docs]

    provider = ONNXEmbeddingProvider(profile_dir(config.model_cache_dir))
    doc_vectors = np.stack(
        [provider.embed_document(topic=d["topic"], summary=d["summary"]).values for d in docs]
    )
    query_vectors = np.stack(
        [provider.embed_query(q["text"]).values for q in queries]
    )
    scores = query_vectors @ doc_vectors.T

    recall, mrr, ndcg = _metrics(queries, scores, doc_ids)
    print(f"\nq4 gate: Recall@5={recall:.4f} MRR={mrr:.4f} nDCG@10={ndcg:.4f}")
    assert recall >= THRESHOLD_RECALL_AT_5, (
        f"q4 Recall@5 {recall:.4f} < {THRESHOLD_RECALL_AT_5}"
    )
    assert mrr >= THRESHOLD_MRR, f"q4 MRR {mrr:.4f} < {THRESHOLD_MRR}"
    assert ndcg >= THRESHOLD_NDCG_AT_10, (
        f"q4 nDCG@10 {ndcg:.4f} < {THRESHOLD_NDCG_AT_10}"
    )
