"""Encode corpus items with the fp32 oracle (spike env).

    cd spikes/fp32 && uv run python fp32_encode.py

Output: evidence/fp32_corpus.npy (row order = evidence/corpus_items.json)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

SPIKE = Path(__file__).resolve().parent
sys.path.insert(0, str(SPIKE))
from payloads import document_payload, query_payload  # noqa: E402


def main() -> int:
    items = json.loads((SPIKE / "evidence" / "corpus_items.json").read_text(encoding="utf-8"))["items"]
    texts = [
        document_payload(p["topic"], p["summary"]) if p["kind"] == "document"
        else query_payload(p["query"])
        for p in items
    ]
    model = SentenceTransformer(str(SPIKE / ".models" / "fp32"), device="cpu")
    vectors = model.encode(
        texts, normalize_embeddings=True, convert_to_numpy=True,
        batch_size=32, show_progress_bar=True,
    ).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    assert np.all(norms > 0.99), f"fp32 norms far from 1: {norms.min()}"
    vectors = vectors / norms  # exact unit norm; ST normalize leaves up to ~4e-3 drift
    out = SPIKE / "evidence" / "fp32_corpus.npy"
    np.save(out, vectors.astype(np.float32))
    print(f"fp32: wrote {vectors.shape} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
