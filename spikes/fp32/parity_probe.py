"""Encode probes with the fp32 oracle and report paired cosine(q4, fp32).

Run inside the SPIKE environment (sentence-transformers + torch CPU):

    cd spikes/fp32
    uv run python parity_probe.py

Reads the q4 vectors produced by the root-env `q4_encode.py` and embeds the
same payloads with the pinned fp32 SentenceTransformers profile (its own
tokenizer artifacts, `normalize_embeddings=True`, query prompt prepended
manually so the payload bytes match the q4 side exactly).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

SPIKE = Path(__file__).resolve().parent
sys.path.insert(0, str(SPIKE))
from payloads import QUERY_PROMPT, document_payload, query_payload  # noqa: E402,F401

FP32_DIR = SPIKE / ".models" / "fp32"


def main() -> int:
    probes = json.loads((SPIKE / "probes.json").read_text(encoding="utf-8"))["items"]
    texts = [
        document_payload(p["topic"], p["summary"]) if p["kind"] == "document"
        else query_payload(p["query"])
        for p in probes
    ]
    model = SentenceTransformer(str(FP32_DIR), device="cpu")
    fp32 = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    assert fp32.dtype == np.float32 or fp32.dtype == np.float64

    q4 = np.load(SPIKE / "evidence" / "q4_probe.npy")
    assert q4.shape == fp32.shape, f"shape mismatch {q4.shape} vs {fp32.shape}"

    cosines = np.sum(q4 * fp32.astype(np.float32), axis=1)
    for probe, cos in zip(probes, cosines):
        label = probe.get("topic") or probe["query"][:40]
        print(f"  cos={cos:.6f}  [{probe['kind']}] {label}")
    print(
        f"paired cosine(q4, fp32): median={np.median(cosines):.6f}"
        f" min={cosines.min():.6f} (smoke check; gate thresholds apply to the"
        " embedding-quality-v1 corpus in TASK-002..004)"
    )
    out = SPIKE / "evidence" / "fp32_probe.npy"
    np.save(out, fp32.astype(np.float32))
    print(f"fp32: wrote {fp32.shape} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
